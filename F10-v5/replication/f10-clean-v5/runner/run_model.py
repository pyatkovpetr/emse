"""Run one frozen F10 clean cohort for one prespecified model, without peeking."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
CAMPAIGN = HERE.parent
ROOT = CAMPAIGN.parent
MODELS = ("qwen3.6-fp8-noreason", "deepseek-v4.1-flash", "glm-5.3-flash", "gpt-oss-120b")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=MODELS, required=True)
    parser.add_argument("--qualification-only", action="store_true")
    args = parser.parse_args()
    os.environ["F10_MODEL"] = args.model
    sys.path.insert(0, str(HERE))
    import screen
    import qa_clean

    plan_path = CAMPAIGN / "campaign-protocol.json"
    plan = read(plan_path)
    if (plan["models"] != list(MODELS) or plan["task_count"] != 600 or
            plan["task_wall_seconds"] != 900 or plan["global_hard_request_cap"] is not None or
            plan["global_hard_token_cap"] is not None or
            plan["clean_tasks_sha256"] != sha(CAMPAIGN / "frozen/tasks.jsonl") or
            plan["clean_answers_sha256"] != sha(CAMPAIGN / "frozen/answers.jsonl") or
            plan["binary_sha256"] != sha(ROOT / "bin/orion-fixed-word-boundary-r4") or
            plan["orchestrator_sha256"] != sha(HERE / "run_model.py") or
            plan["analysis_sha256"] != sha(HERE / "analyze.py") or
            any(plan["runner_sha256"][name] != sha(HERE / name)
                for name in ("provider.py", "screen.py", "cases.py", "qa_clean.py"))):
        raise RuntimeError("frozen F10 clean protocol differs")
    if qa_clean.self_test()["status"] != "PASS":
        raise RuntimeError("QA grader controls failed")
    key_file = Path("/home/petr/.config/orion-feedback-study/neuraldeep-api-key")
    key = key_file.read_text(encoding="utf-8").strip()
    tasks = [json.loads(line) for line in (CAMPAIGN / "frozen/tasks.jsonl").read_text(encoding="utf-8").splitlines()]
    if args.qualification_only:
        raise RuntimeError("qualification uses a separate old-pool script; clean task runner cannot qualify")
    root = CAMPAIGN / "runs" / args.model
    root.mkdir(parents=True, exist_ok=False)
    write(root / "run-binding.json", {"model": args.model, "protocol_sha256": sha(plan_path),
                                      "task_manifest_sha256": plan["clean_tasks_sha256"],
                                      "binary_sha256": plan["binary_sha256"],
                                      "task_count": len(tasks), "trial_count": 2 * len(tasks),
                                      "clean_outcomes_opened": True})
    trial_args = SimpleNamespace(binary=ROOT / "bin/orion-fixed-word-boundary-r4",
                                 max_requests=None, provider_timeout=880,
                                 timeout=900, max_tokens=8192, context_window=131072)
    technical_failures = 0
    completed = 0
    for row in tasks:
        case_id = row["task_id"]
        for arm in row["arm_order"]:
            condition = "baseline" if arm == "rich" else "F10_qa_selective"
            trial = root / f"{row['index']:04d}" / case_id / arm
            result = screen.run_trial(trial_args, qa_clean, case_id, condition, trial, key)
            completed += 1
            technical_failures += result["integrity_status"] != "PASS"
            write(root / "progress.json", {"model": args.model, "completed_trials": completed,
                                           "assigned_trials": 1200,
                                           "technical_failures": technical_failures,
                                           "last_task_index": row["index"],
                                           "protocol_sha256": sha(plan_path)})
            # Systematic transport failure invalidates the frozen campaign;
            # preserve all clean results and stop without replacing tasks.
            if technical_failures > max(3, int(completed * 0.05)):
                write(root / "technical-hold.json", {"model": args.model,
                                                       "completed_trials": completed,
                                                       "technical_failures": technical_failures,
                                                       "reason": "prespecified greater-than-five-percent integrity gate"})
                return 2
    write(root / "complete.json", {"model": args.model, "completed_trials": completed,
                                    "technical_failures": technical_failures,
                                    "protocol_sha256": sha(plan_path)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
