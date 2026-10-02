import os
os.environ['CLINIC_DB'] = '/tmp/clinic-unit-tests.sqlite3'
os.environ['OTEL_SDK_DISABLED'] = 'true'

def pytest_collection_modifyitems(items):
    """Vary ordering reproducibly without editing frozen sources between rounds."""
    import random
    random.Random(int(os.environ.get('CF_SEED', '1234'))).shuffle(items)
