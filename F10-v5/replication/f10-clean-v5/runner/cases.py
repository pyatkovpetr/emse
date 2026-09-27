"""Reuse-only fixture adapter. No provider calls; no model answers in seed workspaces.

Public API: list_cases(), materialize(case_id, workspace), grade(case_id, workspace,
model_final). Run with --freeze once to write the pinned manifest, --self-test for
baseline/oracle/scorer validation, or --list for inventory. All graders run on the
host; tests and truth in the manifest must never be advertised as model tools.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import string
import subprocess
import sys
import tempfile
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
MANIFEST = HERE / "cases-manifest.json"
H8_SOURCE = REPO / "research/agent-scaffold-study-2026/h8-outcome-recovery/build_h8_disjoint_cohort.py"
QA_SOURCE = REPO / "research/weak-model-harness-qa-direct-2026/full-benchmark/frozen/c1-smoke.jsonl"
QA_GRADER = QA_SOURCE.parents[1] / "framework_adapter/deterministic_answer_c1.py"
ABI_IDS = ["backend-retry-stable-id", "backend-cumulative-source-state", "messaging-signed-command-outbox"]
NUMBER_RE = re.compile(r"[-+]?(?:\d[\d,]*)(?:\.\d+)?")
CHOICE_RE = re.compile(r"(?<![A-Z0-9])([ABCD])(?![A-Z0-9])", re.IGNORECASE)
NON_COMPLETED_RE = re.compile(r"\A# Task outcome\s*\r?\nStatus:\s*(?:blocked|partial(?:ly completed)?)(?:\s|$)", re.IGNORECASE)


def _digest(data: bytes | str) -> str:
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


def _text(content: str) -> str:
    # Exact source-only write normalization used by the historical H8 builder.
    return content.rstrip() + "\n"


def _node() -> str:
    candidate = os.environ.get("FEEDBACK_NODE") or shutil.which("node")
    if candidate:
        return candidate
    bundled = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
    if bundled.is_file():
        return str(bundled)
    raise RuntimeError("Node.js >=20 unavailable; set FEEDBACK_NODE")


def _abi_root() -> Path:
    return Path(os.environ.get("FEEDBACK_BENCHMARK_ROOT", str(REPO.parent / "benchmark"))).resolve()


def _node_task(root: Path, case_id: str, mode: str, workspace: Path | None = None) -> dict:
    if case_id not in ABI_IDS or mode not in {"seed", "oracle", "grade"}:
        raise ValueError("unrecognized ABI request")
    script = """import {pathToFileURL} from 'node:url';
const task = (await import(pathToFileURL(process.argv[1]).href)).default;
const mode = process.argv[2];
const result = mode === 'grade' ? await task.grade(process.argv[3]) :
  {meta: task.meta, files: mode === 'oracle' ? task.oracle : task.baseline};
