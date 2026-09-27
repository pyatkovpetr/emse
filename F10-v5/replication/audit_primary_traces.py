"""Read-only post-campaign audit of original F10 v5 records. No model calls."""
from pathlib import Path
from collections import Counter, defaultdict
import argparse, csv, hashlib, importlib.util, json, re, statistics, socket

def no_network(*a, **kw):
    raise RuntimeError('Network disabled in primary trace audit')
socket.socket = no_network
socket.create_connection = no_network
ap=argparse.ArgumentParser()
ap.add_argument('--campaign',type=Path,default=Path(__file__).resolve().parent/'f10-clean-v5')
ap.add_argument('--output',type=Path,default=Path(__file__).resolve().parent/'primary-audit')
args=ap.parse_args();C=args.campaign;O=args.output;O.mkdir(parents=True,exist_ok=True)
read=lambda p:json.loads(p.read_text(encoding='utf-8'))
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
canon=lambda x:json.dumps(x,sort_keys=True,separators=(',',':'))
p=read(C/'campaign-protocol.json');old=read(C/'analysis-result.json');freeze=read(C/'frozen/freeze-audit.json')
assert sha(C/'campaign-protocol.json')==old['protocol_sha256']
assert sha(C/'frozen/tasks.jsonl')==p['clean_tasks_sha256']
assert sha(C/'frozen/answers.jsonl')==p['clean_answers_sha256']
assert sha(C/'frozen/freeze-audit.json')==p['selection_audit_sha256']
assert sha(C/'frozen/excluded-task-ids.jsonl')==freeze['excluded_ids_sha256']
assert sha(C/'runner/analyze.py')==p['analysis_sha256']
assert sha(C/'bin/orion-frozen')==p['binary_sha256']
bindings=dict(p['runner_sha256']);bindings.update({'run_model.py':p['orchestrator_sha256'],'launch_four.py':p['launcher_sha256']})
for name,h in bindings.items():assert sha(C/'runner'/name)==h,name
spec=importlib.util.spec_from_file_location('frozen_analysis',C/'runner/analyze.py')
f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f)
tasks=[json.loads(x) for x in (C/'frozen/tasks.jsonl').read_text(encoding='utf-8').splitlines()]
excluded=[json.loads(x) for x in (C/'frozen/excluded-task-ids.jsonl').read_text(encoding='utf-8').splitlines()]
excluded_ids={x if isinstance(x,str) else x['task_id'] for x in excluded}
assert len(tasks)==len({x['task_id'] for x in tasks})==600
assert not excluded_ids.intersection(x['task_id'] for x in tasks)
rows=[];schemas={};counts=Counter();issues=[];failures=[];pair_rows=[];models={}
for model in p['models']:
    assert (C/'runs'/model/'complete.json').is_file()
    saved={x['task_id']:x for x in old['models'][model]['per_task']}
    lane=[]
    for t in tasks:
        pair={}
        for arm in ('rich','selective'):
            root=C/'runs'/model/f"{t['index']:04d}"/t['task_id']/arm
            r=read(root/'result.json');ex=read(root/'execution.json')
            assert r['case_id']==t['task_id'] and ex['model']==model
            assert r['binary_sha256']==ex['binary_sha256']==p['binary_sha256']
            assert ex['provider_max_attempts']==5 and ex['external_timeout_seconds']==p['task_wall_seconds']
            expected='rich' if arm=='rich' or t['family']=='gsm8k' else 'qa-direct'
            paths=sorted((root/'transport').glob('request-*/request.json'))
            assert len(paths)==r['external_requests']
            prompt=completion=receipt_n=0;request_hashes=set();transport_errors=[];first=None;previous_payload=None;previous_failed=False;transport_resubmissions=0
            for q in paths:
                request=read(q);observed=f.contract(request);counts['provider_requests']+=1
                fingerprint=hashlib.sha256(canon(request).encode()).hexdigest()
                transport_resubmissions+=int(previous_failed and fingerprint==previous_payload)
                assert request['model']==model
                if observed!=expected:issues.append({'kind':'route_mismatch','model':model,'task_id':t['task_id'],'arm':arm,'request':q.parent.name,'expected':expected,'observed':observed})
                obj=next(x['function']['parameters'] for x in request['tools'] if x.get('function',{}).get('name')=='task_complete')
                h=hashlib.sha256(canon(obj).encode()).hexdigest();request_hashes.add(h)
                if h not in schemas:schemas[h]={'contract':observed,'parameters':obj,'requests':0,'first_origin':str(q.relative_to(C))}
                schemas[h]['requests']+=1
                if first is None:first=f.first_user(request)
                rec=read(q.parent/'receipt.json');receipt_n+=1;counts['transport_receipts']+=1
                previous_payload=fingerprint;previous_failed=rec['status']!='COMPLETE'
                expected_hash=hashlib.sha256(json.dumps(request,sort_keys=True).encode()).hexdigest()
                assert rec['request_sha256']==expected_hash,(q,'request hash')
                if rec['status']=='COMPLETE':
                    response=read(q.parent/'response.json');assert response.get('usage',{})==rec.get('usage',{})
                    u=rec.get('usage',{})
                    if 'prompt_tokens' not in u or 'completion_tokens' not in u:issues.append({'kind':'incomplete_usage','request':str(q.relative_to(C))})
                    prompt+=u.get('prompt_tokens',0);completion+=u.get('completion_tokens',0)
                else:
                    counts['provider_failure_receipts']+=1
                    transport_errors.append({k:rec.get(k) for k in ('index','status','error_type','http_status','wall_sec')})
            assert prompt==r['prompt_tokens'] and completion==r['completion_tokens'],root
            assert receipt_n==r['complete_transport_receipts']+r['provider_failures'],root
            assert (prompt+completion)==saved[t['task_id']][arm+'_tokens']
            assert bool(r['success_by_deadline'])==saved[t['task_id']][arm+'_correct']
            assert r['integrity_status']==saved[t['task_id']][arm+'_integrity']
            activity=set();steps=set();tool_events=[];guard_events=[];outcome={};model_waits=0
            for session in sorted((root/'home/sessions').glob('*.jsonl')):
                for line in session.read_text(encoding='utf-8').splitlines():
                    record=json.loads(line);d=record.get('data',{})
                    if record.get('type')=='meta':outcome=d.get('outcome') or outcome
                    if record.get('type')!='agent_event':continue
                    if d.get('type')=='provider_activity' and isinstance(d.get('attempt'),int):
                        activity.add((d.get('step'),d['attempt']));steps.add(d.get('step'))
                    if d.get('type')=='model_wait':model_waits+=1
                    if d.get('type')=='tool_result' and d.get('tool')=='task_complete':tool_events.append(d)
                    if d.get('type')=='controller' and re.search(r'executor.validation|file changes|tool_refusal',d.get('content',''),re.I):guard_events.append(d.get('content',''))
            retry_attempts=sum(attempt>1 for step,attempt in activity)
            synthetic=sum(x.get('toolCallId')=='completion-text-relay' for x in tool_events)
            terminal_model_calls=len(tool_events)-synthetic
            native_errors=[i for i,x in enumerate(tool_events) if x.get('toolCallId')!='completion-text-relay' and re.search(r'(?im)(?:^|\n)(?:ERROR:|result: FAIL)|policy_denied:',x.get('result',''))]
            after_error_accepted=bool(native_errors and any(i>min(native_errors) and x.get('result','').startswith('accepted:') for i,x in enumerate(tool_events)) and r.get('completion_status')=='completed' and r.get('completion_accepted'))
            if len(request_hashes)>1:counts['within_window_schema_variation']+=1
            row={'model':model,'task_id':t['task_id'],'family':t['family'],'arm':arm,'contract':expected,
                'requests':len(paths),'prompt_tokens':prompt,'completion_tokens':completion,
                'agent_wall_sec':r['agent_wall_sec'],'provider_failures':r['provider_failures'],
                'provider_retry_attempts_observed':transport_resubmissions,'heartbeat_retry_keys':retry_attempts,'provider_activity_attempt_keys':len(activity),
                'model_wait_events':model_waits,'model_terminal_tool_results':terminal_model_calls,
                'synthetic_terminal_tool_results':synthetic,'native_negative_terminal_results':len(native_errors),'negative_then_accepted_completion':after_error_accepted,'negative_tool_results':r['negative_tool_results'],
                'executor_guard_feedback_events':len(guard_events),'completion_reason':r.get('completion_reason'),
                'completion_status':r.get('completion_status'),'completion_accepted':r.get('completion_accepted'),
                'correct_accepted':r['success_by_deadline'],'integrity_status':r['integrity_status'],'timed_out':r['timed_out']}
            rows.append(row);lane.append(row);pair[arm]={'row':row,'user':first,'initial':sha(root/'initial-hashes.json')}
            if r['integrity_status']!='PASS' or not r['usage_complete']:
                failures.append({'model':model,'task_id':t['task_id'],'arm':arm,'transport_errors':transport_errors,
                    'usage_complete':r['usage_complete'],'provider_failures':r['provider_failures'],
                    'known_infrastructure_errors':r['known_infrastructure_errors'],'tool_infrastructure_errors':r['tool_infrastructure_errors'],
                    'timed_out':r['timed_out'],'grade':r['grade'],'completion_reason':r.get('completion_reason'),
                    'recorded_prompt_tokens':prompt,'recorded_completion_tokens':completion})
        assert pair['rich']['user']==pair['selective']['user']
        assert pair['rich']['initial']==pair['selective']['initial']
        pair_rows.append({'model':model,'task_id':t['task_id'],'family':t['family'],
            'wall_seconds_saved':pair['rich']['row']['agent_wall_sec']-pair['selective']['row']['agent_wall_sec']})
    assert sum(x['requests'] for x in lane)==old['models'][model]['provider_requests']
    def group_summary(group):
        def quantile(xs,p):return sorted(xs)[min(len(xs)-1,int(len(xs)*p))]
        walls=[x['agent_wall_sec'] for x in group]
        return {'windows':len(group),'prompt_tokens':sum(x['prompt_tokens'] for x in group),
            'completion_tokens':sum(x['completion_tokens'] for x in group),'requests':sum(x['requests'] for x in group),
            'provider_retry_attempts_observed':sum(x['provider_retry_attempts_observed'] for x in group),
            'provider_failure_receipts':sum(x['provider_failures'] for x in group),
            'model_terminal_tool_results':sum(x['model_terminal_tool_results'] for x in group),
            'windows_multiple_model_terminal_calls':sum(x['model_terminal_tool_results']>1 for x in group),
            'synthetic_terminal_tool_results':sum(x['synthetic_terminal_tool_results'] for x in group),
            'negative_tool_results':sum(x['negative_tool_results'] for x in group),
            'native_negative_terminal_results':sum(x['native_negative_terminal_results'] for x in group),
            'windows_negative_then_accepted_completion':sum(x['negative_then_accepted_completion'] for x in group),
            'windows_executor_guard_feedback':sum(x['executor_guard_feedback_events']>0 for x in group),
            'windows_guard_then_accepted_completion':sum(x['executor_guard_feedback_events']>0 and x['completion_status']=='completed' and x['completion_accepted'] for x in group),
            'completion_reasons':dict(Counter(x['completion_reason'] for x in group)),
            'wall_seconds_mean':statistics.mean(walls),'wall_seconds_median':statistics.median(walls),'wall_seconds_p95':quantile(walls,.95)}
    models[model]={'arms':{arm:group_summary([x for x in lane if x['arm']==arm]) for arm in ('rich','selective')},
        'families':{family:{arm:group_summary([x for x in lane if x['family']==family and x['arm']==arm]) for arm in ('rich','selective')} for family in ('gsm8k','mmlu','squad2')},
        'paired_wall_seconds_saved_mean':statistics.mean(x['wall_seconds_saved'] for x in pair_rows if x['model']==model),
        'paired_wall_seconds_saved_median':statistics.median(x['wall_seconds_saved'] for x in pair_rows if x['model']==model)}
