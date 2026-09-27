"""Reproduce v5 selection from frozen sources and actual exclusion ledger."""
from pathlib import Path
import ast,hashlib,importlib.util,json,socket
H=Path(__file__).resolve().parent;C=H/'f10-clean-v5';S=H/'sources'
read=lambda p:json.loads(p.read_text(encoding='utf-8'))
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
p=read(C/'campaign-protocol.json');audit=read(C/'frozen/freeze-audit.json')
for name,h in audit['source_hashes'].items():assert sha(S/name)==h
assert sha(C/'frozen/excluded-task-ids.jsonl')==audit['excluded_ids_sha256']
excluded={json.loads(s)['task_id'] for s in (C/'frozen/excluded-task-ids.jsonl').read_text(encoding='utf-8').splitlines()}
def blocked(*a,**kw):raise RuntimeError('Network disabled')
socket.socket=blocked;socket.create_connection=blocked
spec=importlib.util.spec_from_file_location('source_freezer',H/'freeze_tasks_f10_source.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);m.STUDY_ID=p['study_id']
# Load only original pure formatting functions; never import or run the absolute-path wrapper.
tree=ast.parse((H/'freeze_tasks_f10_v5.py').read_text(encoding='utf-8'))
defs=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ('sha','canonical','task_row')]
env={'hashlib':hashlib,'json':json};exec(compile(ast.Module(body=defs,type_ignores=[]),'original_v5_pure_formatting','exec'),env)
pools={'gsm8k':m.freeze_gsm(S/'gsm8k-test.jsonl'),'mmlu':m.freeze_mmlu(S/'mmlu-data.tar'),'squad2':m.freeze_squad(S/'squad2-dev.json')}
selected=[]
for family in ('gsm8k','squad2'):
    candidates=[r for r in pools[family] if family+'_'+r['upstream_task_id'] not in excluded]
    candidates.sort(key=lambda r:r['selection_hash']);selected.extend(candidates[:200])
for group in ('humanities','social_sciences','stem','other'):
    candidates=[r for r in pools['mmlu'] if r['subject_group']==group and 'mmlu_'+r['upstream_task_id'] not in excluded]
    candidates.sort(key=lambda r:r['selection_hash']);selected.extend(candidates[:50])
selected.sort(key=lambda r:(r['dataset'],r['selection_hash']));assert len(selected)==600
tasks=[env['task_row'](r,i) for i,r in enumerate(selected)]
answers=[{'task_id':r['dataset']+'_'+r['upstream_task_id'],'target':r['target']} for r in selected]
result={}
for name,rows,key in [('tasks.jsonl',tasks,'clean_tasks_sha256'),('answers.jsonl',answers,'clean_answers_sha256')]:
    data=b''.join(env['canonical'](r)+b'\n' for r in rows)
    digest=hashlib.sha256(data).hexdigest();assert digest==p[key]
    assert data==(C/'frozen'/name).read_bytes();result[name]=digest
O=H/'selection-reproduced';O.mkdir(exist_ok=True)
(O/'selection-reproduction.json').write_text(json.dumps({'model_calls':0,'tasks':600,'excluded_ids':len(excluded),'source':'Full frozen pools and exclusion ledger, without using outcome-selected IDs','exact_manifest_sha256':result,'freeze_chronology_or_prior_exposure_completeness_proved':False},indent=2)+'\n',encoding='utf-8')
print('PASS exact v5 selection and targets from source pools and exclusion ledger; zero model calls')
