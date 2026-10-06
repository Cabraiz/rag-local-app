"""The order's record: what the booking policy trusts, one per session, never read back from the
session state, which ADK's clients can write (`adk web`, `adk run --state`); the state gets a copy, for
the CLI and the person (OrderRecord.view). A context with no session (a call outside ADK's runner) has one
record per state it carries. Bounded for a long `adk web`: a record unused for IDLE_SECONDS is dropped, and
only the KEEP_FINISHED latest finished orders stay (a run that failed past the POST never finishes: it still
answers). A dropped order leaves what says it ran (KEEP_EVICTED of them), and a session's Idempotency-Key is
derived from the session: an order sent again after both are gone gets the same appointment back from the API.
"""
import collections
import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import asdict, dataclass, fields
from typing import Any, NamedTuple, NotRequired, TypedDict

from leitura import Intent

from .entrada import image_token


class Item(TypedDict):
    """An exam the policy sorted out, as the [s/N] question, the report and the CLI read it."""
    code: str
    name: str
    confidence: float
    line: int | None
    read: str  # the line as the OCR read it
    reason: NotRequired[str]  # why it is not booked (runtime/relatorio.py, LEFT_OUT and REFUSED)
    why: NotRequired[str]  # why it is asked although written clearly (runtime/relatorio.py, WHY)
    used_by: NotRequired[str | None]  # the exam that holds its piece of the line ('line_used')
    check: NotRequired[bool]  # in the question band, on a list that does not book


class Piece(NamedTuple):
    """Where the words of a query are in a line read, and how well they match it."""
    line: int
    start: int
    end: int
    support: float


class Accounted(NamedTuple):
    """The piece of a line an exam sorted out stands on: its plain name when it is only similar to the line
    (it then accounts only for searches of words of that name), and its own words."""
    line: int
    start: int
    end: int
    similar: str | None
    exam: str


@dataclass
class Candidate:
    """A code a search returned, at its best confidence on the order (runtime/confianca.py)."""
    name: str
    confidence: float
    score: float  # the RAG's, capped for a neighbour or a resemblance
    support: float  # how well the query matches the line read
    line: int | None
    read: str
    span: str | None  # the words found in a line, only for the exam that matches them best
    floor: float  # the OCR reading the line needs to book it alone


@dataclass
class Find:
    """An exam a search found in the order: its untied best hit and every piece of the lines its query matched."""
    code: str
    name: str
    score: float
    query: str
    pieces: list[Piece]
    floor: float


@dataclass
class OrderRecord:
    """The order's record. None: not set yet; only what is set reaches the session state."""
    order_invocation: str | None = None  # the invocation that holds the order
    image_file: str | None = None
    image_token: str | None = None  # what the model calls the image (runtime/entrada.py)
    ask: bool | None = None  # cli run: whether someone answers [s/N]
    own_key: str | None = None  # the session's Idempotency-Key, derived from it
    idempotency_key: str | None = None
    finished: bool | None = None
    model_error: str | None = None
    # What the OCR read (runtime/confianca.py, remember_ocr), or why it read nothing.
    ocr_read: list[str] | None = None
    ocr_lines: list[str] | None = None  # the lines plain (catalogo.words)
    ocr_confidence: list[float] | None = None
    ocr_intent: list[Intent] | None = None
    ocr_contested: dict[str, str] | None = None  # code -> reason
    ocr_terms: list[list[tuple[str, str]]] | None = None
    off_list: list[int] | None = None
    page_clean: bool | None = None
    cancel_unlinked: bool | None = None
    pii_masked: dict[str, int] | None = None
    instructions_removed: int | None = None
    text_removed: int | None = None
    ocr_error: str | None = None
    file_refused: bool | None = None
    # The searches and the policy (runtime/confianca.py), the booking call (runtime/callbacks.py).
    candidates: dict[str, Candidate] | None = None
    finds: list[Find] | None = None
    accounted: list[Accounted] | None = None
    pending: dict[str, list[Item]] | None = None  # call id -> the list it asked about
    refused: bool | None = None
    blocked: str | None = None
    confirmed: list[Item] | None = None
    posted: list[str] | None = None
    low_confidence: list[Item] | None = None
    listing: list[Item] | None = None  # a spec that lists exams without booking
    invented: list[str] | None = None
    booked_appointment: dict[str, Any] | None = None  # the API's reply
    api_error: str | None = None
    unreported: list[Item] | None = None  # the check of the whole order (runtime/reconcilia.py)
    order_unchecked: bool | None = None

    def view(self) -> dict[str, Any]:
        """The copy in the session state: what is set, but PRIVATE. Once the OCR answered, a reading
        it gave no usable value for (None) is copied too."""
        read = self.ocr_lines is not None
        return {name: value for name, value in asdict(self).items()
                if name not in PRIVATE and (value is not None or read and name in UNUSABLE)}


