"""Deterministic reanalysis of saved F10 v5 records. Standard library only.

No campaign main(), no provider import, no model calls. This reproduces the
statistical analysis, not the missing primary transport/runtime evidence.
"""
from pathlib import Path
import csv, hashlib, importlib.util, json, socket, sys

ROOT=Path(__file__).resolve().parent
C=ROOT/'f10-clean-v5'
OUT=ROOT/'reproduced'; OUT.mkdir(exist_ok=True)
def no_network(*a,**kw): raise RuntimeError('Network is disabled in offline reproduction')
socket.socket=no_network
socket.create_connection=no_network
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
read=lambda p:json.loads(p.read_text(encoding='utf-8'))
p=read(C/'campaign-protocol.json'); original=read(C/'analysis-result.json')
assert sha(C/'campaign-protocol.json')==original['protocol_sha256']
assert sha(C/'runner/analyze.py')==p['analysis_sha256']==original['analysis_sha256']
assert sha(C/'frozen/freeze-audit.json')==p['selection_audit_sha256']
freeze=read(C/'frozen/freeze-audit.json')
assert sha(C/'frozen/answers.jsonl')==p['clean_answers_sha256']==freeze['private_answers_sha256']
for name,h in freeze['source_hashes'].items():assert sha(ROOT/'sources'/name)==h,(name,'source hash mismatch')
bindings=dict(p['runner_sha256']); bindings.update({'run_model.py':p['orchestrator_sha256'],'launch_four.py':p['launcher_sha256']})
for f,h in bindings.items():assert sha(C/'runner'/f)==h,(f,'hash mismatch')
spec=importlib.util.spec_from_file_location('frozen_statistics',C/'runner/analyze.py')
f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f)
models={};family_rows=[];identity_sets=[];ids=None
if (C/'frozen/tasks.jsonl').exists():
 assert sha(C/'frozen/tasks.jsonl')==p['clean_tasks_sha256']
 ids={json.loads(l)['task_id'] for l in (C/'frozen/tasks.jsonl').read_text(encoding='utf-8').splitlines()}
for model in p['models']:
 d=original['models'][model];rows=d['per_task']; tids={r['task_id'] for r in rows}
 assert len(rows)==len(tids)==600
 assert ids is None or ids==tids
 identity_sets.append(tids)
 for family in ('gsm8k','mmlu','squad2'):assert sum(r['family']==family for r in rows)==200
 saving,loss=f.metric(rows);task=f.bootstrap(rows,cluster=False);cluster=f.bootstrap(rows,cluster=True)
 assert saving==d['saving'] and loss==d['quality_loss']
 assert task==d['task_bootstrap'] and cluster==d['source_cluster_sensitivity']
 integrity=sum(r[a+'_integrity']!='PASS' for r in rows for a in ('rich','selective'))
 assert integrity==d['integrity_failures']
 passed=(integrity==0 and task['saving_lower_multiplicity_adjusted']>.15 and
  cluster['saving_lower_multiplicity_adjusted']>.15 and task['quality_loss_upper_multiplicity_adjusted']<.05 and
  cluster['quality_loss_upper_multiplicity_adjusted']<.05)
 status='CONFIRMATION_PASS' if passed else 'CONFIRMATION_NOT_ESTABLISHED'
 assert status==d['status']
 out={k:d[k] for k in ('status','tasks','trials','provider_requests','logical_tokens_rich','logical_tokens_selective','saving','quality_loss','integrity_failures')}
 out['task_bootstrap']=task;out['source_cluster_sensitivity']=cluster
 out['correct_rich']=sum(r['rich_correct'] for r in rows);out['correct_selective']=sum(r['selective_correct'] for r in rows)
 out['selective_only_correct']=sum(r['selective_correct'] and not r['rich_correct'] for r in rows)
 out['rich_only_correct']=sum(r['rich_correct'] and not r['selective_correct'] for r in rows)
 out['integrity_failure_records']=[{'task_id':r['task_id'],'arm':a,'status':r[a+'_integrity']} for r in rows for a in ('rich','selective') if r[a+'_integrity']!='PASS']
 for family in ('gsm8k','mmlu','squad2'):
  rs=[r for r in rows if r['family']==family]
  family_rows.append([model,family,len(rs),sum(r['rich_tokens'] for r in rs),sum(r['selective_tokens'] for r in rs),f.metric(rs)[0],sum(r['rich_correct'] for r in rs),sum(r['selective_correct'] for r in rs)])
 models[model]=out
assert all(s==identity_sets[0] for s in identity_sets)
report={'study_id':p['study_id'],'unique_task_identities':600,'model_task_pairs':2400,'trial_windows':4800,
 'model_calls':0,'analysis_reproduced_exactly':True,'raw_execution_audit':'NOT_REPRODUCED_WITHOUT_RECEIPTS','models':models}
(OUT/'offline-results.json').write_text(json.dumps(report,sort_keys=True,indent=2)+'\n',encoding='utf-8')
with (OUT/'family-results.csv').open('w',encoding='utf-8',newline='') as stream:
 w=csv.writer(stream,lineterminator='\n');w.writerow(['model','family','pairs','rich_tokens','selective_tokens','saving','correct_rich','correct_selective']);w.writerows(family_rows)
print('PASS: exact frozen statistics for all four models; zero model calls; 600 unique task IDs')
