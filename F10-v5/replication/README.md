# F10 v5 replication package with original traces

The original protocol, analyzer and outcomes are immutable. Earlier v2 and the 27 September partial-recovery submission packet are superseded by this trace-verified revision. No outcome or gate was changed.

## Reproduction without a model

Python 3.12 or newer, standard library only. From this directory:

```
python reproduce_offline.py
python unpack_primary.py
python reproduce_selection.py
python audit_primary_traces.py
python reproduce_primary.py
```

The first command reproduces the saved frozen statistics without unpacking primary files. The second safely extracts primary-traces.tar.gz and checks every substantive file SHA. Existing frozen files must match and are not overwritten. The last two commands audit original requests/receipts/sessions and recompute both frozen bootstrap estimators directly from raw result files. They disable network use, do not import the provider, do not invoke the campaign, and do not execute Orion. Primary-record statistics match all original per-task rows and four-model decisions exactly. Derived outputs have separate directories. On Windows unpacking many small files can take several minutes.

## Original evidence

reproduce_selection.py independently regenerates the hash-ranked sample from the three frozen source snapshots and the recovered exclusion ledger, without consulting model outcomes or using the selected IDs to choose tasks. Both complete task and answer manifests reproduce their original bytes and SHA-256 values exactly.

The recovered server archive contains all 4,800 windows, 2,400 pairs and 6,441 forwarded requests/receipts, exact runner, task and target manifests, 2,320 excluded IDs, qualification records and the original Linux executable. Source snapshots match the frozen hashes. The submitted compressed trace archive preserves every substantive record byte; only ephemeral signing keys, empty lock files and Python caches are omitted. The full original archive is retained privately. primary-trace-file-sha256.json inventories each included primary file, and primary-archive-provenance.json records the original and submitted archive hashes.

The selected 600 IDs are disjoint from the exclusion ledger and the three qualification identities. These checks verify the recorded rule but do not prove completeness of every earlier exposure or public preregistration. Original stale v2 protocol wording is disclosed in the manuscript and is not changed.

The executable SHA is 2376fb27db0f6ec5543bcdeeb8d2e01d7943dabb994396d63089c7a03f1487cb. Embedded metadata: Go 1.26.2, Linux amd64, base Git revision a8c87a3f2804c9a61e07c0350e2cccb860dabe5c, vcs.modified=true. The exact executable is supplied in the trace archive; its modified source working tree was not reconstructed. The base revision alone is insufficient for a reproducible source rebuild. Reusing the executable does not guarantee identical future provider outputs.

## Additional trace measurements

primary-audit/ contains the audit, exact frozen schema objects and per-window measurements. All 6,441 requests expose the expected contract: 4,479 rich and 1,962 QA-direct. Two complete schema objects are stable within windows. Request receipt hashes, saved response usage and recorded trial sums reconcile; all original per-task outcomes match.

Transport retry counts identify identical payload resubmissions immediately after failed receipts, not extra calls or heartbeat polls. There are six such retries across three failing windows. Seven upstream failures comprise one 503, five 400s and one 504. Those failed calls have no usage and their expenditure remains unknown; GLM and GPT-OSS do not meet zero integrity. Provider error bodies and original HTTP response body bytes were not retained by the gateway. Saved response JSON and recorded usage are included, but original HTTP-body hashes cannot be independently regenerated from pretty-printed JSON.

Negative-result recovery sequences, non-relay terminal events, executor-validation stalls and agent wall times are descriptive analyses after the campaign. The schema intervention and downstream feedback form one policy contrast. No factorial ablation, autonomy, repository-effectiveness or adversarial-safety claim is made. Residual general executor guards stop some QA windows in both arms.

## Contract documentation

implementation-reference/frozen-qa-direct.schema.json and frozen-rich.schema.json are exact schema objects from original requests. They also match separately source-inspected reference objects. Source provenance is retained separately; raw runtime schemas and source documentation are distinguished.

## Source attribution

GSM8K: https://github.com/openai/grade-school-math, grade_school_math/data/test.jsonl.
MMLU: https://github.com/hendrycks/test and original authors' https://people.eecs.berkeley.edu/~hendrycks/data.tar.
SQuAD2: https://rajpurkar.github.io/SQuAD-explorer/, dev-v2.0.json, CC BY-SA 4.0 as distributed by its source site. Dataset snapshots and transformed manifests retain original third-party terms; no blanket new license is asserted.

No API credentials or signing secrets are included in the submission copy. No public repository or DOI deposit is claimed.
