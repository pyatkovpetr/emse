"""Recompute frozen statistics directly from original trial results. No models."""
from pathlib import Path
import argparse,hashlib,importlib.util,json,socket

def blocked(*a,**kw):raise RuntimeError('Network disabled')
socket.socket=blocked;socket.create_connection=blocked
ap=argparse.ArgumentParser();ap.add_argument('--campaign',type=Path,default=Path(__file__).resolve().parent/'f10-clean-v5');ap.add_argument('--output',type=Path,default=Path(__file__).resolve().parent/'primary-reproduced');args=ap.parse_args()
C=args.campaign;O=args.output;O.mkdir(parents=True,exist_ok=True)
read=lambda p:json.loads(p.read_text(encoding='utf-8'))
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
p=read(C/'campaign-protocol.json');original=read(C/'analysis-result.json')
assert sha(C/'campaign-protocol.json')==original['protocol_sha256']
assert sha(C/'runner/analyze.py')==p['analysis_sha256']
assert sha(C/'frozen/tasks.jsonl')==p['clean_tasks_sha256']
tasks=[json.loads(s) for s in (C/'frozen/tasks.jsonl').read_text(encoding='utf-8').splitlines()]
spec=importlib.util.spec_from_file_location('frozen_statistics',C/'runner/analyze.py');f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f)
models={}
for model in p['models']:
    rows=[];requests=0;integrity=0
    for t in tasks:
        row={'task_id':t['task_id'],'family':t['family'],'subject':t['subject'],'article':t['article']}
        for arm in ('rich','selective'):
            root=C/'runs'/model/f"{t['index']:04d}"/t['task_id']/arm;r=read(root/'result.json')
            assert r['binary_sha256']==p['binary_sha256'] and r['case_id']==t['task_id']
            row[arm+'_tokens']=r['prompt_tokens']+r['completion_tokens']
            row[arm+'_correct']=bool(r['success_by_deadline']);row[arm+'_integrity']=r['integrity_status']
            requests+=r['external_requests'];integrity+=int(r['integrity_status']!='PASS' or not r['usage_complete'])
        rows.append(row)
    old=original['models'][model];assert rows==old['per_task']
    saving,loss=f.metric(rows);task=f.bootstrap(rows,cluster=False);cluster=f.bootstrap(rows,cluster=True)
    assert saving==old['saving'] and loss==old['quality_loss']
    assert task==old['task_bootstrap'] and cluster==old['source_cluster_sensitivity']
    assert requests==old['provider_requests'] and integrity==old['integrity_failures']
    passed=integrity==0 and min(task['saving_lower_multiplicity_adjusted'],cluster['saving_lower_multiplicity_adjusted'])>.15 and max(task['quality_loss_upper_multiplicity_adjusted'],cluster['quality_loss_upper_multiplicity_adjusted'])<.05
    status='CONFIRMATION_PASS' if passed else 'CONFIRMATION_NOT_ESTABLISHED';assert status==old['status']
    models[model]={'status':status,'saving':saving,'quality_loss':loss,'integrity_failures':integrity,'provider_requests':requests,'task_bootstrap':task,'source_cluster_sensitivity':cluster}
report={'model_calls':0,'input':'Original result.json for every assigned window; immutable frozen estimators','all_4800_raw_outcomes_and_all_frozen_statistics_match':True,'models':models}
(O/'primary-reproduced-statistics.json').write_text(json.dumps(report,sort_keys=True,indent=2)+'\n',encoding='utf-8')
print('PASS: all 4800 primary outcomes and frozen four-model statistics reproduced; zero model calls')
