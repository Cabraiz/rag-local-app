"""Delivery integrity must survive repeated exports, not only a clean folder."""
import hashlib
import json
from pathlib import Path
from tools import export_examples

def test_repeated_export_manifest_matches_every_artifact_without_self_reference(tmp_path,monkeypatch):
    # Isolate only the output folder; samples and live API remain real.
    root=tmp_path/'package'
    root.mkdir()
    (root/'unrelated.txt').write_text('NOT_AN_EXPORTED_ARTIFACT')
    real_path=Path
    monkeypatch.setattr(export_examples,'Path',lambda value:root if str(value)=='/artifacts/package' else real_path(value))
    for _ in range(2):
        export_examples.main()
    manifest=json.loads((root/'manifest.json').read_text())
    assert manifest['fictional'] is True
    assert 'manifest.json' not in manifest['sha256']
    assert 'unrelated.txt' not in manifest['sha256']
    assert (root/'unrelated.txt').read_text()=='NOT_AN_EXPORTED_ARTIFACT'
    assert len(manifest['sha256'])==13
    for name,digest in manifest['sha256'].items():
        assert Path(name).name==name
        assert hashlib.sha256((root/name).read_bytes()).hexdigest()==digest
