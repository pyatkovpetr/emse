from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
CAMPAIGN = HERE.parent
ROOT = CAMPAIGN.parent
MODELS = ("qwen3.6-fp8-noreason", "deepseek-v4.1-flash", "glm-5.3-flash", "gpt-oss-120b")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def validate(model):
    plan_path = CAMPAIGN / "campaign-protocol.json"
    plan = read(plan_path)
    checks = [
        plan["models"] == list(MODELS), plan["task_count"] == 600,
        plan["task_wall_seconds"] == 900,
        plan["global_hard_request_cap"] is None,
        plan["global_hard_token_cap"] is None,
        plan["clean_tasks_sha256"] == sha(CAMPAIGN / "frozen/tasks.jsonl"),
        plan["clean_answers_sha256"] == sha(CAMPAIGN / "frozen/answers.jsonl"),
        plan["binary_sha256"] == sha(ROOT / "bin/orion-fixed-word-boundary-r4"),
        plan["orchestrator_sha256"] == sha(HERE / "run_model.py"),
        plan["analysis_sha256"] == sha(HERE / "analyze.py"),
    ]
    for name in ("provider.py", "screen.py", "cases.py", "qa_clean.py"):
        checks.append(plan["runner_sha256"][name] == sha(HERE / name))
    if not all(checks):
        raise RuntimeError("frozen F10-v2 protocol binding differs")
    binding = CAMPAIGN / "runs" / model / "run-binding.json"
    if not binding.is_file():
        raise RuntimeError(f"missing run binding for {model}")
    b = read(binding)
    if b.get("protocol_sha256") != sha(plan_path) or b.get("binary_sha256") != plan["binary_sha256"]:
        raise RuntimeError(f"run binding mismatch for {model}")
    return plan, plan_path


def inventory(model, tasks):
    root = CAMPAIGN / "runs" / model
    completed, failures, missing = 0, 0, []
    for task in tasks:
        for arm in task["arm_order"]:
            trial = root / f"{task['index']:04d}" / task["task_id"] / arm
            result = trial / "result.json"
            if result.is_file():
                completed += 1
                try:
                    if read(result).get("integrity_status") != "PASS":
                        failures += 1
                except Exception:
                    failures += 1
            else:
                missing.append((task, arm, trial))
    return completed, failures, missing


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", choices=MODELS, required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    os.environ["F10_MODEL"] = args.model
    sys.path.insert(0, str(HERE))
    import screen
    import qa_clean

    plan, plan_path = validate(args.model)
    if qa_clean.self_test()["status"] != "PASS":
        raise RuntimeError("QA grader self-test failed")
    key_file = Path("/home/petr/.config/orion-feedback-study/neuraldeep-api-key")
    key = key_file.read_text(encoding="utf-8").strip()
    tasks = [json.loads(line) for line in (CAMPAIGN / "frozen/tasks.jsonl").read_text(encoding="utf-8").splitlines()]
    root = CAMPAIGN / "runs" / args.model
    completed, failures, missing = inventory(args.model, tasks)
    summary = {"model": args.model, "completed_trials": completed,
               "assigned_trials": 1200, "technical_failures": failures,
               "remaining_trials": len(missing), "protocol_sha256": sha(plan_path)}
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    if args.dry_run:
        return 0
    trial_args = SimpleNamespace(binary=ROOT / "bin/orion-fixed-word-boundary-r4",
                                 max_requests=None, provider_timeout=880,
                                 timeout=900, max_tokens=8192, context_window=131072)
    for task, arm, trial in missing:
        if trial.exists():
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            archived = trial.with_name(trial.name + ".interrupted-" + stamp)
            trial.rename(archived)
            print(json.dumps({"archived_interrupted_trial": str(archived)}), flush=True)
        condition = "baseline" if arm == "rich" else "F10_qa_selective"
        result = screen.run_trial(trial_args, qa_clean, task["task_id"], condition, trial, key)
        completed += 1
        failures += int(result.get("integrity_status") != "PASS")
        write(root / "progress.json", {"model": args.model, "completed_trials": completed,
              "assigned_trials": 1200, "technical_failures": failures,
              "last_task_index": task["index"], "protocol_sha256": sha(plan_path)})
        if failures > max(3, int(completed * 0.05)):
            write(root / "technical-hold.json", {"model": args.model,
                  "completed_trials": completed, "technical_failures": failures,
                  "reason": "prespecified greater-than-five-percent integrity gate"})
            return 2
    write(root / "complete.json", {"model": args.model, "completed_trials": completed,
          "technical_failures": failures, "protocol_sha256": sha(plan_path),
          "resumed_without_replacing_completed_trials": True})
    print(json.dumps({"model": args.model, "status": "COMPLETE",
                      "completed_trials": completed, "technical_failures": failures}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
