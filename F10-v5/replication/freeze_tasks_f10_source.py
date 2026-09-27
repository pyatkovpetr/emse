#!/usr/bin/env python3
"""Freeze disjoint, hash-selected tasks for both confirmatory phases."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import string
import tarfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


STUDY_ID = "WMC-ADAPTIVE-CONTRACT-C1-2026-08-06"
MMLU_GROUPS = {
    "humanities": {
        "formal_logic", "high_school_european_history", "high_school_us_history",
        "high_school_world_history", "international_law", "jurisprudence",
        "logical_fallacies", "moral_disputes", "moral_scenarios", "philosophy",
        "prehistory", "professional_law", "world_religions",
    },
    "social_sciences": {
        "econometrics", "high_school_geography", "high_school_government_and_politics",
        "high_school_macroeconomics", "high_school_microeconomics",
        "high_school_psychology", "human_sexuality", "professional_psychology",
        "public_relations", "security_studies", "sociology", "us_foreign_policy",
    },
    "stem": {
        "abstract_algebra", "anatomy", "astronomy", "college_biology",
        "college_chemistry", "college_computer_science", "college_mathematics",
        "college_physics", "computer_security", "conceptual_physics",
        "electrical_engineering", "elementary_mathematics", "high_school_biology",
        "high_school_chemistry", "high_school_computer_science",
        "high_school_mathematics", "high_school_physics", "machine_learning",
        "high_school_statistics",
    },
    "other": {
        "business_ethics", "clinical_knowledge", "college_medicine",
        "global_facts", "human_aging", "management", "marketing",
        "medical_genetics", "miscellaneous", "nutrition", "professional_accounting",
        "professional_medicine", "virology",
    },
}
MMLU_ALLOCATION = {"humanities": 13, "social_sciences": 13, "stem": 12, "other": 12}
SMOKE_PER_C1_DATASET = 5


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def selection_hash(dataset: str, upstream_id: str, prompt: str, target: Any) -> str:
    material = "\0".join((STUDY_ID, dataset, upstream_id, prompt,
                           canonical_json(target).decode("utf-8")))
    return sha256(material.encode("utf-8"))


def normalize_squad(text: str) -> str:
    text = text.lower()
    text = "".join(ch for ch in text if ch not in set(string.punctuation))
    text = re.sub(r"\b(a|an|the)\b", " ", text)
    return " ".join(text.split())


def write_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def jsonl_bytes(rows: Iterable[dict[str, Any]]) -> bytes:
    return b"".join(canonical_json(row) + b"\n" for row in rows)


def freeze_gsm(path: Path) -> list[dict[str, Any]]:
    rows = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        obj = json.loads(line)
        answer = obj["answer"].rsplit("####", 1)[-1].strip().replace(",", "")
        prompt = ("Solve the arithmetic word problem. Return only the final number.\n\n"
                  f"Question: {obj['question']}")
        upstream_id = f"test-{index:04d}"
        target = {"canonical_number": answer}
        rows.append({"dataset": "gsm8k", "upstream_task_id": upstream_id,
                     "prompt": prompt, "target": target,
                     "selection_hash": selection_hash("gsm8k", upstream_id, prompt, target)})
    return rows


def subject_group(subject: str) -> str:
    matches = [group for group, members in MMLU_GROUPS.items() if subject in members]
    if len(matches) != 1:
        raise ValueError(f"MMLU subject has ambiguous/missing group: {subject}: {matches}")
    return matches[0]


def freeze_mmlu(path: Path) -> list[dict[str, Any]]:
    rows = []
    with tarfile.open(path, "r") as archive:
        members = sorted(
            (m for m in archive.getmembers()
             if m.isfile() and re.search(r"/test/[^/]+_test\.csv$", m.name)),
            key=lambda member: member.name,
        )
        if len(members) != 57:
            raise ValueError(f"expected 57 MMLU test CSV files, found {len(members)}")
        for member in members:
            subject = Path(member.name).name.removesuffix("_test.csv")
            group = subject_group(subject)
            source = archive.extractfile(member)
            if source is None:
                raise ValueError(f"cannot read {member.name}")
            reader = csv.reader(io.TextIOWrapper(source, encoding="utf-8"))
            for index, row in enumerate(reader):
                if len(row) != 6 or row[5] not in "ABCD":
                    raise ValueError(f"invalid MMLU row {member.name}:{index + 1}")
                prompt = (f"Answer the multiple-choice question. Return only A, B, C, or D.\n\n"
                          f"Question: {row[0]}\nA. {row[1]}\nB. {row[2]}\n"
                          f"C. {row[3]}\nD. {row[4]}")
                upstream_id = f"{subject}-test-{index:04d}"
                target = {"choice": row[5]}
                rows.append({"dataset": "mmlu", "upstream_task_id": upstream_id,
                             "prompt": prompt, "target": target, "subject": subject,
                             "subject_group": group,
                             "selection_hash": selection_hash("mmlu", upstream_id, prompt, target)})
    return rows


def freeze_squad(path: Path) -> list[dict[str, Any]]:
    root = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    for article in root["data"]:
        for paragraph in article["paragraphs"]:
            context = paragraph["context"]
            for qa in paragraph["qas"]:
                answers = [a["text"] for a in qa.get("answers", []) if a.get("text", "").strip()]
                normalized = {normalize_squad(answer) for answer in answers}
                if qa.get("is_impossible") or len(answers) < 2 or len(normalized) != 1:
                    continue
                prompt = ("Answer using only the supplied passage. Return only the shortest answer span.\n\n"
                          f"Passage:\n{context}\n\nQuestion: {qa['question']}")
                target = {"answers": sorted(set(answers)), "normalized_answer": next(iter(normalized))}
                rows.append({"dataset": "squad2", "upstream_task_id": qa["id"],
                             "prompt": prompt, "target": target, "title": article["title"],
                             "selection_hash": selection_hash("squad2", qa["id"], prompt, target)})
    return rows


def select_c1(pools: dict[str, list[dict[str, Any]]]) -> tuple[list[dict], list[dict]]:
    confirmatory, smoke = [], []
    for dataset in ("gsm8k", "squad2"):
        ordered = sorted(pools[dataset], key=lambda row: row["selection_hash"])
        confirmatory.extend(ordered[:50])
        smoke.extend(ordered[50:50 + SMOKE_PER_C1_DATASET])
    mmlu_by_group: dict[str, list[dict]] = {group: [] for group in MMLU_ALLOCATION}
    for row in pools["mmlu"]:
        mmlu_by_group[row["subject_group"]].append(row)
    for group, count in MMLU_ALLOCATION.items():
        ordered = sorted(mmlu_by_group[group], key=lambda row: row["selection_hash"])
        confirmatory.extend(ordered[:count])
        smoke.extend(ordered[count:count + 2])
    return confirmatory, smoke


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def select_framework_tasks(framework_root: Path) -> dict[str, Any]:
    he_path = framework_root / "benchmarks_data/humaneval_plus/questions/humaneval_plus.jsonl"
    he_rows = load_jsonl(he_path)
    he_ranked = sorted(he_rows, key=lambda row: sha256(
        f"{STUDY_ID}\0humaneval_plus\0{row['task_id']}".encode()))
    he_confirm = [f"humaneval_{r['task_id'].replace('/', '_')}" for r in he_ranked[:30]]
    he_smoke = [f"humaneval_{r['task_id'].replace('/', '_')}" for r in he_ranked[30:35]]

    bfcl_confirm, bfcl_smoke = [], []
    qroot = framework_root / "benchmarks_data/bfcl/questions"
    for category in ("multi_turn_base", "multi_turn_miss_param", "multi_turn_long_context"):
        rows = load_jsonl(qroot / f"BFCL_v4_{category}.json")
        ranked = sorted(rows, key=lambda row: sha256(
            f"{STUDY_ID}\0bfcl_v4\0{row['id']}".encode()))
        bfcl_confirm.extend(f"bfcl_{r['id']}" for r in ranked[:10])
        bfcl_smoke.extend(f"bfcl_{r['id']}" for r in ranked[10:12])
    return {"humaneval_plus": {"confirmatory": he_confirm, "smoke": he_smoke},
            "bfcl_c1": {"confirmatory": bfcl_confirm, "smoke": bfcl_smoke}}


def workspace_specs() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    specs = []
    for index in range(36):
        variant = index % 3
        if variant == 0:
            kind = "json_configuration_edit"
            files = {"config.json": json.dumps({"service": {"port": 7000 + index,
                      "features": {"audit": False, "cache": True}}, "owners": ["ops"]}, indent=2) + "\n"}
            expected = {"service.port": 8000 + index, "service.features.audit": True,
                        "service.features.cache": True, "owners_append": f"reviewer-{index}"}
            instruction = (f"Edit config.json: set service.port to {8000 + index}, enable audit, "
                           f"append reviewer-{index} to owners, and preserve every other value.")
        elif variant == 1:
            kind = "cross_file_symbol_rename"
            old, new = f"legacy_rate_{index}", f"normalized_rate_{index}"
            files = {"src/rates.py": f"def {old}(value):\n    return value / 100\n",
                     "src/report.py": f"from .rates import {old}\n\ndef render(value):\n    return {old}(value)\n",
                     "tests/test_rates.py": f"from src.report import render\n\ndef test_render():\n    assert render(25) == 0.25\n"}
            expected = {"old_absent": old, "new_present": new, "pytest": True}
            instruction = (f"Rename the Python symbol {old} to {new} across the workspace without "
                           "changing behavior. Keep tests passing.")
        else:
            kind = "localized_bug_fix"
            files = {"src/window.py": "def moving_sum(values, width):\n    if width <= 0:\n        raise ValueError('width')\n    return [sum(values[i:i+width]) for i in range(len(values))]\n",
                     "tests/test_window.py": "from src.window import moving_sum\n\ndef test_complete_windows_only():\n    assert moving_sum([1,2,3,4], 3) == [6,9]\n\ndef test_too_wide():\n    assert moving_sum([1,2], 3) == []\n"}
            expected = {"pytest": True, "edit_scope": ["src/window.py"]}
            instruction = "Fix moving_sum so it returns only complete windows. Keep its validation behavior and make all tests pass."
        specs.append({"dataset": "workspace_ops", "upstream_task_id": f"generated-{index:03d}",
                      "kind": kind, "instruction": instruction, "files": files, "expected": expected})
    ranked = sorted(specs, key=lambda row: sha256(
        f"{STUDY_ID}\0workspace_ops\0{row['upstream_task_id']}\0".encode() + canonical_json(row)))
    return ranked[:30], ranked[30:36]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--framework-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise SystemExit(f"refusing to write into non-empty freeze directory: {args.output_dir}")

    pools = {"gsm8k": freeze_gsm(args.source_dir / "gsm8k-test.jsonl"),
             "mmlu": freeze_mmlu(args.source_dir / "mmlu-data.tar"),
             "squad2": freeze_squad(args.source_dir / "squad2-dev.json")}
    c1, c1_smoke = select_c1(pools)
    framework = select_framework_tasks(args.framework_root)
    ws, ws_smoke = workspace_specs()
    c2 = {**framework, "workspace_ops": {"confirmatory": [r["upstream_task_id"] for r in ws],
                                          "smoke": [r["upstream_task_id"] for r in ws_smoke]}}
    for rows in (c1, c1_smoke):
        for row in rows:
            row["task_id"] = f"{row['dataset']}_{row['upstream_task_id']}"
    outputs = {
        "c1-confirmatory.jsonl": jsonl_bytes(sorted(c1, key=lambda r: (r["dataset"], r["selection_hash"]))),
        "c1-smoke.jsonl": jsonl_bytes(sorted(c1_smoke, key=lambda r: (r["dataset"], r["selection_hash"]))),
        "c2-selections.json": json.dumps(c2, sort_keys=True, indent=2).encode() + b"\n",
        "workspace-confirmatory.jsonl": jsonl_bytes(ws),
        "workspace-smoke.jsonl": jsonl_bytes(ws_smoke),
    }
    counts = Counter(row["dataset"] for row in c1)
    if counts != Counter({"gsm8k": 50, "mmlu": 50, "squad2": 50}):
        raise SystemExit(f"invalid C1 counts: {counts}")
    receipt = {"schema_version": 1, "study_id": STUDY_ID,
               "counts": dict(sorted(counts.items())),
               "mmlu_groups": dict(sorted(Counter(r["subject_group"] for r in c1
                                                    if r["dataset"] == "mmlu").items())),
               "outputs": {name: {"bytes": len(payload), "sha256": sha256(payload)}
                           for name, payload in outputs.items()}}
    outputs["freeze.receipt.json"] = json.dumps(receipt, sort_keys=True, indent=2).encode() + b"\n"
    for name, payload in outputs.items():
        write_new(args.output_dir / name, payload)
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
