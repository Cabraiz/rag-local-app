"""What of the person's message reaches the model: the image only by a token (a file name can carry a
patient's name), the message as the one `cli run` sends, and only text and tool calls in each part.
"""
from pathlib import Path
from typing import TYPE_CHECKING, Any

from google.adk.models.llm_request import LlmRequest
from google.genai import types

if TYPE_CHECKING:  # the record imports image_token from here
    from .pedido import OrderRecord

IMAGE_SUFFIXES = ('.png', '.jpg', '.jpeg')  # the OCR server's own
# A part of what the model is sent may carry only these: text, and the tool calls and their replies.
PART_FIELDS = ('text', 'thought', 'thought_signature', 'function_call', 'function_response')
CONFIRMATION = 'adk_request_confirmation'  # the client's answer to the question, when a call resumes


def image_token(name: str) -> str:
    """What the model calls the order's image: never the file name, which can carry a patient's name."""
    return f'pedido-1{Path(name).suffix.lower()}'


def image_names(text: str) -> list[str]:
    """The image file names a message names: words ending in .png, .jpg or .jpeg (any case), quotes
    and punctuation around them left out. A name with a folder is kept whole, to be refused."""
    around = '\'"`“”‘’()[]{}<>,;:!?'
    names = [word.lstrip(around).rstrip(around + '.') for word in str(text or '').split()]
    return list(dict.fromkeys(name for name in names if name.lower().endswith(IMAGE_SUFFIXES)))


def texts_of(content: types.Content | None) -> list[str]:
    return [part.text for part in (content.parts if content else None) or [] if part.text]


def fields_of(part: types.Part) -> set[str]:
    return set(part.model_dump(exclude_none=True))


def accepted(part: types.Part) -> bool:
    """A part a person may send: text only, or the answer to the question."""
    fields = fields_of(part)
    return fields == {'text'} or (fields == {'function_response'}
                                  and getattr(part.function_response, 'name', None) == CONFIRMATION)


def only_allowed(part: types.Part) -> types.Part:
    """The part with only the fields of PART_FIELDS; a part with none of them becomes a marker."""
    if fields_of(part) <= set(PART_FIELDS):
        return part
    kept = {name: getattr(part, name) for name in PART_FIELDS if getattr(part, name) is not None}
    return types.Part(**kept) if kept else types.Part(text='[parte removida]')


def scrub(llm_request: LlmRequest, sent: list[types.Part], token: str | None, real: str | None) -> None:
    """The request with the person's own text (`sent`: the parts of the user's events) as the message
    `cli run` sends, the real file name as the token, and only text and tool calls in each part."""
    typed = {part.text for part in sent if part.text}
    others = [part for part in sent if fields_of(part) != {'text'}]  # an answer, or a part start_order refused
    for content in llm_request.contents:
        parts = [only_allowed(part) for part in content.parts or [] if part not in others]
        content.parts = parts or [types.Part(text='[parte removida]')]
    for part, text in ((part, part.text) for content in llm_request.contents for part in content.parts or [] if part.text):
        if text in typed:
            part.text = f'Arquivo do pedido: {token}' if token else '[mensagem]'
        elif token and real and real in text:
            part.text = text.replace(real, token)


def real_file(args: dict[str, Any], order: 'OrderRecord') -> dict[str, str] | None:
    """The OCR's call: the token becomes the real name; any other name is refused."""
    token, real = order.image_token, order.image_file
    if not (token and real) or args.get('filename') != token:
        order.file_refused = True
        return {'blocked': 'arquivo que não é o desta execução; use o nome informado na mensagem'}
    args['filename'] = real
    return None


def without_file_name(response: dict[str, Any], order: 'OrderRecord') -> dict[str, Any] | None:
    """The OCR's error reply with the real file name turned back into the token, or None if there is
    nothing to hide."""
    token, real = order.image_token, order.image_file
    texts = [item for item in response.get('content', []) if isinstance(item, dict) and 'text' in item]
    if not (token and real) or not any(real in item['text'] for item in texts):
        return None
    return response | {'content': [item | {'text': item['text'].replace(real, token)} if item in texts else item
                                   for item in response['content']]}
