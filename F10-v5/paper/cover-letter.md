# Cover Letter for Empirical Software Engineering

27 September 2026

Dear Editors,

Please consider my manuscript, "Source Conditioned Completion Contracts in an LLM Agent Harness", as a Research Paper for the special issue "Agentic Software Engineering: The Rise of AI Teammates".

The contribution concerns an agent harness interface: a registered, pre-generation source identifier selects an evidence-rich or minimal factual-QA terminal contract. The harness retains terminal processing and independent host-side answer grading. The paper documents the location of the router, the contract boundary, host-generated delivery metadata, and the distinction between response delivery and factual correctness.

The empirical evaluation compares the same agent configuration and model identifier within task pairs. The final frozen v5 campaign contains 600 unique identities shared across four models, with 2,400 model-task pairs and 4,800 assigned arm windows. Qwen and DeepSeek satisfy their original model-specific joint gates with 25.76% and 21.34% recorded logical-token savings. All four model lanes are reported. GLM and GPT-OSS do not satisfy the zero-integrity condition and are not presented as confirmed results.

The manuscript fits the special issue's Architectures for Agents, Testing and Evaluation, and Economic Cost and Impact topics. Its software engineering contribution is an implemented terminal-interface policy and an empirical evaluation of that policy's episode-level cost and accepted-answer outcome. The scope is explicitly source-labelled QA. I do not claim repository-editing improvements, greater autonomy, general adversarial safety, or a universal four-model benefit.

The supplementary archive includes the immutable protocol and outcome file, SHA-matched runner sources and task/target manifests, exact source snapshots, and a deterministic offline reproduction script. It includes original transport and session records, the excluded-ID ledger and exact frozen executable. A supplementary audit checks all 4,800 windows and all 6,441 forwarded requests, with zero route mismatches. Reanalysis of original results reproduces the four-model statistics without calling a model. Additional analyses report token components, latency, terminal validation behavior, and provider failure reasons, while identifying residual QA validator stalls and limits to failed-call usage accounting. The paper does not represent this reanalysis as a new confirmation campaign or claim a public preregistration.

The Methods section introduces Orion as an author-developed Go command-line harness and documents the model loop, terminal validation, gateway, external grading, and authority boundaries. A separate frozen research package is maintained in the private repository https://github.com/pyatkovpetr/emse under tag f10-v5-artifact-r1. The supplement provides reviewer access to the evidence and original Linux executable. The full harness source is not publicly released, and a reproducible source rebuild is not claimed.

I am the sole author. The manuscript has not been published previously and is not under consideration elsewhere. The study received no external funding, and I report no commercial relationship with Orion or the provider. My involvement in developing the studied harness is disclosed. This paper concerns completion-contract routing and is distinct from work on policy-denied file-write boundaries; no results from that separate study are substituted here.

Thank you for considering the manuscript.

Sincerely,

Petr A. Piatkov

Independent researcher, Ufa, Russian Federation

bigbox89@mail.ru

ORCID 0009-0003-0423-6467
