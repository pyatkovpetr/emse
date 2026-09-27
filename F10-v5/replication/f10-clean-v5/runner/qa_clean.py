"""Host-only F10 clean QA adapter; answers never enter model workspace."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import cases

HERE = Path(__file__).resolve().parent
FROZEN = HERE.parent / "frozen"


def sha(data: bytes | str) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def _load():
    audit = json.loads((FROZEN / "freeze-audit.json").read_text(encoding="utf-8"))
    tasks_path, answers_path = FROZEN / "tasks.jsonl", FROZEN / "answers.jsonl"
    if sha(tasks_path.read_bytes()) != audit["tasks_sha256"] or sha(answers_path.read_bytes()) != audit["private_answers_sha256"]:
        raise RuntimeError("frozen clean material hash mismatch")
    tasks = {row["task_id"]: row for row in
             (json.loads(line) for line in tasks_path.read_text(encoding="utf-8").splitlines())}
    answers = {row["task_id"]: row["target"] for row in
               (json.loads(line) for line in answers_path.read_text(encoding="utf-8").splitlines())}
    if len(tasks) != 600 or set(tasks) != set(answers):
        raise RuntimeError("clean task/answer identity mismatch")
    return tasks, answers


TASKS, ANSWERS = _load()


def list_cases():
    return [{"id": task_id, "family": row["family"]} for task_id, row in TASKS.items()]


def materialize(case_id: str, workspace: Path):
    row = TASKS[case_id]
    workspace = Path(workspace).resolve()
    if workspace.exists() and any(workspace.iterdir()):
        raise FileExistsError("clean workspace not empty")
    workspace.mkdir(parents=True, exist_ok=True)
    prompt = row["prompt"]
    if sha(prompt) != row["prompt_sha256"]:
        raise RuntimeError("clean prompt hash mismatch")
    (workspace / "TASK.md").write_text(prompt, encoding="utf-8")
    return {"id": case_id, "kind": "qa", "prompt": prompt,
            "config": {"case_family": "qa-terminal", "network_required": False,
                       "editable_files": [], "mechanism_exposure": "measure_from_forwarded_requests",
                       "platform_adjustments": []},
            "mechanisms": ["F10"],
            "grader_inputs": {"case_id": case_id, "workspace": str(workspace)},
            "prompt_sha256": sha(prompt), "original_prompt_sha256": sha(prompt),
            "manifest_sha256": sha((FROZEN / "tasks.jsonl").read_bytes()),
            "source_label": row["source_label"]}


def grade(case_id: str, workspace: Path, model_final: object):
    row = TASKS[case_id]
    item = {"dataset": row["family"], "target": ANSWERS[case_id]}
    return {"case_id": case_id, "mechanisms": ["F10"],
            "exposure": "not_measured_by_grader", **cases._grade_qa(item, model_final)}


def self_test():
    for case_id, row in TASKS.items():
        target = ANSWERS[case_id]
        if row["family"] == "gsm8k":
            correct = target["canonical_number"]
            wrong = str(int(correct.replace(",", "")) + 1) if correct.replace(",", "").lstrip("-").isdigit() else "wrong"
        elif row["family"] == "mmlu":
            correct = target["choice"]
            wrong = next(choice for choice in "ABCD" if choice != correct)
        else:
            correct = target["answers"][0]
            wrong = "zz_unrelated_nonanswer_qa_study_zz"
        if not grade(case_id, Path("."), {"status": "completed", "response": correct})["correctness"]:
            raise AssertionError("positive grader failure")
        if grade(case_id, Path("."), {"status": "completed", "response": wrong})["correctness"]:
            raise AssertionError("negative grader failure")
        if grade(case_id, Path("."), {"status": "blocked", "response": correct})["correctness"]:
            raise AssertionError("blocked accepted")
    return {"status": "PASS", "cases": len(TASKS), "new_model_requests": 0}


if __name__ == "__main__":
    print(json.dumps(self_test()))
