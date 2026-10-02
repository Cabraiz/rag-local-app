# Workspace paths: standalone scripts remain executable after regrouping.
import sys as _workspace_sys
from pathlib import Path as _WorkspacePath
_workspace_root = next(p for p in _WorkspacePath(__file__).resolve().parents
                       if (p / "app/workspace.py").is_file())
_workspace_sys.path.insert(0, str(_workspace_root / "app"))
from workspace import bootstrap as _workspace_bootstrap, named_file as _named_file
_workspace_bootstrap()

APP = _workspace_root / "app"

"""Documentation/graph countermodels only. No MCP connection, credentials or runtime test."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import re
import secrets

ROOT=Path(__file__).resolve().parent
FILES=['application.md','application-eraser.txt','production-contracts.md','production-policy.json','remote-mcp.md','remote-mcp-acceptance.md','review_remote_mcp.py']


def hashes():
    return {f:hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in FILES}


def edges(text):
    return [(m[0],m[1]) for m in re.findall(r'^([A-Za-z]+)\s+(?:>|-->)\s+([A-Za-z]+)',text,re.M)]


def reachable(graph, source, dest, without=None):
    if source==without: return False
    pending=[source]; seen=set()
    while pending:
        node=pending.pop()
        if node==dest: return True
        if node in seen: continue
        seen.add(node)
        pending.extend(b for a,b in graph if a==node and b!=without)
    return False


def gateway_safe(graph):
    return all(reachable(graph,agent,provider) and not reachable(graph,agent,provider,without='MCP')
               for agent in ('ADK','Deep','Ports') for provider in ('GH','AT'))


def checks():
    dsl=(ROOT/'application-eraser.txt').read_text(encoding='utf8')
    doc=(ROOT/'remote-mcp.md').read_text(encoding='utf8')
    policy=json.loads((ROOT/'production-policy.json').read_text(encoding='utf8'))['remote_mcp']
    graph=edges(dsl)
    # Current revision has its own capture; never overwrite the historical readback.
    readback=(ROOT/'application-eraser-segments-readback.txt').read_text(encoding='utf8')
    return {
        'eraser_source_equals_local':dsl.replace('\r','').strip()==readback.replace('\r','').strip(),
        'two_remote_providers_declared':all(x in dsl for x in ('GitHub MCP remoto','Atlassian MCP remoto','6 MCP ONLINE PLANEJADO')),
        'all_agents_and_ports_go_through_gateway':gateway_safe(graph),
        'negative_direct_github_bypass_detected':not gateway_safe(graph+[('ADK','GH')]),
        'negative_direct_atlassian_bypass_detected':not gateway_safe(graph+[('Deep','AT')]),
        'negative_missing_provider_detected':not gateway_safe([(a,b) for a,b in graph if b!='AT']),
        'private_data_paths_preserved':all((a,b) in graph for a,b in [('IO','PG'),('IO','QD'),('IO','FS'),('IO','Remote')]),
        'control_and_release_preserved':all((a,b) in graph for a,b in [('Control','UseCases'),('Eval','Release')]),
        'official_https_endpoints':policy['providers']['github']['endpoint']=='https://api.githubcopilot.com/mcp/' and policy['providers']['atlassian']['endpoint']=='https://mcp.atlassian.com/v2/mcp',
        'disabled_until_authorized':policy['status']=='planned_not_connected' and all(p['enabled'] is False for p in policy['providers'].values()),
        'proposed_guards_present':all(policy[k] for k in ('read_only_required','per_identity_credentials','gateway_required','acl_recheck_before_delivery','logs_redacted')),
        'proposed_limits_bounded':policy['connect_seconds']==5 and policy['call_seconds']==20 and policy['max_attempts']==2 and policy['max_response_bytes']==1048576,
        'authorization_and_cache_contract':all(t in doc for t in ('cloudId','sessões e caches','revogação','token global','credencial')),
        'read_only_atlassian_not_assumed_from_github':all(t in doc for t in ('Não presumir que o header read-only do GitHub existe na Atlassian','nega cada operação','Criar/editar/comentar/transition/merge/delete')),
        'durable_failure_and_deadline_contract':all(t in doc for t in ('Retry-After','durável','circuit breaker','deadline','UNKNOWN')),
        'no_std_io_substitution_or_fake_runtime_pass':all(t in doc for t in ('Não será instalado um servidor','Filesystem/stdio','nenhum está aprovado','Não houve conexão')),
        'logs_and_untrusted_output_contract':all(t in doc for t in ('Não registram OAuth/PAT','dados não confiáveis')),
        'quality_and_scale_still_gated':all(t in doc for t in ('100.000','duas rodadas consecutivas','não prova conexão MCP')),
    }


def main():
    frozen=hashes()
    folder=ROOT.parent.parent/'eval/runs'/('remote-mcp-design-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+secrets.token_hex(3))
    folder.mkdir(parents=True)
    seeds=[secrets.randbits(32),secrets.randbits(32)]
    (folder/'contract.json').write_text(json.dumps(dict(scope='document_and_graph_checks_only',same_reviewer=True,seeds=seeds,sha256=frozen),indent=2),encoding='utf8')
    rounds=[]
    for seed in seeds:
        items=list(checks().items()); random.Random(seed).shuffle(items)
        rounds.append(dict(seed=seed,checks=[dict(name=k,passed=v) for k,v in items],passed=sum(v for _,v in items),total=len(items)))
    unchanged=hashes()==frozen
    passed=unchanged and all(r['passed']==r['total'] for r in rounds)
    receipt=dict(rounds=rounds,files_unchanged=unchanged,document_checks_passed=passed,real_mcp_tested=False,gate_b_passed=False)
    (folder/'receipt.json').write_text(json.dumps(receipt,indent=2),encoding='utf8')
    print(json.dumps(dict(receipt=str(folder/'receipt.json'),rounds=[dict(seed=r['seed'],passed=r['passed'],total=r['total']) for r in rounds],document_checks_passed=passed,real_mcp_tested=False)))
    raise SystemExit(0 if passed else 1)


if __name__=='__main__': main()
