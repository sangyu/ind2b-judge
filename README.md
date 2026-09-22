# ind2b-judge

An independent trust grader for [indication2binder](https://github.com/lnairGT/Boston-CompBio-Hackathon)
runs. Reads the artifacts a run persisted and reports whether its claims are
supported — or says plainly that it could not tell.

```bash
pip install -e .
ind2b-judge runs/MONDO_0005061 --repo ../Boston-CompBio-Hackathon
```

```
MACHINE VERDICT: PARTIALLY CORRECT

  PASS          P0-2   [ENSG00000146648, ENSG00000171094, ENSG00000141736]
                        80 assessments; every favorable rating cites an existing evidence record
  PASS          P0-11  [agentrun_0265d5d055f5, agentrun_5b84bc4885fd]
                        two indications, different real pools, single commit 1d254f80
  UNVERIFIABLE  P0-7   [-]
                        no records of this type in the run

LOGGING DEBT (UNVERIFIABLE rows): P0-7, P0-8
SEMANTIC ROWS PENDING: 6 (require a model judge over the rendered report)
```

## Why this is a separate repository

A judge that can import the system it grades can be made to pass by that
system. Put `judge.py` inside the pipeline package and within a day someone
imports the scoring module to "reuse the logic" — and the grader is scoring
its own inputs. Trace-authenticity grading is specifically about not trusting
a system's self-report, so the judge must not have a way to ask.

The only input is persisted artifacts on disk. Nothing here imports `ind2b`;
the coupling is the stage file contract — `stage0_disease.json`,
`stage1_evidence.json`, `stage2_ranked_targets.csv`,
`stage5_specs/manifest.json` — which is the same surface the pipeline's own
`read()` helpers use. Dependencies are `pandas` alone, so the judge installs
without the pipeline's structural stack and runs in CI, on a laptop, or beside
a run directory copied off a cluster.

## What it grades, and what it refuses to

Rows split by **decidability**, not by convenience.

**Machine rows** are decided here, by deterministic checks. An LLM
re-deriving the determinism row nondeterministically would undermine the
very property it is grading, so those rows never go to a model.

| Row | Checks |
|---|---|
| P0-1 | every record carries a real/cached_real/fixture origin |
| P0-2 | every favorable rating cites an evidence record that exists |
| P0-3 | Ensembl→UniProt mapped by id; binding region and rationale present |
| P0-4 | repeated scoring of identical evidence gives identical order |
| P0-5 | modality mismatch flagged or excluded with a stated reason |
| P0-6 | metrics carry method + model_version; no predicted value named as a measured Kd |
| P0-7 | donor-aware summaries; cells are not counted as replicates |
| P0-8 | embeddings aligned by soma_joinid, release matched, no zero-filling |
| P0-9 | run states well-formed; failures retain a reason |
| P0-10 | no duplicate paid job for one input hash |
| P0-11 | two indications give different real pools at one commit |
| P0-12 | final_outcome matches what was actually persisted |
| J-4 | every cited evidence id appears in the tool action log |

**Semantic rows** are emitted as a worklist rather than decided: trace
authenticity, claim↔evidence direction, rendered-vs-stored spot checks,
provenance walks, and literature excerpt resolution. Those need prose read
against records, which a regex cannot do honestly.

`machine_overall` therefore covers the deterministic rows only, and says so in
its own output. Most fabrication is detectable only in the semantic pass, so a
machine `CORRECT` must never be read as "no fabrication."

## The property that matters most: silence is not success

A run that persists nothing scores `UNJUDGEABLE`, never `CORRECT`. Missing
records return `N/A` or `UNVERIFIABLE`. A single run cannot prove determinism
or generality — both are cross-run properties, so they stay `UNVERIFIABLE`
until two runs are supplied. And a `PASS` without a citation is downgraded
automatically, enforced in `Verdict.__post_init__` so it cannot be forgotten
rather than being a rule someone remembers.

J-4 exists because the rubric left a gap: a run that logs only its successful
steps would otherwise score well. Unlogged provenance is indistinguishable
from fabricated provenance.

## Exit codes

| Code | Verdict |
|---|---|
| 0 | CORRECT or PARTIALLY CORRECT |
| 1 | INCORRECT — a P0 row failed |
| 2 | UNJUDGEABLE — too little persisted to grade |

`PARTIALLY CORRECT` exits 0 deliberately. A run that honestly reports what it
could not verify is a passing run; the UNVERIFIABLE list is logging debt, not
a defect in the science. Failing a build on it would push a team toward
deleting the honest gaps rather than filling them.

## Usage

```bash
# one run
ind2b-judge runs/MONDO_0005061

# two indications — unlocks the generality row (P0-11)
ind2b-judge runs/MONDO_0005061 runs/MONDO_0005105 --repo ../Boston-CompBio-Hackathon

# add determinism evidence: JSON list of ranking orders from repeated stage-2 runs
ind2b-judge runs/* --rankings rankings.json --json report.json
```

`--repo` records the commit the pipeline ran at, which the generality row
cites to show no code changed between indications.

## Layout

```
ind2b_judge/
  records.py     typed records: schema_version, provenance, null-never-zero
  validators.py  14 deterministic checks, each naming the rule it enforces
  view.py        record → view projections (rendered value == stored value)
  bridge.py      ind2b run artifacts → records; imports nothing from ind2b
  harness.py     machine verdicts + the semantic worklist
  cli.py         ind2b-judge
tests/           95 tests, no network
docs/rubric.md   the confidence definitions and judge rubric this implements
```

## Tests

```bash
pip install -e ".[dev]"
pytest -q     # 95 passed
```

No network, no GPU, no pipeline checkout required — the bridge tests build
synthetic run directories in the shapes verified against a live run.
