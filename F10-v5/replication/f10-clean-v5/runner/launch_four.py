"""Start the four frozen model arms concurrently and wait for their exit status."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CAMPAIGN = HERE.parent
MODELS = ("qwen3.6-fp8-noreason", "deepseek-v4.1-flash", "glm-5.3-flash", "gpt-oss-120b")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    plan_path = CAMPAIGN / "campaign-protocol.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if (plan["models"] != list(MODELS) or not (CAMPAIGN / "frozen/freeze-audit.json").is_file() or
            plan["launcher_sha256"] != sha(Path(__file__))):
        raise RuntimeError("four-model protocol binding differs")
    for model in MODELS:
        qualification = CAMPAIGN / "qualification" / model / "qualification-summary.json"
        if not qualification.is_file() or json.loads(qualification.read_text(encoding="utf-8"))["status"] != "PASS":
            raise RuntimeError(f"old-task technical qualification missing: {model}")
    if (CAMPAIGN / "runs").exists():
        raise FileExistsError("clean campaign already has run data; refusing duplicate launch")
    (CAMPAIGN / "runs").mkdir()
    processes = []
    logs = []
    for model in MODELS:
        out = (CAMPAIGN / "runs" / f"{model}.log").open("w", encoding="utf-8")
        logs.append(out)
        proc = subprocess.Popen([sys.executable, str(HERE / "run_model.py"), "--model", model],
                                cwd=HERE, stdout=out, stderr=subprocess.STDOUT)
        processes.append((model, proc))
    results = {}
    for model, proc in processes:
        results[model] = proc.wait()
    for stream in logs:
        stream.close()
    (CAMPAIGN / "runs/launcher-result.json").write_text(json.dumps({
        "protocol_sha256": sha(plan_path), "model_exit_codes": results,
        "all_four_complete": all(code == 0 for code in results.values())}, indent=2) + "\n", encoding="utf-8")
    if not all(code == 0 for code in results.values()):
        return 2
    analysis = subprocess.run([sys.executable, str(HERE / "analyze.py")], cwd=HERE, check=False)
    return analysis.returncode


if __name__ == "__main__":
    raise SystemExit(main())
