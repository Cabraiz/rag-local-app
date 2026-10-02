"""Explicit fixed Free Gemini model for optional SDKs; shared durable daily budget."""
import asyncio
from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
import random
import threading
import time
from uuid import uuid4

from google import genai
from google.genai.errors import APIError
from google.genai import types
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import PrivateAttr
from rag_app.gemini_lab import MODEL, configuration, reserve_attempt

logging.disable(logging.CRITICAL)
PACE_LOCK=threading.Lock()
LAST_CALL=0.0


class LabBlocked(RuntimeError):pass


@dataclass
class Budget:
    max_calls:int=4
    max_reads:int=4
    calls:int=0
    reads:int=0
    deadline:float=field(default_factory=lambda:time.monotonic()+60)
    cancelled:threading.Event=field(default_factory=threading.Event)
    lock:threading.Lock=field(default_factory=threading.Lock)
    def check(self):
        if self.cancelled.is_set():raise LabBlocked('TASK_CANCELLED')
        if time.monotonic()>self.deadline:raise LabBlocked('TASK_DEADLINE')
    def reserve(self):
        with self.lock:
            self.check()
            if self.calls>=self.max_calls:raise LabBlocked('TASK_MODEL_BUDGET')
            key,path=configuration()
            reserve_attempt(path)
            self.calls+=1
            self.check()
            return key.read_text().strip()
    def read(self):
        with self.lock:
            self.check()
            if self.reads>=self.max_reads:raise LabBlocked('TASK_TOOL_BUDGET')
            self.reads+=1


ALLOWED={'lookup_evidence','write_todos'}


def invoke(budget,contents,*,system='',tools=None,schema=None):
    global LAST_CALL
    budget.check()
    if len(json.dumps([system,contents],default=str).encode())>60000:
        raise LabBlocked('TASK_CONTEXT_BYTES')
    # Gemini 3.x guidance recommends its default temperature (1). A lower value
    # is not a determinism guarantee and can degrade reasoning/structured output.
    # Output authorization/schema checks and finite budgets remain mandatory.
    options=dict(temperature=1.0,max_output_tokens=512,
        thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.MINIMAL,include_thoughts=False))
    if system:options['system_instruction']=system
    if tools:options['tools']=tools
    if schema:
        options.update(response_mime_type='application/json',response_schema=schema)
    # Only the side-effect-free structured judge can retry an explicit transient
    # HTTP response. Tool proposals, unknown transport outcomes, invalid outputs
    # and authorization errors are never replayed. Every physical attempt is
    # reserved in BOTH the per-case and durable daily budget, before networking.
    attempts=2 if schema is not None and not tools else 1
    for attempt in range(attempts):
        with PACE_LOCK:
            budget.check()
            delay=8-(time.monotonic()-LAST_CALL)
            if delay>0:
                budget.cancelled.wait(min(delay,max(0,budget.deadline-time.monotonic())))
            budget.check()
            key=budget.reserve()
            LAST_CALL=time.monotonic()
        budget.check()
        client=genai.Client(api_key=key,vertexai=False,http_options=types.HttpOptions(
            base_url='https://generativelanguage.googleapis.com',api_version='v1beta',timeout=15000,
            retry_options=types.HttpRetryOptions(attempts=1)))
        try:
            result=client.models.generate_content(model=MODEL,contents=contents,config=types.GenerateContentConfig(**options))
            budget.check()
            if not result.candidates or not result.candidates[0].content:
                raise LabBlocked('MODEL_EMPTY_RESPONSE')
            return result
        except LabBlocked:raise
        except Exception as error:
            budget.check()
            if attempt+1>=attempts or not isinstance(error,APIError) or error.code not in (429,500,503,504):
                raise LabBlocked('FREE_MODEL_UNAVAILABLE') from None
            budget.cancelled.wait(min(random.SystemRandom().uniform(1,2),
                                      max(0,budget.deadline-time.monotonic())))
            budget.check()
        finally:
            client.close()
            key=''


class FreeChatModel(BaseChatModel):
    # DeepAgents resolves pre-built custom models by a qualified identifier.
    # This does not change the endpoint or actual model used by invoke().
    model_name:str='google_genai:'+MODEL
    _budget:Budget=PrivateAttr()
    def __init__(self,budget,**kwargs):
        super().__init__(**kwargs);self._budget=budget
    @property
    def _llm_type(self):return 'google_genai'
    def bind_tools(self,tools,**kwargs):
        converted=[convert_to_openai_tool(t)['function'] for t in tools]
        if any(t['name'] not in ALLOWED for t in converted):
            raise LabBlocked('TASK_TOOL_SURFACE_NOT_AUTHORIZED')
        return self.bind(provider_tools=converted)
    def _generate(self,messages,stop=None,run_manager=None,**kwargs):
        contents=[];systems=[];known={}
        for m in messages:
            if m.type=='system':systems.append(str(m.content));continue
            if m.type=='ai':
                for call in m.tool_calls:known[call['id']]=call['name']
                saved=m.additional_kwargs.get('gemini_parts')
                parts=[types.Part.model_validate_json(json.dumps(p)) for p in saved] if saved else [types.Part(text=str(m.content))]
                contents.append(types.Content(role='model',parts=parts))
            elif m.type=='tool':
                name=known.get(m.tool_call_id)
                if name not in ALLOWED:raise LabBlocked('UNKNOWN_TOOL_RESPONSE')
                contents.append(types.Content(role='user',parts=[types.Part(function_response=types.FunctionResponse(
                    name=name,id=m.tool_call_id,response={'result':str(m.content)}))]))
            elif m.type=='human':contents.append(types.Content(role='user',parts=[types.Part(text=str(m.content))]))
            else:raise LabBlocked('UNSUPPORTED_MESSAGE')
        declarations=[types.FunctionDeclaration(name=t['name'],description=t.get('description',''),parameters_json_schema=t.get('parameters')) for t in kwargs.get('provider_tools',[])]
        response=invoke(self._budget,contents,system='\n'.join(systems),tools=[types.Tool(function_declarations=declarations)] if declarations else None)
        parts=response.candidates[0].content.parts or []
        text=''.join(p.text or '' for p in parts if not p.thought)
        calls=[]
        for p in parts:
            if p.function_call:
                call=p.function_call
                if call.name not in ALLOWED:raise LabBlocked('MODEL_TOOL_NOT_AUTHORIZED')
                calls.append(dict(name=call.name,args=dict(call.args or {}),id=call.id or str(uuid4()),type='tool_call'))
        if len(calls)>4:raise LabBlocked('MODEL_TOOL_CALL_LIMIT')
        message=AIMessage(content=text,tool_calls=calls,additional_kwargs={'gemini_parts':[p.model_dump(mode='json',exclude_none=True) for p in parts]})
        return ChatResult(generations=[ChatGeneration(message=message)])
    async def _agenerate(self,messages,stop=None,run_manager=None,**kwargs):
        return await asyncio.to_thread(self._generate,messages,stop,run_manager,**kwargs)
