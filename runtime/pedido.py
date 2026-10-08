"""The order's record: what the booking policy trusts, one per session, never read back from the
session state, which ADK's clients can write (`adk web`, `adk run --state`); the state gets a copy, for
the CLI and the person. A context with no session (a call outside ADK's runner) keeps it in its state.
Bounded for a long `adk web`: a record unused for IDLE_SECONDS is dropped, and only the KEEP_FINISHED
latest finished orders stay (a run that failed past the POST never finishes: it still answers). A
dropped order leaves what says it ran (KEEP_EVICTED of them), and a session's Idempotency-Key is derived
from the session: an order sent again after both are gone gets the same appointment back from the API.
"""
import collections
import copy
import hashlib
import hmac
import secrets
import threading
import time

from .entrada import image_token

PRIVATE = ('image_file', 'finished', 'own_key')  # never copied to the session state
RECORD_KEYS = tuple((  # every key of the record (tests/test_transpiler.py finds them): never a spec's output_key
    'accounted api_error ask blocked booked_appointment cancel_unlinked candidates confirmed file_refused '
    'finds finished idempotency_key image_file image_token instructions_removed invented listing low_confidence '
    'model_error ocr_confidence ocr_contested ocr_error ocr_intent ocr_lines ocr_read ocr_terms off_list order_invocation '
    'order_unchecked own_key page_clean pending pii_masked posted refused text_removed unreported').split())
KEEP_FINISHED, KEEP_EVICTED, IDLE_SECONDS = 256, 4096, 6 * 3600
EVICTED = ('order_invocation', 'idempotency_key', 'booked_appointment')  # enough to say "não repita"
SECRET = secrets.token_bytes(32)  # per process: the keys of one process are not guessable from another


def session_key(session):
    return session.app_name, session.user_id, session.id


class Orders:
    """The records of every session of one agent (one BookingCallbacks)."""

    def __init__(self):
        self.records: collections.OrderedDict = collections.OrderedDict()  # least recently used first
        self.evicted: collections.OrderedDict = collections.OrderedDict()  # session -> EVICTED of its order
        self.used: dict = {}  # session -> time.monotonic() of its last use
        self.lock = threading.Lock()  # adk web runs sessions on several threads

    def of(self, context):
        """The session's record (see the module)."""
        session = getattr(context, 'session', None)
        if session is None:
            return context.state
        return self.open(session_key(session))

    def open(self, key, record=None):
        with self.lock:
            if record is not None:
                self.records[key] = record
            record = self.records.setdefault(key, self.evicted.pop(key, {}))
            record.setdefault('own_key', hmac.new(SECRET, repr(key).encode(), hashlib.sha256).hexdigest()[:32])
            self.records.move_to_end(key)
            self.used[key] = now = time.monotonic()
            idle = [other for other, used in self.used.items() if now - used > IDLE_SECONDS]
            finished = [other for other, kept in self.records.items() if kept.get('finished')]
            for other in [*idle, *finished[:-KEEP_FINISHED]]:
                gone = self.records.pop(other, {})
                self.used.pop(other, None)
                if gone.get('order_invocation'):  # the session ran an order: it keeps saying so
                    self.evicted[other] = {name: gone[name] for name in EVICTED if name in gone}
            while len(self.evicted) > KEEP_EVICTED:
                self.evicted.popitem(last=False)
            return record

    def claim(self, order, invocation):
        """The invocation that holds the order: this one, unless another took it first."""
        with self.lock:
            return order.setdefault('order_invocation', invocation)

    def start(self, session, image_file, ask=None):
        """cli run: the order of a session the CLI created, with the image it checked; ask=False: --yes, the
        rules alone, so the list is not confirmed and the middle band is left out."""
        record = {'image_file': image_file, 'image_token': image_token(image_file)}
        if ask is not None:
            record['ask'] = ask
        self.open(session_key(session), record)

    @staticmethod
    def publish(context, order):
        """A copy of the order's record in the session state, for the CLI and the person to read."""
        if order is not context.state:
            context.state.update({key: copy.deepcopy(value) for key, value in order.items() if key not in PRIVATE})
