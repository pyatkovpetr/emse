"""Paired development screening on existing task fixtures, through current Orion.

No confirmatory claims. Each invocation has immutable config/binary/case hashes.
Only one Orion trial and one model request are active at a time. The supplied
credential remains in the supervisor; Orion talks to an ephemeral loopback proxy.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib
import json
import os
import re
import signal
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

from provider import Gateway, MODEL, ENDPOINT, write_json

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PYTHON_DIR = Path(sys.executable).parent

CONDITIONS = {
    "baseline": {},
    "F1_legacy_state_error": {"legacy-tool-state-errors": True},
    "F2_recovery": {"outcome-aware-recovery": True},
    "F2_generic": {"outcome-aware-recovery": True, "criterion-targeted-recovery": False},
    "F2_criterion_targeted": {"outcome-aware-recovery": True, "criterion-targeted-recovery": True},
    "F4_progress": {"evidence-progress-budget": True},
    "F4_generic": {"evidence-progress-budget": True, "outcome-aware-recovery": True, "criterion-targeted-recovery": False},
    "F4_targeted": {"evidence-progress-budget": True, "outcome-aware-recovery": True, "criterion-targeted-recovery": True},
    "F4_cycle_generic": {"evidence-progress-budget": True, "outcome-aware-recovery": True, "criterion-targeted-recovery": False, "cycle-targeted-recovery": False},
    "F4_cycle_targeted": {"evidence-progress-budget": True, "outcome-aware-recovery": True, "criterion-targeted-recovery": False, "cycle-targeted-recovery": True},
    "F4_adaptive_planner": {"planner-mode": "adaptive"},
    "F5_history": {"evidence-aware-history": True},
    "F6_no_observation": {"disable-tool-state-observation": True},
    "F6_dynamic_schema": {"stable-tool-abi": False},
    "F6_stable_observed": {"stable-tool-abi": True, "disable-tool-state-observation": False},
    "F6_stable_unobserved": {"stable-tool-abi": True, "disable-tool-state-observation": True},
    "F6_dynamic_observed": {"stable-tool-abi": False, "observe-dynamic-tool-state": True, "disable-tool-state-observation": False},
    "F6_dynamic_unobserved": {"stable-tool-abi": False, "observe-dynamic-tool-state": False, "disable-tool-state-observation": True},
    "F7_block_denial": {"legacy-policy-denial-blocking": True},
    "F10_qa_direct": {"completion-contract": "qa-direct"},
    "F10_qa_selective": {},
    "F10_compact": {"completion-contract": "compact"},
    "F10_terminal_cap1": {"terminal-attempt-cap": 1},
    "H0_direct_terminal": {"direct-terminal-path": True},
    "H2_minimal_contract": {"minimal-task-contract": True},
    "H4_read_bundle": {"read-bundle": True},
    "H5_no_prefix_cache": {"stable-prompt-prefix": False},
    "H_parallel_reads": {"parallel-read-tools": True},
    "H_planner_always": {"planner-mode": "always"},
    "F11_no_ledger": {"task-ledger": False},
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def file_hashes(root):
    return {p.relative_to(root).as_posix(): sha(p) for p in sorted(root.rglob("*")) if p.is_file() and ".git" not in p.parts and "__pycache__" not in p.parts}


def session_metrics(home):
    meta, events, messages = {}, [], []
    for path in sorted((home / "sessions").glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("type") == "meta":
                meta = rec.get("data", {})
            elif rec.get("type") == "message":
                messages.append(rec.get("data", {}))
            else:
                events.append(rec.get("data", {}))
    texts = [m.get("content", "") for m in messages if m.get("role") == "tool"]
    all_text = "\n".join(texts)
    host_errors = "\n".join(t.split("\nRESULT\n", 1)[-1] for t in texts
                            if t.split("\nRESULT\n", 1)[-1].startswith("ERROR:"))
    outcome = meta.get("outcome") or {}
    tools = Counter()
    negative = 0
    for text in texts:
        found = re.match(r"CALL ([\w.-]+)", text)
        if found:
            tools[found.group(1)] += 1
        negative += int(bool(re.search(r"(?im)(?:^|\n)(?:ERROR:|result: FAIL)|policy_denied:|not executable in the current harness state|was not advertised", text)))
    return {
        "session_id": meta.get("sessionId"), "session_status": meta.get("status"),
        "completion_status": outcome.get("status"), "completion_reason": outcome.get("reason"),
        "completion_accepted": outcome.get("status") == "completed",
        "response": outcome.get("response") or "",
        "tool_calls": dict(tools), "negative_tool_results": negative,
        "controller_state_rejections": len(re.findall(r"not executable in the current harness state|was not advertised", all_text)),
        "policy_denial_mentions": all_text.count("policy_denied:"),
        "context_compactions": meta.get("contextCompactions", 0),
        "event_types": dict(Counter(e.get("type", "unknown") for e in events)),
        "tool_infrastructure_errors": [token for token in ("no trusted executable", "environment_no_verifier", "Access is denied") if token in host_errors],
    }


def provider_attempts():
    # Frozen v5 policy: exactly five bounded provider attempts for transient failures.
    return "5"

def build_env(config_path):
    clean = {k: v for k, v in os.environ.items() if not k.upper().startswith(("ORION_", "OPENAI_", "DEEPSEEK_", "KONTUR_"))}
    clean.update(ORION_API_KEY="loopback-no-provider-credential", ORION_CONFIG=str(config_path),
                 ORION_PROVIDER_MAX_ATTEMPTS=provider_attempts(), ORION_PROVIDER_ATTEMPT_TIMEOUT_SECONDS="880",
                 ORION_PROVIDER_RECOVERY_TIMEOUT_SECONDS="890", ORION_HTTP_TIMEOUT_SECONDS="885",
                 ORION_AGENT_RUN_TIMEOUT_SECONDS="0", PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    clean["PATH"] = str(PYTHON_DIR) + os.pathsep + clean.get("PATH", "")
    return clean


def run_trial(args, adapter, case_id, condition, trial, key):
    trial.mkdir(parents=True, exist_ok=False)
    workspace, home = trial / "workspace", trial / "home"
    supplied = adapter.materialize(case_id, workspace)
    prompt = supplied["prompt"]
    if supplied.get("config", {}).get("required_initial_command"):
        # The old fenced command was classified as diagnostic; current Orion
        # needs an explicit inline verifier binding. Keep TASK.md and all
        # original task/test bytes intact and apply identically to every arm.
        binding = "Host fixture verifier binding: `" + supplied["config"]["required_initial_command"] + "`."
        prompt += "\n\n" + binding
        supplied["runtime_prompt_annotation"] = binding
        supplied["runtime_prompt_annotation_qualification"] = "live/qualification/binding-r2/result.json"
    supplied["runtime_prompt_sha256"] = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    (trial / "prompt.txt").write_text(prompt, encoding="utf-8")
    write_json(trial / "materialization.json", supplied)
    initial_hashes = file_hashes(workspace)
    write_json(trial / "initial-hashes.json", initial_hashes)
    cfg_path = trial / "config.json"
    config = dict(supplied.get("orion_config", {}))
    if supplied.get("config", {}).get("allowed_tools"):
        config["allowedTools"] = supplied["config"]["allowed_tools"]
    write_json(cfg_path, config)
    neutral_paths = {str(workspace.resolve()): "/workspace", str(home.resolve()): "/orion-home"}
    gateway = Gateway(key, trial / "transport", args.max_requests, args.provider_timeout,
                      neutral_paths=neutral_paths).start()
    flags = {
        "agent": "general", "skills": "none", "task-mode": "auto", "response-mode": "direct",
        "approval": "auto", "tool-profile": "default", "max-tokens-per-step": args.max_tokens,
        "context-window": args.context_window, "agent-controller-v2": True, "agent-controller-mode": "strict",
        "executor-validation": True, "write-gates": True, "tools": True, "allow-shell": True,
        "stream": False, "thinking": False, "memory": False, "project-memory": False,
        "task-ledger": True, "completion-contract": "rich", "terminal-attempt-cap": 3,
        "direct-terminal-path": False, "evidence-aware-history": False, "minimal-task-contract": False,
        "evidence-progress-budget": False, "read-bundle": False, "stable-prompt-prefix": True,
        "stable-tool-abi": True, "outcome-aware-recovery": False,
        "legacy-tool-state-errors": False, "legacy-policy-denial-blocking": False,
        "disable-named-transition-tool-choice": False, "planner-mode": "off", "parallel-read-tools": False,
        "workspace-path-aliases": "/workspace", "shell-allowlist": "python*,python3*,pytest*,java*,javac*,mvn*",
    }
    flags.update(supplied.get("flags", {}))
    flags.update(supplied.get("config", {}).get("required_runtime_flags", {}))
    editable = supplied.get("config", {}).get("editable_files")
    if editable:
        flags["workspace-write-paths"] = ",".join(editable)
    if supplied.get("kind") == "qa":
        flags.update({"allow-shell": False, "exact-tool-surface": True})
        config["allowedTools"] = ["task_complete"]
        write_json(cfg_path, config)
    flags.update(CONDITIONS[condition])
    if condition == "F10_qa_selective":
        if supplied.get("kind") != "qa" or not case_id.startswith(("gsm8k_", "mmlu_", "squad2_")):
            raise ValueError("selective QA route requires a registered QA source family")
        flags["completion-contract"] = "rich" if case_id.startswith("gsm8k_") else "qa-direct"
    command = [str(args.binary.resolve()), "run", "--home", str(home), "--cwd", str(workspace), "--base-url", gateway.url, "--model", MODEL]
    command += [f"--{name}={str(value).lower() if isinstance(value, bool) else value}" for name, value in flags.items()]
    command.append("-")
    write_json(trial / "execution.json", {"binary_sha256": sha(args.binary), "flags": flags,
        "selective_route_source_family": "GSM8K-rich/MMLU-SQuAD2-direct" if condition == "F10_qa_selective" else None,
        "max_requests": args.max_requests, "external_timeout_seconds": args.timeout,
        "max_tokens_per_generation": args.max_tokens, "endpoint": ENDPOINT, "model": MODEL,
        "credential_passed_to_orion": False, "provider_max_attempts": int(provider_attempts()),
        "gateway_protocol": "non-streaming; enable_thinking=false; serial trials"})
    write_json(trial / "context-normalization.json", {"neutral_paths": neutral_paths,
        "original_request": "transport/request-*/orion-request.json",
        "forwarded_request": "transport/request-*/request.json",
        "response_rewriting": False, "evidence_id_rewriting": False})
    started = time.monotonic()
    timed_out, request_cap = False, False
    with (trial / "stdout.txt").open("w", encoding="utf-8") as out, (trial / "stderr.txt").open("w", encoding="utf-8") as err:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=out, stderr=err,
            env=build_env(cfg_path), text=True, encoding="utf-8", start_new_session=True)
        process.stdin.write(prompt)
        process.stdin.close()
        while process.poll() is None:
            elapsed = time.monotonic()-started
            if elapsed >= args.timeout or gateway.limit_reached:
                timed_out = elapsed >= args.timeout
                request_cap = gateway.limit_reached
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
                break
            time.sleep(0.2)
    agent_wall = time.monotonic()-started
    gateway.close()
    # Orion can consume the cap response and exit between supervisor polls.
    # The gateway's authoritative flag survives that race.
    request_cap = request_cap or gateway.limit_reached
    metrics = session_metrics(home)
    final = metrics["response"] or (trial / "stdout.txt").read_text(encoding="utf-8")
    grade_started = time.monotonic()
    grade = adapter.grade(case_id, workspace, final)
    grade_seconds = time.monotonic()-grade_started
    artifact_correct = bool(grade.get("correctness", grade.get("artifact_correct", grade.get("correct", grade.get("passed", False)))))
    usage_records = [r.get("usage", {}) for r in gateway.receipts if r.get("status") == "COMPLETE"]
    after = file_hashes(workspace)
    changed = [p for p in sorted(set(initial_hashes) | set(after)) if initial_hashes.get(p) != after.get(p)]
    stderr = (trial / "stderr.txt").read_text(encoding="utf-8")
    known_infra = [x for x in ["Access is denied", "no trusted executable", "trusted inference-budget finish failed", "provider recovery exhausted"] if x in stderr]
    # A supervisor-enforced cap is an observed unsuccessful trial, not a
    # provider outage. Preserve the raw stderr while distinguishing its cause.
    if request_cap and "RESEARCH_REQUEST_BUDGET_EXHAUSTED" in stderr:
        known_infra = [x for x in known_infra if x != "provider recovery exhausted"]
    result = {
        "kind": "DEVELOPMENT", "case_id": case_id, "condition": condition,
        "binary_sha256": sha(args.binary), "grade": grade, "artifact_correct": artifact_correct,
        "agent_returncode": process.returncode, "timed_out": timed_out, "request_budget_exhausted": request_cap,
        "agent_wall_sec": round(agent_wall, 3), "grader_wall_sec": round(grade_seconds, 3),
        "external_requests": gateway.requests, "complete_transport_receipts": len(usage_records),
        "prompt_tokens": sum(r.get("prompt_tokens", 0) for r in usage_records),
        "completion_tokens": sum(r.get("completion_tokens", 0) for r in usage_records),
        "cached_tokens": sum(r.get("prompt_tokens_details", {}).get("cached_tokens", 0) for r in usage_records),
        "usage_complete": len(usage_records) == gateway.requests and all("prompt_tokens" in r for r in usage_records),
        "provider_failures": sum(r.get("status") != "COMPLETE" for r in gateway.receipts),
        "known_infrastructure_errors": known_infra, "changed_files": changed,
        "unplanned_human_interventions": 0, **metrics,
    }
    result["success_by_deadline"] = artifact_correct and metrics["completion_accepted"] and not (timed_out or request_cap)
    result["integrity_status"] = "FAIL" if known_infra or metrics["tool_infrastructure_errors"] or result["provider_failures"] or not result["usage_complete"] or grade.get("grader_status") == "infra_invalid" else "PASS"
    # Safety is not inferred from correctness; each task-specific grader must
    # record its measured restrictions. Do not call success_by_deadline "safe".
    write_json(trial / "result.json", result)
    print(json.dumps({k: result[k] for k in ("case_id", "condition", "agent_wall_sec", "external_requests", "integrity_status")}), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter", default="cases")
    parser.add_argument("--cases", required=True)
    parser.add_argument("--conditions", default="baseline,F2_recovery")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--binary", type=Path, default=HERE.parent / ".tmp-harness-audit/orion-f6.exe")
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--provider-timeout", type=float, default=120)
    parser.add_argument("--max-requests", type=int, default=24)
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--context-window", type=int, default=262144)
    parser.add_argument("--protocol-file", type=Path)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--campaign-max-requests", type=int, required=True)
    parser.add_argument("--campaign-max-logical-tokens", type=int, required=True)
    parser.add_argument("--resume-bookkeeping-reason", help="Resume completed trials only; allows the documented cap-accounting race fix, no model/runtime changes")
    args = parser.parse_args()
    adapter = importlib.import_module(args.adapter)
    cases, conditions = args.cases.split(","), args.conditions.split(",")
    assert all(c in CONDITIONS for c in conditions)
    run_root = HERE / "runs" / args.run_id
    run_root.mkdir(parents=True, exist_ok=bool(args.resume_bookkeeping_reason))
    source_files = [p for p in HERE.glob("*.py")]
    manifest = {"schema": "orion-reused-development-screen-v1", "run_id": args.run_id, "adapter": args.adapter, "cases": cases,
                "conditions": conditions, "repeat": args.repeat, "binary_sha256": sha(args.binary),
                "source_sha256": {p.name: sha(p) for p in source_files}, "model": MODEL, "endpoint": ENDPOINT,
                "timeout": args.timeout, "max_requests": args.max_requests, "max_tokens": args.max_tokens,
                "context_window": args.context_window,
                "revision_protocol_sha256": sha(args.protocol_file) if args.protocol_file else None,
                "campaign_max_requests": args.campaign_max_requests,
                "campaign_max_logical_tokens": args.campaign_max_logical_tokens,
                "order": "alternate/reverse conditions by case and repetition; no old baseline reuse",
                "status": "STARTED", "new_task_identities": 0}
    if args.resume_bookkeeping_reason:
        original = json.loads((run_root / "manifest.json").read_text(encoding="utf-8"))
        for field in ("cases", "conditions", "repeat", "binary_sha256", "model", "endpoint", "timeout", "max_requests", "max_tokens", "context_window", "campaign_max_requests", "campaign_max_logical_tokens"):
            if original[field] != manifest[field]:
                raise ValueError("resume would change frozen configuration: " + field)
        if original.get("adapter", args.adapter) != args.adapter:
            raise ValueError("resume would change frozen adapter")
        for name in (Path(adapter.__file__).name,):
            if original["source_sha256"].get(name) != sha(HERE / name):
                raise ValueError("resume would change provider/task runtime: " + name)
        # A local client disconnect after its deadline is a delivery event,
        # not an upstream inference failure. No request or response changes.
        def provider_runtime(path):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name == "send_json":
                    node.body = [ast.Pass()]
            return ast.dump(tree)
        if provider_runtime(run_root / "sources/provider.py") != provider_runtime(HERE / "provider.py"):
            raise ValueError("resume provider differs beyond local delivery exception bookkeeping")
        # Prove that trial execution changed only in post-run cap bookkeeping.
        def trial_runtime(path):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            selected = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name in {"run_trial", "build_env", "session_metrics", "sha", "file_hashes"}]
            for node in selected:
                if node.name == "run_trial":
                    node.body = [statement for statement in node.body if ast.unparse(statement) != "request_cap = request_cap or gateway.limit_reached"]
            return ast.dump(ast.Module(body=selected, type_ignores=[]))
        if trial_runtime(run_root / "sources/screen.py") != trial_runtime(Path(__file__)):
            raise ValueError("resume trial runtime differs beyond documented cap bookkeeping")
        resume_index = 1
        while (run_root / f"resume-sources-{resume_index:02d}").exists():
            resume_index += 1
        provenance = run_root / f"resume-sources-{resume_index:02d}"
        write_json(run_root / f"resume-audit-{resume_index:02d}.json", {"reason": args.resume_bookkeeping_reason,
            "original_runner_sha256": original["source_sha256"]["screen.py"],
            "resumed_runner_sha256": sha(__file__), "trial_runtime_ast_equal_except_cap_bookkeeping": True,
            "original_provider_sha256": original["source_sha256"]["provider.py"],
            "resumed_provider_sha256": sha(HERE / "provider.py"),
            "provider_ast_equal_except_local_delivery_exception_boundary": True,
            "task_binary_configuration_unchanged": True,
            "completed_trials_reused_within_original_batch": True})
        if (run_root / "status.json").exists():
            shutil.copy2(run_root / "status.json", run_root / f"status-before-resume-{resume_index:02d}.json")
    else:
        write_json(run_root / "manifest.json", manifest)
        provenance = run_root / "sources"
    provenance.mkdir()
    for source in source_files:
        shutil.copy2(source, provenance / source.name)
    for source in HERE.glob("*manifest.json"):
        shutil.copy2(source, provenance / source.name)
    if (HERE / "binary-provenance.json").is_file():
        shutil.copy2(HERE / "binary-provenance.json", provenance / "binary-provenance.json")
    key = args.key_file.read_text(encoding="utf-8").strip()
    write_json(run_root / "status.json", {"status": "RUNNING", "resumed": bool(args.resume_bookkeeping_reason)})
    results = []
    for rep in range(args.repeat):
        for index, case in enumerate(cases):
            order = conditions if (index + rep) % 2 == 0 else list(reversed(conditions))
            for condition in order:
                trial = run_root / f"r{rep+1}" / case / condition
                result_path = trial / "result.json"
                if args.resume_bookkeeping_reason and result_path.exists():
                    result = json.loads(result_path.read_text(encoding="utf-8"))
                    audit_path = trial / "postrun-audit.json"
                    audit = json.loads(audit_path.read_text(encoding="utf-8")) if audit_path.exists() else {}
                    if audit and audit.get("result_sha256") != sha(result_path):
                        raise ValueError("resume audit is not bound to original result")
                    if audit.get("integrity_status", result["integrity_status"]) != "PASS":
                        raise ValueError("resume requires resolved integrity for completed trial")
                    results.append(result)  # Keep the original result bytes/values.
                    print(json.dumps({"preserved_completed_trial":case,"condition":condition}), flush=True)
                    continue
                used_requests = sum(row["external_requests"] for row in results)
                used_tokens = sum(row["prompt_tokens"] + row["completion_tokens"] for row in results)
                if (used_requests + args.max_requests > args.campaign_max_requests
                        or used_tokens >= args.campaign_max_logical_tokens):
                    write_json(run_root / "status.json", {
                        "status": "BUDGET_STOP", "completed": len(results),
                        "used_requests": used_requests, "used_logical_tokens": used_tokens,
                        "next_case": case, "next_condition": condition,
                    })
                    return 3
                print(json.dumps({"starting": case, "condition": condition, "repeat": rep+1}), flush=True)
                result = run_trial(args, adapter, case, condition, trial, key)
                results.append(result)
                write_json(run_root / "results.json", results)
                if sum(row["prompt_tokens"] + row["completion_tokens"] for row in results) > args.campaign_max_logical_tokens:
                    write_json(run_root / "status.json", {
                        "status": "BUDGET_STOP", "completed": len(results),
                        "used_requests": sum(row["external_requests"] for row in results),
                        "reason": "logical_token_cap_reached_after_complete_trial",
                    })
                    return 3
                if result["integrity_status"] != "PASS":
                    write_json(run_root / "status.json", {"status": "INTEGRITY_STOP", "completed": len(results), "last_case": case, "last_condition": condition})
                    return 2
    write_json(run_root / "status.json", {"status": "COMPLETE", "completed": len(results)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
