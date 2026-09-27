"""Frozen paired analysis of the four-model F10 clean campaign."""
from __future__ import annotations

import hashlib
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
CAMPAIGN = HERE.parent
MODELS = ("qwen3.6-fp8-noreason", "deepseek-v4.1-flash", "glm-5.3-flash", "gpt-oss-120b")
SEED = 20260923
BOOTSTRAPS = 10000
ALPHA = 0.0125


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def first_user(request):
    return [message.get("content") for message in request["messages"] if message.get("role") == "user"]


def contract(request):
    matches = [item["function"] for item in request.get("tools", [])
               if item.get("function", {}).get("name") == "task_complete"]
    if len(matches) != 1:
        return "unknown"
    properties = set(matches[0]["parameters"].get("properties", {}))
    return "qa-direct" if properties == {"status", "response"} else "rich" if "criteria" in properties else "unknown"


def metric(rows):
    rich = sum(row["rich_tokens"] for row in rows)
    selective = sum(row["selective_tokens"] for row in rows)
    loss = sum(int(row["rich_correct"]) - int(row["selective_correct"]) for row in rows) / len(rows)
    return (rich - selective) / rich if rich else 0.0, loss


def quantile(values, proportion):
    values = sorted(values)
    return values[min(len(values) - 1, max(0, int(proportion * len(values))))]


def bootstrap(rows, *, cluster: bool):
    by_family = defaultdict(list)
    for row in rows:
        by_family[row["family"]].append(row)
    rng = random.Random(SEED + int(cluster))
    savings, losses = [], []
    for _ in range(BOOTSTRAPS):
        selected = []
        for family in ("gsm8k", "mmlu", "squad2"):
            family_rows = by_family[family]
            if not cluster or family == "gsm8k":
                selected.extend(family_rows[rng.randrange(len(family_rows))] for _ in family_rows)
                continue
            grouped = defaultdict(list)
            for row in family_rows:
                grouped[row["subject"] if family == "mmlu" else row["article"]].append(row)
            groups = list(grouped.values())
            selected.extend(row for _ in groups for row in groups[rng.randrange(len(groups))])
        savings.append(metric(selected)[0])
        # Equal source-family weights even when cluster bootstrap changes group sizes.
        family_losses = []
        for family in ("gsm8k", "mmlu", "squad2"):
            family_sample = [row for row in selected if row["family"] == family]
            family_losses.append(metric(family_sample)[1])
        losses.append(sum(family_losses) / 3)
    return {"saving_lower_multiplicity_adjusted": quantile(savings, ALPHA),
            "quality_loss_upper_multiplicity_adjusted": quantile(losses, 1 - ALPHA),
            "saving_95pct": [quantile(savings, 0.025), quantile(savings, 0.975)],
            "quality_loss_95pct": [quantile(losses, 0.025), quantile(losses, 0.975)]}


