#!/usr/bin/env python3
import hashlib, importlib.util, json, os, sys
from pathlib import Path

ROOT = Path('/home/petr/research-agent-sandbox/feedback-study-2026-09-23')
CAMPAIGN = ROOT / 'f10-clean-v5'
OLD = ROOT / 'f10-clean'
SOURCE_SCRIPT = ROOT / 'freeze_tasks_f10_source.py'
STUDY_ID = 'F10-EMSE-CLEAN-V5-2026-09-25'

spec = importlib.util.spec_from_file_location('source_freezer', SOURCE_SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
mod.STUDY_ID = STUDY_ID

def sha(b): return hashlib.sha256(b).hexdigest()
def canonical(v): return json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()

def load_old_ids():
    ids = set()
    for campaign in (OLD, ROOT / 'f10-clean-v2', ROOT / 'f10-clean-v2-retryfix', ROOT / 'f10-clean-v3', ROOT / 'f10-clean-v3', ROOT / 'f10-clean-v4'):
        p = campaign / 'frozen/tasks.jsonl'
        if p.is_file():
            for line in p.read_text(encoding='utf-8').splitlines():
                if line:
                    ids.add(json.loads(line)['task_id'])
    ledger = ROOT / 'development-ledger.json'
    x = json.loads(ledger.read_text(encoding='utf-8'))
    for row in x.get('tasks', []):
        if row.get('family') == 'qa-terminal': ids.add(row['task_id'])
    for values in x.get('families', {}).values():
        if isinstance(values, list):
            for v in values:
                if isinstance(v, str) and (v.startswith(('gsm8k_', 'mmlu_', 'squad2_')) or v.startswith(('gsm8k-', 'mmlu-', 'squad2-'))):
                    ids.add(v)
    return ids

def task_row(row, index):
    dataset = row['dataset']; uid = row['upstream_task_id']; tid = f'{dataset}_{uid}'
    art = row.get('title') if dataset == 'squad2' else None
    psha = None
    if dataset == 'squad2':
        passage = row['prompt'].split('\n\nQuestion:', 1)[0]
        psha = sha(passage.encode())
    rank = row['selection_hash']
    arm_order = ['rich', 'selective'] if int(rank[:2], 16) % 2 == 0 else ['selective', 'rich']
    return {
        'arm_order': arm_order,
        'article': art,
        'family': dataset,
        'index': index,
        'passage_sha256': psha,
        'prompt': row['prompt'],
        'prompt_sha256': sha(row['prompt'].encode()),
        'selection_rank_sha256': rank,
        'source_label': dataset,
        'subject': row.get('subject'),
        'subject_group': row.get('subject_group'),
        'task_id': tid,
    }

def main():
    source = OLD / 'sources'
    pools = {
        'gsm8k': mod.freeze_gsm(source / 'gsm8k-test.jsonl'),
        'mmlu': mod.freeze_mmlu(source / 'mmlu-data.tar'),
        'squad2': mod.freeze_squad(source / 'squad2-dev.json'),
    }
    excluded = load_old_ids()
    selected = []
    # Keep family balance and MMLU group balance fixed before outcomes.
    for dataset in ('gsm8k', 'squad2'):
        candidates = [r for r in pools[dataset] if f"{dataset}_{r['upstream_task_id']}" not in excluded]
        candidates.sort(key=lambda r: r['selection_hash'])
        if len(candidates) < 200: raise RuntimeError(f'{dataset}: only {len(candidates)} clean candidates')
        selected.extend(candidates[:200])
    groups = {g: [] for g in ('humanities','social_sciences','stem','other')}
    for r in pools['mmlu']:
        tid = f"mmlu_{r['upstream_task_id']}"
        if tid not in excluded: groups[r['subject_group']].append(r)
    for g in groups: groups[g].sort(key=lambda r: r['selection_hash'])
    for g, n in [('humanities',50),('social_sciences',50),('stem',50),('other',50)]:
        if len(groups[g]) < n: raise RuntimeError(f'mmlu {g}: only {len(groups[g])}')
        selected.extend(groups[g][:n])
    if len(selected) != 600: raise RuntimeError(len(selected))
    tids = [f"{r['dataset']}_{r['upstream_task_id']}" for r in selected]
    if len(set(tids)) != 600 or set(tids) & excluded: raise RuntimeError('identity exclusion failure')
    # Stable interleaving by family and rank; identical order in all model lanes.
    selected.sort(key=lambda r: (r['dataset'], r['selection_hash']))
    task_rows = [task_row(r, i) for i, r in enumerate(selected)]
    answer_rows = [{'task_id': f"{r['dataset']}_{r['upstream_task_id']}", 'target': r['target']} for r in selected]
    frozen = CAMPAIGN / 'frozen'; frozen.mkdir(parents=True, exist_ok=False)
    (frozen / 'tasks.jsonl').write_bytes(b''.join(canonical(r)+b'\n' for r in task_rows))
    (frozen / 'answers.jsonl').write_bytes(b''.join(canonical(r)+b'\n' for r in answer_rows))
    excluded_rows = sorted(excluded)
    (frozen / 'excluded-task-ids.jsonl').write_text(''.join(json.dumps({'task_id': x}, ensure_ascii=False, sort_keys=True)+'\n' for x in excluded_rows), encoding='utf-8')
    audit = {
      'schema': 'f10-emse-clean-freeze-v5', 'study_id': STUDY_ID,
      'selection_before_model_outcomes': True, 'model_outcomes_opened': False,
      'families': {'gsm8k':200,'mmlu':200,'squad2':200},
      'mmlu_subject_groups': {'humanities':50,'social_sciences':50,'stem':50,'other':50},
      'task_identities':600, 'excluded_task_ids':len(excluded_rows),
      'tasks_sha256': sha((frozen/'tasks.jsonl').read_bytes()),
      'private_answers_sha256': sha((frozen/'answers.jsonl').read_bytes()),
      'excluded_ids_sha256': sha((frozen/'excluded-task-ids.jsonl').read_bytes()),
      'source_hashes': {p.name: sha(p.read_bytes()) for p in sorted(source.iterdir()) if p.is_file()},
    }
    (frozen/'freeze-audit.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(audit, ensure_ascii=False, sort_keys=True))
if __name__ == '__main__': main()
