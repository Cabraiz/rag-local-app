from google.adk.agents import BaseAgent
from google.adk.events import Event, EventActions
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from .domain import Citation, Proposal
from .assistant_policy import POLICY_VERSION, lab_request_guard
from dataclasses import asdict
import asyncio
import json
import time
from opentelemetry import trace
from .safe_logging import setup as setup_sdk_logging

setup_sdk_logging()


class AbstentionAgent(BaseAgent):
    async def _run_async_impl(self, ctx):
        yield Event(author=self.name, actions=EventActions(state_delta={'proposal':{
            'kind':'ABSTAIN','text':'Não há corpus aprovado nesta primeira fatia. Não posso fundamentar uma resposta.'}}))


class AdkAbstentionWorkflow:
    async def run(self, request):
        guarded = lab_request_guard(request['question'])
        if guarded is not None:
            return guarded
        # Private per-attempt session, never exported to the frontend.
        sessions = InMemorySessionService()
        user_id = request['tenant']+':'+request['actor']
        session_id = str(request['id'])+':'+str(request['fence'])
        await sessions.create_session(app_name='rag_lab',user_id=user_id,session_id=session_id)
        runner = Runner(app_name='rag_lab',agent=AbstentionAgent(name='evidence_gate'),session_service=sessions)
        proposal = None
        async for event in runner.run_async(user_id=user_id,session_id=session_id,
                new_message=types.Content(role='user',parts=[types.Part(text=request['question'])])):
            value = event.actions.state_delta.get('proposal')
            if value: proposal = Proposal(**value)
        if proposal is None: raise RuntimeError('ADK_NO_PROPOSAL')
        return proposal


class AdkRetrievalWorkflow:
    async def run(self, request):
        guarded = lab_request_guard(request['question'])
        if guarded is not None:
            # No discovery, retrieval, cloud call or write on unsupported paths.
            with trace.get_tracer('rag_app.workflow').start_as_current_span('rag.policy_guard',
                    record_exception=False, set_status_on_exception=False):
                print(json.dumps(dict(event='assistant_policy_guard',
                    request_id=str(request['id']), fence=request['fence'],
                    version=POLICY_VERSION, outcome='ABSTAIN')), flush=True)
            return guarded
        from google.adk import Workflow
        from google.adk.workflow import START
        from . import corpus
        # Internal ADK spans may be filtered at the export privacy boundary.
        # Application stages attach to the explicit application root, not a dropped SDK parent.
        application_context=trace.set_span_in_context(trace.get_current_span())

        async def stage(name, action):
            started=time.monotonic(); ok=False; error_type=None
            with trace.get_tracer('rag_app.workflow').start_as_current_span('rag.'+name,context=application_context,
                    record_exception=False,set_status_on_exception=False) as stage_span:
                try:
                    value=await action(); ok=True
                    stage_span.set_status(trace.Status(trace.StatusCode.OK))
                    return value
                except Exception as error:
                    error_type=type(error).__name__
                    stage_span.set_status(trace.Status(trace.StatusCode.ERROR))
                    raise
                finally:
                    print(json.dumps(dict(event='workflow_stage',stage=name,request_id=str(request['id']),
                        fence=request['fence'],ok=ok,error_type=error_type,duration_ms=round((time.monotonic()-started)*1000))),flush=True)

        async def retrieve_evidence(node_input):
            return await stage('retrieve',lambda:asyncio.to_thread(corpus.retrieve,request))

        async def compose_response(node_input):
            from . import gemini_grounded
            if gemini_grounded.enabled():
                async def grounded():
                    return asdict(await gemini_grounded.select(request,node_input))
                return await stage('gemini_grounding',grounded)
            return await stage('compose',lambda:asyncio.to_thread(lambda:asdict(corpus.compose(node_input))))

        def checked(node_input):
            # Structural gate; canonical SQL authorization is repeated at commit.
            if node_input['kind'] not in ('ABSTAIN','EXTRACTIVE'): raise RuntimeError('INVALID_GRAPH_PROPOSAL')
            if node_input['kind']=='EXTRACTIVE':
                cites=node_input['citations']
                if len(cites)!=1 or node_input['text']!='Trecho da fonte:\n'+cites[0]['quote']:
                    raise RuntimeError('INVALID_GRAPH_CITATION')
            return {'proposal':node_input}

        async def verify_evidence(node_input):
            return await stage('verify',lambda:asyncio.to_thread(checked,node_input))

        graph=Workflow(name='rag_retrieval',edges=[(START,retrieve_evidence,compose_response,verify_evidence)],max_concurrency=2)
        sessions=InMemorySessionService()
        user_id=request['tenant']+':'+request['actor']
        session_id=str(request['id'])+':'+str(request['fence'])
        await sessions.create_session(app_name='rag_lab',user_id=user_id,session_id=session_id)
        runner=Runner(app_name='rag_lab',agent=graph,session_service=sessions)
        proposal=None
        async for event in runner.run_async(user_id=user_id,session_id=session_id,
                new_message=types.Content(role='user',parts=[types.Part(text=request['question'])])):
            if isinstance(event.output,dict) and 'proposal' in event.output:
                value=event.output['proposal']
                proposal=Proposal(value['kind'],value['text'],tuple(Citation(**c) for c in value['citations']),value.get('model'))
        if proposal is None: raise RuntimeError('ADK_NO_PROPOSAL')
        return proposal