def main():
    protocol_path = CAMPAIGN / "campaign-protocol.json"
    protocol = read(protocol_path)
    if (protocol["models"] != list(MODELS) or protocol["task_count"] != 600 or
            protocol["analysis_sha256"] != sha(Path(__file__))):
        raise RuntimeError("campaign protocol changed")
    tasks = [json.loads(line) for line in (CAMPAIGN / "frozen/tasks.jsonl").read_text(encoding="utf-8").splitlines()]
    if sha(CAMPAIGN / "frozen/tasks.jsonl") != protocol["clean_tasks_sha256"]:
        raise RuntimeError("clean task manifest changed")
    reports = {}
    for model in MODELS:
        root = CAMPAIGN / "runs" / model
        if not (root / "complete.json").is_file():
            raise RuntimeError(f"model clean run incomplete: {model}")
        rows, integrity_failures, provider_requests = [], 0, 0
        for task in tasks:
            pair = {}
            for arm in task["arm_order"]:
                trial = root / f"{task['index']:04d}" / task["task_id"] / arm
                result = read(trial / "result.json")
                execution = read(trial / "execution.json")
                request_paths = sorted((trial / "transport").glob("request-*/request.json"))
                if execution["model"] != model or result["case_id"] != task["task_id"]:
                    raise RuntimeError("model/task binding mismatch")
                if len(request_paths) != result["external_requests"]:
                    raise RuntimeError("provider request ledger mismatch")
                provider_requests += len(request_paths)
                integrity_failures += result["integrity_status"] != "PASS" or not result["usage_complete"]
                if request_paths:
                    request = read(request_paths[0])
                    observed_contract = contract(request)
                    expected_contract = "rich" if arm == "rich" or task["family"] == "gsm8k" else "qa-direct"
                    if observed_contract != expected_contract:
                        raise RuntimeError("route exposure mismatch")
                    first = first_user(request)
                else:
                    first = None
                pair[arm] = {"tokens": result["prompt_tokens"] + result["completion_tokens"],
                             "correct": bool(result["success_by_deadline"]),
                             "first_user": first,
                             "initial_hashes_sha256": sha(trial / "initial-hashes.json"),
                             "integrity": result["integrity_status"]}
            if pair["rich"]["first_user"] != pair["selective"]["first_user"] or pair["rich"]["initial_hashes_sha256"] != pair["selective"]["initial_hashes_sha256"]:
                raise RuntimeError("paired task context mismatch")
            rows.append({"task_id": task["task_id"], "family": task["family"],
                         "subject": task["subject"], "article": task["article"],
                         "rich_tokens": pair["rich"]["tokens"],
                         "selective_tokens": pair["selective"]["tokens"],
                         "rich_correct": pair["rich"]["correct"],
                         "selective_correct": pair["selective"]["correct"],
                         "rich_integrity": pair["rich"]["integrity"],
                         "selective_integrity": pair["selective"]["integrity"]})
        if len(rows) != 600:
            raise RuntimeError("missing assigned task")
        saving, quality_loss = metric(rows)
        main_ci = bootstrap(rows, cluster=False)
        cluster_ci = bootstrap(rows, cluster=True)
        by_family = {family: {"tasks": len(subset), "saving": metric(subset)[0],
                              "quality_loss": metric(subset)[1],
                              "rich_correct": sum(row["rich_correct"] for row in subset),
                              "selective_correct": sum(row["selective_correct"] for row in subset)}
                     for family in ("gsm8k", "mmlu", "squad2")
                     for subset in [[row for row in rows if row["family"] == family]]}
        pass_gate = (integrity_failures == 0 and main_ci["saving_lower_multiplicity_adjusted"] > 0.15 and
                     main_ci["quality_loss_upper_multiplicity_adjusted"] < 0.05 and
                     cluster_ci["saving_lower_multiplicity_adjusted"] > 0.15 and
                     cluster_ci["quality_loss_upper_multiplicity_adjusted"] < 0.05)
        reports[model] = {"status": "CONFIRMATION_PASS" if pass_gate else "CONFIRMATION_NOT_ESTABLISHED",
                          "tasks": 600, "trials": 1200, "provider_requests": provider_requests,
                          "logical_tokens_rich": sum(r["rich_tokens"] for r in rows),
                          "logical_tokens_selective": sum(r["selective_tokens"] for r in rows),
                          "saving": saving, "quality_loss": quality_loss,
                          "integrity_failures": integrity_failures,
                          "by_family": by_family, "task_bootstrap": main_ci,
                          "source_cluster_sensitivity": cluster_ci,
                          "per_task": rows}
    output = {"schema": "f10-four-model-clean-analysis-v1",
              "protocol_sha256": sha(protocol_path),
              "tasks_sha256": protocol["clean_tasks_sha256"],
              "analysis_sha256": sha(Path(__file__)),
              "bootstrap_repetitions": BOOTSTRAPS,
              "four_model_familywise_alpha": 0.05,
              "models": reports}
    path = CAMPAIGN / "analysis-result.json"
    if path.exists():
        raise FileExistsError("analysis result already exists")
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({model: {k: value[k] for k in ("status", "saving", "quality_loss", "integrity_failures")}
                      for model, value in reports.items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