summary={'study_id':p['study_id'],'analysis_role':'Additional descriptive audit after campaign; frozen primary analysis unchanged',
    'model_calls':0,'windows_verified':len(rows),'pairs_verified':len(pair_rows),'counts':dict(counts),
    'all_request_route_mismatches':issues,'all_per_task_saved_outcomes_match':True,
    'excluded_ids':len(excluded_ids),'selected_excluded_overlap':0,'schemas':len(schemas),
    'models':models,'integrity_failure_records':failures,
    'provider_retry_definition':'Identical canonical payload resubmissions immediately following a failed provider receipt, with no intervening success. Heartbeat attempt fields are not used: they remain 1 during lower-layer retries.',
    'executor_guard_definition':'Controller event text matching executor validation, file changes, or tool_refusal; descriptive trace classification',
    'receipt_hash_scope':'Canonical request hash and saved response usage verified; original HTTP response body bytes were not saved',
    'unreported_failed_call_token_expenditure':'Unknown where failed provider receipt has no usage; not invented or silently treated as complete accounting'}
assert len(rows)==4800 and len(pair_rows)==2400
(O/'primary-audit-summary.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
(O/'frozen-observed-schemas.json').write_text(json.dumps(schemas,indent=2)+'\n',encoding='utf-8')
with (O/'trial-measurements.csv').open('w',encoding='utf-8',newline='') as stream:
    w=csv.DictWriter(stream,fieldnames=list(rows[0]),lineterminator='\n');w.writeheader();w.writerows(rows)
print(json.dumps({'windows':len(rows),'pairs':len(pair_rows),'counts':dict(counts),'schema_objects':len(schemas),'issues':len(issues),'integrity_failures':len(failures),'model_calls':0}))
