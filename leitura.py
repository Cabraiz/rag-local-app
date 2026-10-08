"""The OCR server's reply to the agent, declared once for both images: the OCR server builds it (mcp_servers/ocr.py)
and the runtime validates it once (runtime/confianca.py, remember_ocr); a reply that does not validate is not read.
VERSION changes when a field is added or changes meaning. A field left out fails closed: no reading, no kind or no
contested set asks every exam at most. The tool still declares a plain object: ADK copies a tool's outputSchema into
the declaration the model reads, and the model only gets the exam lines (runtime/callbacks.py, after_tool)."""
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, StrictBool, StrictFloat, StrictInt, model_validator

VERSION: Final = 1
# What a line asks for (guardrails/intent.py): only 'request' books alone, nothing is booked from 'negated',
# 'history' or 'prep', and the rest is asked.
Intent = Literal['request', 'negated', 'history', 'uncertain', 'prep', 'unrecognized', 'table', 'form']


class Contest(BaseModel):
    """An exam the page contests, and why: a line that says not to do it, that it was done, or an instruction on it."""
    model_config = ConfigDict(extra='forbid')
    code: str
    name: str
    reason: Literal['negated', 'history', 'instruction']


class OcrReading(BaseModel):
    """The lines read, PII masked, and what the OCR says of each (one value per line, in the same order)."""
    model_config = ConfigDict(extra='forbid')
    version: Literal[1]
    lines: list[str]
    line_confidence: list[StrictFloat] | None = None  # Tesseract's reading, 0-100
    line_intent: list[Intent] | None = None
    contested_exams: list[Contest] | None = None
    page_clean: StrictBool = False  # nothing on the page besides the list of exams: it may book alone
    cancel_unlinked: StrictBool = False  # a cancellation no exam was tied to: nothing books alone
    off_list: list[StrictInt] = []  # the lines that keep the page from being clean
    exam_lines: list[int] = []  # the only lines the model reads
    exam_terms: list[list[tuple[str, str]]] = []  # per line: [catalog term, exam name] of each name written whole
    pii_masked: dict[str, int] = {}  # personal data masked, by type
    instructions_removed: int = 0  # orders to the model taken out
    text_removed: int = 0  # [TEXTO_REMOVIDO] pieces

    @model_validator(mode='after')
    def one_per_line(self) -> 'OcrReading':
        for values in (self.line_confidence, self.line_intent, self.exam_terms or None):
            if values is not None and len(values) != len(self.lines):
                raise ValueError('a value for each line read')
        return self
