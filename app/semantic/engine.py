"""Pinned ONNX assets, offline CPU execution and normalized finite vectors."""
import hashlib
import json
import math
from pathlib import Path
from fastembed import TextEmbedding

VERSION='minilm-multilingual-faf4aa4225822f3bc6376869cb1164e8e3feedd0-384-v1'
REV='faf4aa4225822f3bc6376869cb1164e8e3feedd0'
MODEL='sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'

class Encoder:
    def __init__(self):
        root=Path('/models'); manifest=json.loads((root/'manifest.json').read_text())
        if manifest['revision']!=REV: raise RuntimeError('PINNED_MODEL_REVISION_MISMATCH')
        for name,item in manifest['files'].items():
            path=root/name
            if path.resolve().parent!=root or path.stat().st_size!=item['bytes']: raise RuntimeError('MODEL_ASSET_MISMATCH')
            digest=hashlib.sha256()
            with path.open('rb') as stream:
                for chunk in iter(lambda:stream.read(1048576),b''): digest.update(chunk)
            if digest.hexdigest()!=item['sha256']: raise RuntimeError('MODEL_ASSET_MISMATCH')
        if manifest['files']['model_optimized.onnx']['sha256']!='634d0f66c29dc934c8fa72b8a4fe91dd4d420a22f1d82a241058d4316e659a99': raise RuntimeError('MODEL_WEIGHTS_MISMATCH')
        self.model=TextEmbedding(model_name=MODEL,specific_model_path=root,threads=2,local_files_only=True)

    def embed(self,texts):
        values=[]
        for vector in self.model.embed(texts,batch_size=16):
            row=[float(v) for v in vector]
            if len(row)!=384 or not all(math.isfinite(v) for v in row): raise RuntimeError('EMBEDDING_SHAPE_MISMATCH')
            norm=math.sqrt(sum(v*v for v in row))
            if norm<=1e-8: raise RuntimeError('EMPTY_EMBEDDING')
            values.append([v/norm for v in row])
        if len(values)!=len(texts): raise RuntimeError('EMBEDDING_COUNT_MISMATCH')
        return values
