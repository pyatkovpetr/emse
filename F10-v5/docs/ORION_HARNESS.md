# Orion in the F10 study

Orion is an author-developed Go command-line harness around remote language-model deployments. It is the implementation under study, not an assumed community-standard product. Its full source code remains closed. The exact Linux executable used in v5 is distributed here, alongside the experimental runner and narrower source-reference documentation. This package is not an open-source release of the whole harness.

## Episode and component responsibilities

1. The QA adapter materializes a registered task identity, prompt, workspace and configuration. Targets remain with the separate grader.
2. The runner chooses the arm and completion contract before starting Orion. It supplies a new home/workspace for each window, the prompt on standard input, flags, a model identifier and a loopback gateway URL.
3. Orion builds a provider request with messages and the advertised task_complete schema. In this experiment that is the only exposed tool; shell access, skills, thinking, streaming and memory are disabled.
4. The gateway forwards requests to the external deployment, owns its credential, and saves original/forwarded payloads, responses and usage receipts. Orion receives a loopback placeholder rather than the external credential.
5. Orion processes native terminal calls and host validation. A negative result can become feedback in another request. Direct-text terminal processing also occurs; tool-result and provider-request counts differ.
6. Orion records completion status, reason and response in session records. The runner extracts the response, grades it externally and writes result.json with acceptance, correctness, usage completeness, expenditure and wall time.

## Source routing and completion contracts

The source router is the experiment runner's screen.py, not a model-generated classification. It requires QA kind and a registered task prefix. The rich arm uses rich for all families. The selective arm uses rich for gsm8k_ identities, qa-direct for mmlu_ and squad2_ identities. The gateway does not choose a route from model answers.

The QA-direct schema requires status and response. Rich asks for additional summary, criteria and evidence-reference fields. Exact schema objects from every original request are in replication/implementation-reference/frozen-*.schema.json. The audit finds exactly two stable objects, 4,479 rich exposures and 1,962 QA-direct exposures, with zero route mismatches.

In the inspected completion implementation QA-direct retains the answer payload and constructs control metadata on the host. A delivery receipt documents reception, not factual truth. The separate benchmark grader decides correctness. Authority comes from the task adapter; the design has not been evaluated as an authenticated or adversarially secure source-label service.

## Configuration and frozen implementation

The original executable is runtime/orion-linux-amd64-v5, an ELF for Linux amd64, SHA-256 2376fb27db0f6ec5543bcdeeb8d2e01d7943dabb994396d63089c7a03f1487cb. The campaign used Go 1.26.2, base revision a8c87a3f2804c9a61e07c0350e2cccb860dabe5c, with vcs.modified=true. The modified source tree is not recovered. Today's source or a new Windows build would not be the exact v5 artifact. No Windows .exe is labelled as frozen v5.

The recorded QA configuration uses the general agent, direct response, exact tool surface, stable prompt prefix and tool ABI, terminal-attempt cap three, controller strict mode and executor validation. Per-generation limit: 8,192 tokens; context: 131,072; outer task deadline: 900 seconds; provider attempt timeout: 880 seconds; up to five provider attempts including the initial attempt. Global request/token hard caps are null. All flags and configuration bindings are retained in primary execution.json records.

## Engineering limitations seen in the traces

General file-change validation still stalls some QA-only episodes: Qwen and DeepSeek each have 86 rich and 74 selective stalled windows. Thus the measured intervention bundles the schema and associated validation/feedback; it does not isolate a field-count effect or compare against a separately corrected QA validator.

Seven provider failures occur in three windows: one upstream 503, five 400s and one 504. The frozen gateway records upstream status but returns generic 502 to Orion, causing even the 400 sequence to consume five attempts. Failed calls have no usage; their unknown cost is not invented. Operational recovery does not repair the registered zero-integrity condition. The frozen binary/outcomes are not edited to remove these limitations.

## Evidence boundaries

All 4,800 windows and 6,441 requests are recovered and audited. Numerical reproduction from original results matches every saved task outcome and four-model statistic. Reproduction of analysis is distinct from re-executing a remote stochastic model, source rebuilding, provider-weight attestation, and proof of autonomy or adversarial security.