process.stdout.write(JSON.stringify(result));"""
    result = subprocess.run(
        [_node(), "--input-type=module", "-e", script, str(root / "tasks" / f"{case_id}.mjs"),
         mode, str(workspace.resolve()) if workspace is not None else ""],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=150,
        env=os.environ.copy(), check=False,
    )
    if result.returncode:
        raise RuntimeError(f"ABI {mode} failed: {result.stderr[-2000:]}")
    return json.loads(result.stdout)


def _load() -> dict:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported case manifest version")
    ids = [case["id"] for case in payload["cases"]]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate existing task identities")
    return payload


def _case(case_id: str) -> dict:
    for item in _load()["cases"]:
        if item["id"] == case_id:
            return item
    raise KeyError(case_id)


def list_cases() -> list[dict]:
    """Available frozen identities only. Truth, tests and seed text are host-private."""
    return [{key: item[key] for key in ("id", "kind", "family", "mechanisms", "provenance", "prompt_sha256")}
            for item in _load()["cases"]]


def _safe_path(workspace: Path, relative: str) -> Path:
    candidate = workspace / relative
    resolved = candidate.resolve()
    if not resolved.is_relative_to(workspace.resolve()) or candidate.is_symlink():
        raise ValueError(f"workspace escape or symlink: {relative}")
    return candidate


def materialize(case_id: str, workspace: Path) -> dict:
    """Write only original baseline files and original task prompt to an empty dir.

    Does not write oracle, answer, hidden tests, grading code or manifest to workspace.
    H8 visible tests are original and are distinct from the authoritative host copy.
    """
    case = _case(case_id)
    workspace = Path(workspace).resolve()
    if workspace.exists() and any(workspace.iterdir()):
        raise FileExistsError(f"refusing to overwrite nonempty workspace: {workspace}")
    workspace.mkdir(parents=True, exist_ok=True)
    for relative, content in case["files"].items():
        target = _safe_path(workspace, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content.encode("utf-8"))
    prompt = case["prompt"]
    platform_adjustments = []
    if case["family"] == "h8" and os.name == "nt":
        prompt = prompt.replace("python3 -m unittest", "python -m unittest")
        platform_adjustments.append({"from": "python3 -m unittest", "to": "python -m unittest",
                                     "reason": "Windows interpreter executable alias only; same suite and task"})
    (workspace / "TASK.md").write_bytes(prompt.encode("utf-8"))
    config = {"case_family": case["family"], "network_required": False,
              "editable_files": case["editable_files"], "mechanism_exposure": "must_be_measured_from_runtime_events",
              "platform_adjustments": platform_adjustments}
    if case["family"] == "h8":
        config.update({"required_initial_command": ("python" if os.name == "nt" else "python3") + " -m unittest discover -s tests -v",
                       "python3_host_interpreter": sys.executable,
                       "runtime_note": "runner must make the existing python3 command resolvable"})
    elif case["family"] == "stable-abi":
        config["runtime_requirements"] = ["Node.js >=20", "javac", "java"]
    return {"id": case_id, "kind": case["kind"], "prompt": prompt,
            "config": config, "mechanisms": case["mechanisms"],
            "grader_inputs": {"case_id": case_id, "workspace": str(workspace)},
            "prompt_sha256": _digest(prompt), "original_prompt_sha256": case["prompt_sha256"],
            "manifest_sha256": _digest(MANIFEST.read_bytes())}


def _final_response(model_final: Any) -> tuple[str, bool | None]:
    """Unwrap only an explicit complete JSON envelope, never extract from prose."""
    value = model_final
    if isinstance(value, str):
        if NON_COMPLETED_RE.search(value.strip()):
            return "", False
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict) and "response" in parsed:
                value = parsed
        except (ValueError, TypeError):
            pass
    if isinstance(value, dict):
        status = value.get("status")
        if status in {"blocked", "partial", "failed"}:
            return "", False
        response = value.get("response", value.get("final_output", ""))
        return response if isinstance(response, str) else "", True if status == "completed" else None
    return value if isinstance(value, str) else "", None


def _normalize_squad(text: str) -> str:
    text = "".join(ch for ch in text.lower() if ch not in set(string.punctuation))
    return " ".join(re.sub(r"\b(a|an|the)\b", " ", text).split())


def _squad_scores(prediction: str, answers: list[str]) -> tuple[float, float]:
    pred = _normalize_squad(prediction)
    em, f1 = 0.0, 0.0
    for answer in answers:
        gold = _normalize_squad(answer)
        em = max(em, float(pred == gold))
        p, a = pred.split(), gold.split()
        common = sum((Counter(p) & Counter(a)).values())
        candidate = float(p == a) if not p or not a else (2 * common / (len(p) + len(a)) if common else 0.0)
        f1 = max(f1, candidate)
    return em, f1


def _decimal(text: str) -> Decimal | None:
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None


def _grade_qa(case: dict, final: Any) -> dict:
    output, completed = _final_response(final)
    output = output.strip()
    result = {"correctness": False, "evaluable": bool(output), "score": 0.0,
              "terminal_completed": completed, "grader_status": "ok",
              "response_sha256": _digest(output), "evaluator": "deterministic_answer_c1_v2_host_envelope_fix",
              "response_format_valid": False}
    if not output:
        return result
    dataset, target = case["dataset"], case["target"]
    if dataset == "gsm8k":
        matches = NUMBER_RE.findall(output)
        predicted = _decimal(matches[-1]) if matches else None
        passed = predicted is not None and predicted == _decimal(target["canonical_number"])
        result.update({"predicted_canonical": str(predicted) if predicted is not None else None,
                       "response_format_valid": NUMBER_RE.fullmatch(output) is not None,
                       "evaluable": predicted is not None})
    elif dataset == "mmlu":
        choices = {match.upper() for match in CHOICE_RE.findall(output)}
        predicted = next(iter(choices)) if len(choices) == 1 else None
        passed = predicted == target["choice"]
        result.update({"predicted_choice": predicted, "evaluable": predicted is not None,
                       "response_format_valid": output in {"A", "B", "C", "D"}})
    elif dataset == "squad2":
        em, f1 = _squad_scores(output, target["answers"])
        passed = em == 1.0
        result.update({"exact_match": em, "token_f1": f1,
                       "response_format_valid": "\n" not in output})
    else:
        raise ValueError(f"unsupported QA dataset: {dataset}")
    result.update({"correctness": passed, "score": float(passed)})
    return result


def _grade_h8(case: dict, workspace: Path, final: Any) -> dict:
    source = _safe_path(workspace, "solution.py")
    if not source.is_file():
        return {"correctness": False, "evaluable": False, "artifact_pass": False,
                "grader_status": "ok", "reason": "solution.py missing", "score": 0.0}
    actual_tests = _safe_path(workspace, "tests/test_solution.py")
    tests_intact = actual_tests.is_file() and _digest(actual_tests.read_bytes()) == _digest(case["grader"]["tests"])
    # The authoritative suite is reconstructed outside the model workspace.
    with tempfile.TemporaryDirectory(prefix="feedback-h8-grade-") as temporary:
        target = Path(temporary)
        (target / "solution.py").write_bytes(source.read_bytes())
        (target / "test_solution.py").write_bytes(case["grader"]["tests"].encode("utf-8"))
        command = "import sys,unittest; sys.path.insert(0,sys.argv[1]); s=unittest.defaultTestLoader.discover(sys.argv[1],pattern='test_solution.py'); r=unittest.TextTestRunner(verbosity=2).run(s); sys.exit(0 if r.testsRun>0 and r.wasSuccessful() else 1)"
        try:
            process = subprocess.run([sys.executable, "-I", "-B", "-c", command, str(target)],
                                     cwd=target, capture_output=True, text=True, encoding="utf-8",
                                     errors="replace", timeout=20, check=False)
        except subprocess.TimeoutExpired:
            return {"correctness": False, "evaluable": True, "artifact_pass": False,
                    "grader_status": "ok", "reason": "candidate grading timeout", "score": 0.0,
                    "tests_unchanged": tests_intact}
    artifact_pass = process.returncode == 0
    output = process.stdout + process.stderr
    return {"correctness": artifact_pass and tests_intact, "artifact_pass": artifact_pass,
            "evaluable": True, "score": float(artifact_pass and tests_intact),
            "terminal_completed": _final_response(final)[1], "grader_status": "ok",
            "tests_unchanged": tests_intact, "grader_returncode": process.returncode,
            "output_tail": "\n".join(output.splitlines()[-30:]),
            "solution_sha256": _digest(source.read_bytes())}


def _grade_abi(case: dict, workspace: Path, final: Any) -> dict:
    root = _abi_root()
    for relative, expected in case["grader"]["source_hashes"].items():
        path = root / relative
        if not path.is_file() or _digest(path.read_bytes()) != expected:
            return {"correctness": None, "evaluable": False, "grader_status": "infra_invalid",
                    "reason": f"missing or changed original ABI grading dependency: {relative}"}
    if not shutil.which("javac") or not shutil.which("java"):
        return {"correctness": None, "evaluable": False, "grader_status": "infra_invalid",
                "reason": "javac/java unavailable on grader PATH"}
    try:
        raw = _node_task(root, case["id"], "grade", workspace)
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return {"correctness": None, "evaluable": False, "grader_status": "infra_invalid",
                "reason": str(exc)}
    passed = raw["status"] == "passed"
    return {"correctness": passed, "artifact_pass": passed, "evaluable": True,
            "score": raw["score"] / raw["max_score"], "strict_score": raw["score"],
            "strict_status": raw["status"], "terminal_completed": _final_response(final)[1],
            "grader_status": "ok", "checks": raw["checks"], "execution": raw["execution"]}


def grade(case_id: str, workspace: Path, model_final: Any) -> dict:
    """Score artifact/answer independently of whether the agent ended before timeout.

    Caller must combine runtime facts separately; never infer autonomous success
    from this score. It must also measure actual hypothesis mechanism exposure.
    """
    case, workspace = _case(case_id), Path(workspace).resolve()
    if case["family"] == "qa-terminal":
        result = _grade_qa(case, model_final)
    elif case["family"] == "h8":
        result = _grade_h8(case, workspace, model_final)
    elif case["family"] == "stable-abi":
        result = _grade_abi(case, workspace, model_final)
    else:
        raise ValueError(case["family"])
    return {"case_id": case_id, "mechanisms": case["mechanisms"],
            "exposure": "not_measured_by_grader", **result}


def _h8_originals() -> tuple[str, list[dict]]:
    values = {}
    for node in ast.parse(H8_SOURCE.read_text(encoding="utf-8")).body:
        name = node.target.id if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) else None
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
        if name in {"COMMON_TASK", "CASES"}:
            values[name] = ast.literal_eval(node.value)
    return values["COMMON_TASK"], values["CASES"]


def _freeze() -> None:
    if MANIFEST.exists():
        raise FileExistsError("manifest already frozen; do not overwrite it")
    common, originals = _h8_originals()
    cases, unavailable = [], []
    for original in originals:
        prompt = _text(common.format(spec=original["spec"]))
        files = {"solution.py": _text(original["baseline"]),
                 "tests/test_solution.py": _text(original["tests"])}
        cases.append({"id": original["id"], "kind": "python", "family": "h8",
                      "mechanisms": ["F1", "F2", "F4"], "prompt": prompt,
                      "prompt_sha256": _digest(prompt), "files": files,
                      "editable_files": ["solution.py"], "grader": {"tests": files["tests/test_solution.py"]},
                      "provenance": {"source": H8_SOURCE.relative_to(REPO).as_posix(),
                                     "source_sha256": _digest(H8_SOURCE.read_bytes()),
                                     "baseline_sha256": _digest(files["solution.py"]),
                                     "tests_sha256": _digest(files["tests/test_solution.py"]),
                                     "source_kind": "original_builder_literals_not_solved_run"}})
    root = _abi_root()
    for case_id in ABI_IDS:
        try:
            sources = [f"tasks/{case_id}.mjs", "tasks/helpers.mjs", "src/grading.mjs", "src/utils.mjs"]
            hashes = {path: _digest((root / path).read_bytes()) for path in sources}
            seed = _node_task(root, case_id, "seed")
            prompt = seed["meta"]["prompt"]
            cases.append({"id": case_id, "kind": "java", "family": "stable-abi",
                          "mechanisms": ["F1", "F4"], "prompt": prompt,
                          "prompt_sha256": _digest(prompt), "files": seed["files"],
                          "editable_files": seed["meta"]["editable_files"],
                          "grader": {"source_hashes": hashes},
                          "provenance": {"source_root": str(root), "source_hashes": hashes,
                                         "source_kind": "original_strict_benchmark_task_baseline"}})
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
            unavailable.append({"id": case_id, "reason": str(exc)})
    rows = [json.loads(line) for line in QA_SOURCE.read_text(encoding="utf-8").splitlines() if line]
    for dataset in ("gsm8k", "mmlu", "squad2"):
        for row in [item for item in rows if item["dataset"] == dataset][:2]:
            prompt = row["prompt"]
            cases.append({"id": row["task_id"], "kind": "qa", "family": "qa-terminal",
                          "mechanisms": ["F10"], "prompt": prompt, "prompt_sha256": _digest(prompt),
                          "files": {}, "editable_files": [], "dataset": dataset, "target": row["target"],
                          "provenance": {"source": QA_SOURCE.relative_to(REPO).as_posix(),
                                         "source_sha256": _digest(QA_SOURCE.read_bytes()),
                                         "row_sha256": _digest(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))),
                                         "selection_hash": row["selection_hash"], "upstream_task_id": row["upstream_task_id"],
                                         "reference_grader": QA_GRADER.relative_to(REPO).as_posix(),
                                         "reference_grader_sha256": _digest(QA_GRADER.read_bytes()),
                                         "source_kind": "existing_frozen_c1_smoke_not_confirmatory"}})
    payload = {"schema_version": 1, "reuse_only": True, "new_task_identities": 0,
               "pool_role": "development_screening", "case_count": len(cases),
               "selection": "all original H8 eight; all existing ABI three if available; first two per dataset in existing C1 smoke file order",
               "mechanism_tags_are": "eligibility_only_not_observed_exposure",
               "unavailable_cases": unavailable,
               "unavailable_mechanisms": {
                   "F5": "No genuine harness history-compaction exposure guaranteed; H8 compact_history is only the program-under-test.",
                   "F7": "No authorized policy-denial scenario in these originals.",
                   "F11": "These originals are single-turn tasks without user-correction sequence."},
               "cases": cases}
    MANIFEST.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _self_test() -> dict:
    _, originals = _h8_originals()
    oracles = {case["id"]: case["oracle"] for case in originals}
    results = []
    for case in _load()["cases"]:
        with tempfile.TemporaryDirectory(prefix="feedback-adapter-check-") as temporary:
            workspace = Path(temporary)
            materialize(case["id"], workspace)
            if case["family"] == "qa-terminal":
                target = case["target"]
                answer = target.get("canonical_number", target.get("choice", (target.get("answers") or [""])[0]))
                correct = grade(case["id"], workspace, {"status": "completed", "response": answer})
                blocked = grade(case["id"], workspace, {"status": "blocked", "response": answer})
                empty = grade(case["id"], workspace, "")
                wrapper = grade(case["id"], workspace, "# Task outcome\nStatus: partially completed\n\nA service report, not an answer.\n" + answer)
                assert correct["correctness"] and not blocked["evaluable"] and not empty["evaluable"] and not wrapper["evaluable"]
                results.append({"id": case["id"], "known_truth_pass": True, "blocked_not_evaluable": True})
                continue
            baseline = grade(case["id"], workspace, "")
            assert baseline["grader_status"] == "ok", baseline
            assert baseline["correctness"] is False, baseline
            if case["family"] == "h8":
                (workspace / "solution.py").write_text(_text(oracles[case["id"]]), encoding="utf-8")
            else:
                oracle = _node_task(_abi_root(), case["id"], "oracle")["files"]
                for path, content in oracle.items():
                    (workspace / path).write_text(content, encoding="utf-8")
            passed = grade(case["id"], workspace, {"status": "completed", "response": "done"})
            assert passed["correctness"], passed
            if case["family"] == "h8":
                (workspace / "tests/test_solution.py").write_text("# tampered\n", encoding="utf-8")
                tampered = grade(case["id"], workspace, "done")
                assert tampered["artifact_pass"] and not tampered["correctness"]
            results.append({"id": case["id"], "baseline_fails": True, "oracle_passes": True,
                            "baseline_score": baseline["score"]})
    return {"passed": True, "cases_checked": len(results), "results": results,
            "manifest_sha256": _digest(MANIFEST.read_bytes())}


def regrade_run(run_root: Path) -> dict:
    """Regrade only completed QA batches; preserve every original result.json.

    Writes hash-bound grade-override.json records, never silently changes the
    immutable raw result. The analyzer verifies the binding before applying.
    """
    run_root = run_root.resolve()
    status_path = run_root / "status.json"
    status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.is_file() else {}
    if status.get("status") != "COMPLETE":
        raise RuntimeError("refusing to regrade an incomplete/running batch")
    known = {item["id"]: item for item in _load()["cases"] if item["family"] == "qa-terminal"}
    corrected = []
    for path in sorted(run_root.glob("r*/*/*/result.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("case_id") not in known:
            continue
        final = raw.get("response") or (path.parent / "stdout.txt").read_text(encoding="utf-8")
        new_grade = grade(raw["case_id"], path.parent / "workspace", final)
        old_grade = raw.get("grade", {})
        changed = any(old_grade.get(key) != new_grade.get(key) for key in ("correctness", "evaluable", "score"))
        if not changed:
            continue
        payload = {"schema": "feedback-grade-override-v1", "result_sha256": _digest(path.read_bytes()),
                   "grader_sha256": _digest(Path(__file__).read_bytes()), "manifest_sha256": _digest(MANIFEST.read_bytes()),
                   "reason": "Classify Orion 'Status: partially completed' host envelope before QA answer extraction; task, response bytes and runtime remain unchanged.",
                   "original_grade": old_grade, "corrected_grade": new_grade,
                   "artifact_correct": bool(new_grade["correctness"]),
                   "success_by_deadline": bool(new_grade["correctness"]) and raw.get("completion_accepted") is True and not (raw.get("timed_out") or raw.get("request_budget_exhausted")),
                   "raw_artifacts_preserved": True}
        destination = path.parent / "grade-override.json"
        if destination.exists():
            existing = json.loads(destination.read_text(encoding="utf-8"))
            if existing != payload:
                raise RuntimeError(f"refusing to overwrite a different grade override: {destination}")
        else:
            destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        corrected.append({"case_id": raw["case_id"], "condition": raw["condition"], "override": str(destination)})
    return {"run_root": str(run_root), "corrected_trials": corrected, "model_calls": 0, "raw_artifacts_preserved": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--freeze", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--regrade-run", type=Path)
    args = parser.parse_args()
    if args.freeze:
        _freeze()
    if args.regrade_run:
        print(json.dumps(regrade_run(args.regrade_run), ensure_ascii=False, indent=2))
    elif args.self_test:
        print(json.dumps(_self_test(), ensure_ascii=False, indent=2))
    elif args.list:
        print(json.dumps(list_cases(), ensure_ascii=False, indent=2))