RECORD_KEYS = tuple(field.name for field in fields(OrderRecord))  # never a spec's output_key (runtime/plugin.py)
PRIVATE = ('image_file', 'finished', 'own_key')  # never copied to the session state
UNUSABLE = ('ocr_confidence', 'ocr_intent', 'ocr_contested')  # None once read: every exam asked at most
KEEP_FINISHED, KEEP_EVICTED, IDLE_SECONDS = 256, 4096, 6 * 3600
EVICTED = ('order_invocation', 'idempotency_key', 'booked_appointment')  # enough to say "não repita"
SECRET = secrets.token_bytes(32)  # per process: the keys of one process are not guessable from another
SessionKey = tuple[str, str, str]


def session_key(session: Any) -> SessionKey:
    return session.app_name, session.user_id, session.id


class Orders:
    """The records of every session of one agent (one BookingCallbacks)."""

    def __init__(self) -> None:
        self.records: collections.OrderedDict[SessionKey, OrderRecord] = collections.OrderedDict()  # least recent first
        self.evicted: collections.OrderedDict[SessionKey, OrderRecord] = collections.OrderedDict()  # EVICTED of each
        self.used: dict[SessionKey, float] = {}  # session -> time.monotonic() of its last use
        self.loose: dict[int, tuple[Any, OrderRecord]] = {}  # a context with no session: by its state
        self.lock = threading.Lock()  # adk web runs sessions on several threads

    def of(self, context: Any) -> OrderRecord:
        """The session's record (see the module)."""
        session = getattr(context, 'session', None)
        if session is None:
            return self.loose.setdefault(id(context.state), (context.state, OrderRecord()))[1]
        return self.open(session_key(session))

    def open(self, key: SessionKey, record: OrderRecord | None = None) -> OrderRecord:
        with self.lock:
            if record is not None:
                self.records[key] = record
            record = self.records.setdefault(key, self.evicted.pop(key, OrderRecord()))
            record.own_key = record.own_key or hmac.new(SECRET, repr(key).encode(), hashlib.sha256).hexdigest()[:32]
            self.records.move_to_end(key)
            self.used[key] = now = time.monotonic()
            idle = [other for other, used in self.used.items() if now - used > IDLE_SECONDS]
            finished = [other for other, kept in self.records.items() if kept.finished]
            for other in [*idle, *finished[:-KEEP_FINISHED]]:
                gone = self.records.pop(other, None)
                self.used.pop(other, None)
                if gone and gone.order_invocation:  # the session ran an order: it keeps saying so
                    self.evicted[other] = OrderRecord(**{name: getattr(gone, name) for name in EVICTED})
            while len(self.evicted) > KEEP_EVICTED:
                self.evicted.popitem(last=False)
            return record

    def claim(self, order: OrderRecord, invocation: str) -> str:
        """The invocation that holds the order: this one, unless another took it first."""
        with self.lock:
            order.order_invocation = order.order_invocation or invocation
            return order.order_invocation

    def start(self, session: Any, image_file: str, ask: bool | None = None) -> None:
        """cli run: the order of a session the CLI created, with the image it checked; ask=False: --yes, the
        rules alone, so the list is not confirmed and the middle band is left out."""
        self.open(session_key(session), OrderRecord(image_file=image_file, image_token=image_token(image_file), ask=ask))

    @staticmethod
    def publish(context: Any, order: OrderRecord) -> None:
        """A copy of the order's record in the session state, for the CLI and the person to read."""
        context.state.update(order.view())
