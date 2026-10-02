import logging
import warnings

class SafeHandler(logging.Handler):
    def emit(self, record):
        # Never format record.msg/args/exception, which may contain inputs.
        print('{"event":"sdk_diagnostic","level":"WARNING"}', flush=True)

def setup():
    root = logging.getLogger()
    root.handlers = [SafeHandler()]
    root.setLevel(logging.ERROR)
    for value in logging.Logger.manager.loggerDict.values():
        if isinstance(value, logging.Logger):
            value.handlers = []
            value.propagate = True
            value.setLevel(logging.ERROR)
    warnings.filterwarnings('ignore', category=UserWarning, module='google.adk')
