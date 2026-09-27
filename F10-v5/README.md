# Frozen F10 v5 research package

This snapshot supports the manuscript Source Conditioned Completion Contracts in an LLM Agent Harness by Petr A. Piatkov. It contains the original evidence and exact executable for the completed campaign; no new model outcomes were collected to prepare it.

Repository: https://github.com/pyatkovpetr/emse (private). Snapshot: f10-v5-artifact-r1. Study: F10-EMSE-CLEAN-V5-2026-09-25. The full Orion harness source remains closed. Experimental code, exact Linux binary and records are included; an executable release does not assert open-source status.

## Contents

- paper/: revised IMRAD manuscript and cover letter, PDF/DOCX/Markdown, architecture figure.
- runtime/: exact Linux amd64 executable, its SHA/build provenance and dependency licence notices.
- replication/: frozen campaign metadata, runner/analyzer, source snapshots, primary trace archive, schema objects and all-request audit; see its README.
- docs/: harness architecture and evidence/version boundaries.
- frozen-package-manifest.json and SHA256SUMS.txt: content inventory for this snapshot.

## Reproduce without a provider

Python 3.12+, standard library only. From replication/:

For a Git checkout, first run python restore_source_snapshot.py from F10-v5/. Git stores the 166 MB MMLU source tar in lossless gzip form to fit file-size limits. The script reconstructs and verifies its exact frozen SHA; it refuses to overwrite different bytes. The supplementary ZIP already contains the original uncompressed tar and needs no restoration step.

```
python reproduce_offline.py
python unpack_primary.py
python reproduce_selection.py
python audit_primary_traces.py
python reproduce_primary.py
```

These commands do not execute Orion or call a model. All five passed when the supplementary archive was independently unpacked on Ubuntu. They regenerate the 600-task selection and targets, check all substantive file hashes and 6,441 routes/receipts, and reconstruct frozen statistics from 4,800 original outcomes. Existing frozen files must match; unpack_primary refuses to overwrite differing records. SHA256SUMS.txt at this snapshot root applies before unpacking, which creates further files already covered by the primary-member SHA inventory.

## Runtime

The supplied harness file is a Linux ELF, not a Windows .exe. Download it on Linux amd64 and set executable permission if needed: chmod +x runtime/orion-linux-amd64-v5. Its SHA matches the protocol. Offline analysis does not need the binary.

The original live launch scripts retain server paths and credential references as provenance. They are not a portable one-click launcher. A new live experiment needs separately supplied provider access and a new run directory, with any relocation or adapter changes versioned outside the archived files. Reusing inspected tasks is development work, not a new clean confirmation. Provider outputs and original modified-source rebuild are not guaranteed reproducible.

## Findings

Qwen and DeepSeek pass their original model-specific cost/quality/integrity gates, with 25.76% and 21.34% recorded token savings. GLM and GPT-OSS remain reported with confirmation not established because of missing failed-call usage. All four lanes and all assigned tasks remain. Additional timings, token components, retries, native terminal recovery and residual QA guard stalls are descriptive audits.

The GitHub release attaches the supplementary replication ZIP and full submission packet. Primary traces also reside in replication/primary-traces.tar.gz. The frozen tag must not be reassigned; revisions receive a new tag and retain the original campaign evidence.
