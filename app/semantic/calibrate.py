"""Calibrate from calibration only. Holdout is deliberately absent from image."""
import hashlib
import json
from pathlib import Path
from engine import Encoder,VERSION
import semantic_policy

def main():
    source=Path('/srv/calibration.json'); data=json.loads(source.read_text())
    docs=data['documents']; cases=data['cases']; encoder=Encoder()
    doc_vectors=encoder.embed([d['title']+' '+d['text'] for d in docs])
    queries=encoder.embed([semantic_policy.retrieval_query(c['question']) for c in cases]); options=[]
    for step in range(30,91):
        threshold=step/100; positive=0; false_accept=0
        for case,query in zip(cases,queries):
            ranked=sorted([(sum(a*b for a,b in zip(query,vector)),d['source_key']) for d,vector in zip(docs,doc_vectors) if semantic_policy.eligible(case['question'],d['text'])],reverse=True)
            predicted=None
            if ranked and ranked[0][0]>=threshold and (len(ranked)<2 or ranked[0][0]-ranked[1][0]>=.05): predicted=ranked[0][1]
            if case['expected'] is None: false_accept+=predicted is not None
            else: positive+=predicted==case['expected']
        if false_accept==0: options.append((positive,threshold))
    if not options: raise RuntimeError('NO_SAFE_CALIBRATION_THRESHOLD')
    # Highest calibration recall, then lowest zero-false-accept threshold.
    # No evaluation questions are present in this image or consulted here.
    positive,threshold=max(options,key=lambda item:(item[0],-item[1]))
    total=sum(c['expected'] is not None for c in cases)
    if positive/total<.75: raise RuntimeError('CALIBRATION_RECALL_GATE_FAILED')
    gates=dict(model_version=VERSION,calibration=data['id'],threshold=threshold,margin=.05,
        calibration_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        policy_sha256=hashlib.sha256(Path(semantic_policy.__file__).read_bytes()).hexdigest(),
        positive_correct=positive,positive_total=total,false_accepts=0,holdout_used=False)
    Path('/srv/gates.json').write_text(json.dumps(gates,indent=2))
    print(json.dumps({'calibration_only':True,'threshold':threshold,'correct':positive,'total':total}),flush=True)

if __name__=='__main__': main()
