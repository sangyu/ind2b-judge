# Trust Layer: Per-Layer Confidence Definitions + Judge Rubric

Companion to `docs/verification_rubric.md` (the v1 builder checklist). This doc does two things:

1. **Defines what "confidence" means at each layer** of the evidence-to-binder workflow.
2. **Restructures the v1 checklist into a rubric a Claude agent-as-judge can execute** against a persisted run.

Spec references (§) point to the CELLxGENE evidence-to-binder build specification v3.

---

## 0. Why this doc exists (trust-layer gaps being closed)

The spec has a strong trust *substrate* — origin labels, null-not-zero, deterministic ranking, four distinct data classes, the honest outcome enum. Three structural gaps remain:

1. **"Confidence" does at least three incompatible jobs and the spec never says which applies where.** Biological evidence strength (§9 rubric), model-metric confidence (ipTM/pLDDT, §10), and retrieval/coverage confidence (did we inspect enough of the universe to know) are all called "confidence." §2's disclaimer says what confidence *isn't* (a calibrated probability) but never what it *is* at each stage. Part 1 fixes this.
2. **Process trust and scientific trust are conflated.** A pLDDT of 0.9 on an unverified chain mapping is high model confidence and low provenance trust simultaneously. Any rubric that merges these into one verdict will pass things it shouldn't.
3. **The v1 checklist is a builder's tool, not a judge's rubric.** It is binary, self-reported, has no UNVERIFIABLE state, and doesn't require the grader to cite evidence. Handed to a judge as-is, the judge ends up grading the Status column — i.e., trusting the system's self-report, which is the one thing a trust layer exists to avoid. Part 2 fixes this.

---

## Part 1 — Confidence definitions per layer

### Shared envelope

Every layer output carries `{ result, confidence }` where:

```
confidence = {
  level: strong | moderate | weak | unknown,   // spec §9 vocabulary, layer-local meaning
  claim_scope: string,     // exactly what the level attaches to
  basis: [evidence_ids / record_ids],
  coverage: {...},         // what fraction of the relevant universe was inspected
  caveats: [string]
}
```

### Two orthogonal axes — never merged

- **Scientific support** — is the claim biologically supported?
- **Provenance integrity** — is the artifact what it says it is (real vs fixture, identity verified, deterministic)?

A single number cannot carry both. Every confidence statement in the system is implicitly a pair.

### Per-layer definitions

