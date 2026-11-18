# Knowledge Fabric Assurance PoC

A small working implementation of the Knowledge Fabric solution architecture: an ingestion gate, a versioned knowledge store, a retrieval and verification flow, and an evaluation loop. It is prepared by the QualiZeal AI Center of Excellence (AI CoE) as proof that the proposed design works end to end.

It runs on plain Python with no paid services and no extra packages. All sample content in `data/sources` is synthetic and written for this PoC.

## What it proves

| Architecture block | What the PoC does | Code |
|---|---|---|
| Ingestion gate | Content hash to skip unchanged files, file integrity, readability score, knowledge-type classification, exact and near-duplicate detection (MinHash), checks per knowledge type, conflict check against current knowledge, and one of five outcomes: accept, reference only, human review, quarantine, reject | `kfabric/ingest.py` |
| Knowledge store | Chunks that carry their document title, keyword index (BM25), links between assets, access rights per asset, a trust score, and two sets of dates: when content applies in the business and when the fabric recorded or replaced it | `kfabric/store.py` |
| Retrieval and verification | Access, date and trust filters before search; keyword and vector search fused by reciprocal rank; link expansion; reranking weighted by trust; a sufficiency check; answers built only from approved sentences with a citation each; abstain or flag the gap when evidence is thin | `kfabric/retrieve.py` |
| Evaluation loop | Golden question set run on every change, with each failure attributed to source, retrieval, generation or policy | `kfabric/evaluate.py`, `data/golden.json` |
| Control plane | Source registry with owner, issuer, access, validity dates and version links | `data/registry.json` |

## What a run shows

17 sample sources go in. 9 are accepted, 3 are kept as reference only, 1 goes to human review, 2 are quarantined and 2 are rejected. Examples:

- A Jira story with no acceptance criteria is kept as reference only.
- A repository file with a secret-like value is quarantined.
- A policy from an issuer outside the approved registry is quarantined.
- A regional memo that states a different towing limit from the current policy goes to human review.
- A wiki copy of an approved document is caught as a near-duplicate; an exact copy is rejected.
- An unreadable scan and a damaged PDF never reach the index.

The golden set then asks 9 questions. All 9 pass, including:

- the current policy is cited and the superseded one is not;
- the same question asked "as of June 2024" returns the 2024 policy;
- a confidential document is invisible to a role without access and visible to the right role;
- questions with no approved evidence are declined, and a partly covered question is answered with the gap named.

During the build the golden set caught a real defect: the conflict check missed the regional memo. That is the loop doing its job.

`reports/latest.json` holds the full result of the last run, including the retrieval trace for every question.

## How to run it

Open the **Actions** tab, choose **Knowledge Fabric PoC**, and press **Run workflow**. The run page shows the ingestion outcomes and the golden-set table, and the report is attached as a download. The workflow also runs on every change to the repository.

## PoC shortcuts and what production needs

| Shortcut in this PoC | Production path |
|---|---|
| Sample files in the repository | Connectors to Jira, GitHub, Confluence and SharePoint, with webhooks and scheduled sync |
| Rule and statistical checks only; the slot for an LLM judge is empty | Add a calibrated LLM judge for checks that need reading for meaning, such as clarity and testability |
| Conflict check compares simple "name: value" lines | Claim extraction with an entailment model |
| Vector search uses TF-IDF | Embedding model and a vector index |
| Answers are extracted sentences | Generated answers, checked sentence by sentence against the evidence |
| Access is a list of roles in the registry | Access copied from the source system and enforced with the client's identity provider |
| SQLite file | Managed database and search service in the client's cloud |
| 9 golden questions | 100+ questions per domain, labelled with the client's experts |
| No user interface | Trust dashboard inside the client's existing telemetry |