| Layer | Output | Confidence means | Set by | Never means |
|---|---|---|---|---|
| **L0 Indication/MoA resolution** | Resolved disease ID, MoA interpretation | Match quality of entity resolution | Exact Open Targets match + user confirm → strong; synonym → moderate; parent/subtype broadening or unconfirmed → weak; no match → unknown | That the disease is druggable |
| **L1 Target discovery** | Bounded pool (~20) | Retrieval coverage & source integrity: query succeeded, scope/pagination recorded, dedup done | Clean complete response → strong; partial response → weak; timeout → unknown | Association strength (that's L2/L4). A timeout is a source failure, not evidence no targets exist (§16) |
| **L2 Evidence assembly** | Evidence records | Strength of each claim's underlying evidence class: genetic > clinical > experimental > literature-asserted; direct vs ontology-propagated | Class + directness + contradiction count, per claim | Probability of therapeutic success |
| **L3 Cellular context** | CellSummary, EmbeddingSlice, NeighborhoodEvidence | Statistical & coverage adequacy: donor n, controls present, embedding coverage %, identity alignment verified | Multi-donor + controls + high coverage + alignment check passed → strong; single donor / no controls / sparse coverage → weak | That similarity = identity/causality; that expression = surface protein; that missing = absent |
| **L4 Ranking** | Ordered shortlist | **Decision margin**: separation between finalists under the rubric + criterion missingness. The math is deterministic, so confidence is about decisiveness, not correctness of arithmetic | Top candidate separates on multiple criteria with low missingness → strong; unresolved → explicit tie, never false order | "Highest confidence" = strongest support *among the evaluated shortlist under stated criteria* (§2), nothing more |
| **L5 Design request validation** | Validated DesignRequest | Identity & feasibility verification: Ensembl→UniProt by ID, chain mapping, binding-region rationale tied to MoA | This is a **gate**, not a gradient: validated / not-validated, with caveats enumerated | A scientific endorsement of the target choice |
| **L6 Design execution** | DesignRun status | Execution integrity only: real job, completed, artifacts persisted, honest states | Binary process trust | Anything about candidate quality — a succeeded job may yield zero passing candidates (§7) |
| **L7 Candidate evaluation** | Candidate records | Model-specific metric confidence, each labeled method + model_version, predicted geometry only | The model's own scores + evaluation_status | Affinity, Kd, blockade, efficacy. High model confidence ≠ biological effect; the next wet-lab assay must be named |

### Propagation rules

1. Confidence never averages across layers; the final report shows a per-layer profile, not a composite number.
2. `unknown` propagates as `unknown` and can never be silently upgraded downstream.
3. Gates cap, they don't blend: L5 failure blocks L6, but a weak L3 doesn't invalidate a strong L2 — it narrows the claim scope.
4. Contradictions are preserved, not netted out; a claim with 5 supports and 1 contradiction is "strong, contested," not "moderate."

---

## Part 2 — Judge rubric (agent-as-judge)

### What changes from v1 and why

| v1 (builder checklist) | v2 (judge rubric) |
|---|---|
| Checkbox pass/fail | PASS / FAIL / **UNVERIFIABLE** / N/A |
| Self-reported Status column | Judge re-verifies from raw records; the system's own status column is a claim to check, not evidence |
| "Passes when" | Pass condition + fail condition + **where the judge must look** + citation requirement |
| No aggregation rule | Explicit overall verdict + single-strike fabrication rule |

### Judge inputs

AgentRun + tool_action_log, evidence graph records, CellSlice/EmbeddingSlice manifests, DesignRun/Candidate records, the rendered report, `docs/capabilities.md`, and the team's filled v1 checklist (as a *claim*, not proof).

### Verdict vocabulary (per check)

- **PASS** — judge located the artifact evidence and it satisfies the condition. Must cite ≥1 record ID / log line / manifest key. A PASS without citation is invalid → downgrade to UNVERIFIABLE.
- **FAIL** — evidence located and it violates the condition.
- **UNVERIFIABLE** — the trace lacks the information to decide. Never counts as pass; for P0 it blocks the top verdict. A high UNVERIFIABLE count is itself a finding: the system doesn't log enough to be trusted.
- **N/A** — branch legitimately not exercised (e.g., no source outage occurred), with the reason recorded.

### Overall verdict

- **CORRECT** — all P0 PASS.
- **PARTIALLY CORRECT** — no P0 FAIL, some P0 UNVERIFIABLE.
- **INCORRECT** — any P0 FAIL.
- **UNJUDGEABLE** — artifacts insufficient to grade the majority of P0.
- **Single-strike override:** any fabrication (fixture presented as real, invented citation/metric/sequence, replay shown as live) → INCORRECT regardless of all other rows.

### Judge conduct rules

1. Grade artifacts, not narration. A fluent explanation of a check is not evidence the check ran.
2. Cite or downgrade (above).
3. Check negative evidence: excluded targets, retained failed runs, and contradictions must exist where the workflow claims they do.
4. Judge process correctness and claim↔evidence consistency, not whether the target choice was "right" biology.
5. Split compound checks; every row is atomic and observable.
6. Fixture-origin artifacts can prove plumbing, never biology.
7. Do not execute the system; grade the submitted run's persisted state.

### P0 checks — judge form

| ID | Judge looks at | PASS when | FAIL when |
|---|---|---|---|
| P0-1 Origin labels | Rendered report ↔ stored records, ≥5 spot-checked values across all views | Every value carries origin ∈ {real, cached_real, fixture}; cached shows original run time; validator rejects missing origin (cite test log) | Any unlabeled value, or cached rendered as live |
| P0-2 Contract validation | Validator code + rejection test logs | Missing evidence link, wrong target↔candidate, malformed job state each rejected with cited log; unknown numeric = null | Any malformed record persisted or rendered; 0 found where null belonged |
| P0-3 Target identity | DesignRequest + mapping records | Ensembl→UniProt mapped by ID, species/isoform checked, chain mapping cited; design launch log shows gate enforced | Symbol-only join, or launch without verification record |
| P0-4 Deterministic ranking | Ranking code + ≥2 runs on identical inputs + rubric version | Identical top-3; missing evidence rendered unknown/insufficient; unresolved → tie with explicit record | Order flip on identical inputs; missing silently scored favorably |
| P0-5 Direction/modality separation | Assessment records + exclusion log | Strong-association/wrong-modality target flagged or excluded with stated reason; feasibility shown separately | Modality mismatch blended into a composite score |
| P0-6 Honest metrics | Candidate records + results view | Every metric has method + model_version; ipTM/pLDDT labeled predicted geometry; minibinder never called antibody; next assay named | Any metric presented as measured affinity/Kd; unnamed method |
| P0-7 Donor-aware summaries | CellSummary records | Condition n = donors; per-donor summary precedes condition summary; sample sizes displayed | Raw cell counts presented as independent replicates |
| P0-8 Embedding identity | EmbeddingSlice manifest + alignment check log | Row-shuffle check logged with unchanged mapping; release mismatch rejected; invalid vectors excluded, never zero-filled | Join by row order; NaN/zero-filled vectors in distance calc |
| P0-9 Real durable job | DesignRun + artifact store + UI states | Real job ID resolvable; artifacts readable post-refresh; pending/failed/empty/timed-out states all render | Fixture presented as result; fake progress indication |
| P0-10 Paid-job idempotency | Submission logs + hash records | Duplicate submission deduped by input/model/config hash; double-click suppressed; retry can't bypass budget gate | Two paid runs from one logical request |
| P0-11 Generality | Two AgentRuns, different indications | Different real Open Targets pools, no code edits between runs (cite both run manifests + git log) | Same pool, hardcoded branch, or code diff between runs |
| P0-12 Honest failure branches | Run records + final_outcome | source_unavailable ⇒ qualified report; only validated requests triggered paid design; candidate tied to submitted target | Invented evidence after failure; outcome enum contradicted by records |

### P1 checks — judge form (same pattern, compact)

| ID | Judge looks at | PASS when |
|---|---|---|
| P1-1 render == store | Rendered chart data ↔ stored records by key | Every chart datum equals its stored value (float tolerance); element count = record count; null renders as gap not 0; origin survives DB→UI; recomputed totals equal stored raw |
| P1-2 Provenance walk | Rendered report ↔ evidence graph | Every claim's evidence_ids resolve to real records; clicking returns the same record |
| P1-3 Four data classes distinct | Evidence records + report | asserted-literature / observed-metadata / computed / predicted never collapsed into one |

### P2 checks — judge form

| ID | Judge looks at | PASS when |
|---|---|---|
| P2-1 Ranking stability re-run | Ranking code + rubric configs | Top-3 holds under 3–4 weightings (equal / genetics-heavy / tractability-heavy); flag if order flips |
| P2-2 Literature claim resolution | DOI/PMID via Crossref/Europe PMC | Identifier resolves; excerpt substring appears in fetched source for demo-critical claims; rest marked "identifier-verified" |

### Added judge rows (not in v1 — these grade the trace itself)

- **J-1 Trace authenticity:** tool_action_log shows real typed tool calls with arguments, timestamps, results, and per-action reasons; decisions reference evidence_ids. Narration over a static pipeline → FAIL (§18).
- **J-2 Claim↔evidence direction:** every report claim's evidence_ids resolve to records whose direction (supports/contradicts/context) matches the claim as stated.
- **J-3 Outcome honesty:** final_outcome ∈ declared enum and matches reality — `completed_candidate_report` only if a real candidate exists.

### Judge output format

Per check:

```
{ id, verdict, evidence_cited: [...], reasoning: "≤2 sentences" }
```

Then: overall verdict, then the UNVERIFIABLE list — which doubles as the team's logging-debt report.

### Grading philosophy (carried over from v1)

A run graded PARTIALLY CORRECT with cited evidence is more credible than CORRECT with thin citations. Partial-but-honest is the winning state; this rubric is built to reward exactly that.
