"""Judge harness: grades a FINISHED run from persisted artifacts.

Third tier, separate from tests/ (code, fixtures, commit time) and pipeline/
(live records, gates behaviour). This one is post-hoc and adversarial: it takes
a run's stored state and asks whether the trust claims hold.

Division of labour, per trust-layer-and-judge-rubric.md:

  MACHINE rows  -- decidable from records by a deterministic check. Graded here,
                   by calling contracts/validators.py. The judge CITES these
                   rather than re-deriving them; an LLM re-deriving P0-4
                   (determinism) nondeterministically would undermine the very
                   row it grades.
  SEMANTIC rows -- require reading prose against records: does the cited
                   evidence's direction actually match the claim as stated,
                   does the excerpt appear in the source, is the trace real
                   reasoning or narration. Emitted as a task list for a model
                   judge; NOT decided here.

Verdicts follow the rubric: PASS / FAIL / UNVERIFIABLE / N/A. A PASS must cite
at least one record id -- an uncited PASS is downgraded to UNVERIFIABLE, since
the thing a trust layer exists to avoid is grading a self-report.

    python trust/judge_harness.py run.json
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Sequence

from .records import TERMINAL_RUN_STATES
from .validators import (
    Report,
    check_candidates,
    check_cell_summary,
    check_design_gate,
    check_design_target_identity,
    check_embedding_alignment,
    check_evidence_links,
    check_identity,
    check_modality_fit,
    check_no_duplicate_paid_run,
    check_origin_labels,
    check_run_state,
)

PASS, FAIL, UNVERIFIABLE, NA = "PASS", "FAIL", "UNVERIFIABLE", "N/A"
DESCOPED = "DESCOPED"

# Rows the project decided not to implement. They are reported, not deleted:
# a requirement dropped by decision is a different thing from one a run failed
# or could not evidence, and the difference has to survive into the report or
# the scope of the verdict is overstated. DESCOPED rows do not count toward
# the overall verdict in either direction.
DESCOPED_ROWS: dict[str, str] = {
    "P0-7": ("CELLxGENE Census cellular context was descoped for this build; the "
             "pipeline carries no per-donor expression summaries to grade."),
    "P0-8": ("CELLxGENE Census embeddings were descoped for this build; the "
             "pipeline retrieves no precomputed cell embeddings to grade."),
}


@dataclass
class Verdict:
    id: str
    verdict: str
    evidence_cited: list[str] = field(default_factory=list)
    reasoning: str = ""
    tier: str = "machine"

    def __post_init__(self):
        # Rubric rule: a PASS without citation is invalid -> UNVERIFIABLE.
        # Enforced structurally so it cannot be forgotten.
        if self.verdict == PASS and not self.evidence_cited:
            self.verdict = UNVERIFIABLE
            self.reasoning = (self.reasoning + " [downgraded: PASS without citation]").strip()


def _v(rid: str, violations, cited: Sequence[str], ok_note: str) -> Verdict:
    """Machine row: violations present -> FAIL; none -> PASS with citations."""
    cited = [c for c in cited if c]
    if violations:
        return Verdict(rid, FAIL, cited or ["(records inspected)"],
                       "; ".join(f"{x.check}: {x.detail}" for x in violations[:3])[:400])
    if not cited:
        return Verdict(rid, UNVERIFIABLE, [], "no records of this type in the run")
    return Verdict(rid, PASS, cited, ok_note)


# --- machine-decidable rows -------------------------------------------------

def grade_machine_rows(run: dict[str, Any]) -> list[Verdict]:
    """Grade every P0 row that a deterministic check can decide.

    `run` is the persisted run: parsed record objects keyed by type. Missing
    record types yield UNVERIFIABLE, never PASS -- an absent artifact is a
    logging gap, not a success.
    """
    brief = run.get("brief")
    targets = run.get("targets", [])
    evidence = run.get("evidence", [])
    assessments = run.get("assessments", [])
    cell_slices = run.get("cell_slices", [])
    cell_summaries = run.get("cell_summaries", [])
    embedding_slices = run.get("embedding_slices", [])
    design_request = run.get("design_request")
    design_runs = run.get("design_runs", [])
    candidates = run.get("candidates", [])

    out: list[Verdict] = []

    # P0-1 origin labels
    everything = [*evidence, *cell_summaries, *candidates, *design_runs]
    out.append(_v("P0-1", check_origin_labels(everything),
                  [getattr(r, "evidence_id", None) or getattr(r, "candidate_id", None)
                   or getattr(r, "run_id", None) or getattr(r, "summary_id", None)
                   for r in everything][:5],
                  f"{len(everything)} records all carry an origin label"))

    # P0-2 contract validation (evidence links + provenance)
    viol = check_evidence_links(assessments, evidence)
    out.append(_v("P0-2", viol, [a.target_id for a in assessments][:5],
                  f"{len(assessments)} assessments; every favorable rating cites "
                  "an existing evidence record"))

    # P0-3 target identity
    if design_request is not None:
        out.append(_v("P0-3", check_design_target_identity(design_request, targets),
                      [design_request.design_id],
                      "Ensembl->UniProt mapped by id; binding region and MoA rationale present"))
    else:
        out.append(Verdict("P0-3", NA, [], "no design request in this run"))

    # P0-4 determinism: NOT decidable from a single persisted run. The rubric
    # wants two runs on identical inputs; if the run carries only one ranking,
    # say so rather than guessing.
    rankings = run.get("rankings", [])
    if len(rankings) >= 2:
        same = len({tuple(r) for r in rankings}) == 1
        out.append(Verdict("P0-4", PASS if same else FAIL,
                           [f"ranking[{i}]" for i in range(len(rankings))],
                           "identical top-N across repeated runs" if same
                           else f"order differs across runs: {rankings}"))
    else:
        out.append(Verdict("P0-4", UNVERIFIABLE, [],
                           "fewer than two recorded rankings on identical inputs; "
                           "harness cannot confirm determinism from one run"))

    # P0-5 direction/modality separation
    out.append(_v("P0-5", check_modality_fit(assessments),
                  [a.target_id for a in assessments][:5],
                  "modality mismatches carry an exclusion reason or feasibility rationale"))

    # P0-6 honest metrics
    metric_viol, cited = [], []
    for r in design_runs:
        cs = [c for c in candidates if c.run_id == r.run_id]
        metric_viol += check_candidates(cs, r)
        cited += [c.candidate_id for c in cs]
    if candidates:
        out.append(_v("P0-6", metric_viol, cited[:5],
                      "every metric carries method + model_version; no predicted "
                      "value named as a measured affinity"))
    else:
        out.append(Verdict("P0-6", NA, [], "no candidates produced in this run"))

    # P0-7 donor-aware summaries
    if "P0-7" in DESCOPED_ROWS:
        out.append(Verdict("P0-7", DESCOPED, [], DESCOPED_ROWS["P0-7"]))
    ds_viol, ds_cited = [], []
    for s in cell_summaries:
        ds_viol += check_cell_summary(s, cell_slices)
        ds_cited.append(s.summary_id)
    if "P0-7" not in DESCOPED_ROWS:
        out.append(_v("P0-7", ds_viol, ds_cited[:5],
                      "per-donor summaries precede condition summaries; sample sizes recorded"))

    # P0-8 embedding identity
    if "P0-8" in DESCOPED_ROWS:
        out.append(Verdict("P0-8", DESCOPED, [], DESCOPED_ROWS["P0-8"]))
    em_viol, em_cited = [], []
    for e in embedding_slices:
        em_viol += check_embedding_alignment(e, cell_slices)
        em_cited.append(e.embedding_slice_id)
    if "P0-8" not in DESCOPED_ROWS:
        out.append(_v("P0-8", em_viol, em_cited[:5],
                      "release matched, alignment by soma_joinid, coverage monotonic, "
                      "no zero-filled vectors"))

    # P0-9 real durable job (state honesty is machine-checkable; artifact
    # readability after refresh is not -- that half goes to the semantic list)
    rs_viol, rs_cited = [], []
    for r in design_runs:
        rs_viol += check_run_state(r, [c for c in candidates if c.run_id == r.run_id])
        rs_cited.append(r.run_id)
    if design_runs:
        out.append(_v("P0-9", rs_viol, rs_cited,
                      "run states well-formed; terminal states timestamped; "
                      "failures retain a reason"))
    else:
        out.append(Verdict("P0-9", NA, [], "no design run in this run"))

    # P0-10 paid-job idempotency
    if design_request is not None:
        prior = [r for r in design_runs if r.input_hash == design_request.input_hash()]
        if len(prior) > 1:
            out.append(Verdict("P0-10", FAIL, [r.run_id for r in prior],
                               f"{len(prior)} runs share one input hash -- "
                               "duplicate paid submission"))
        else:
            hashes = [r.input_hash for r in design_runs]
            out.append(_v("P0-10", [] if all(hashes) else [_MissingHash()],
                          [r.run_id for r in design_runs],
                          "each design run carries a distinct input hash"))
    else:
        out.append(Verdict("P0-10", NA, [], "no design request in this run"))

    # P0-11 generality: needs two AgentRuns at the same commit
    agent_runs = run.get("agent_runs", [])
    commits = {a.git_commit for a in agent_runs if getattr(a, "git_commit", None)}
    pools = [tuple(sorted(a.candidate_pool_scope.get("target_ids", [])))
             for a in agent_runs if a.candidate_pool_scope]
    if len(agent_runs) < 2:
        out.append(Verdict("P0-11", UNVERIFIABLE, [],
                           f"{len(agent_runs)} agent run(s) recorded; generality needs two "
                           "indications"))
    elif not commits or len(commits) > 1:
        out.append(Verdict("P0-11", FAIL, [a.run_id for a in agent_runs],
                           f"runs span {len(commits) or 'unrecorded'} commits; "
                           "cannot show 'no code edits between runs'"))
    elif len(set(pools)) < 2:
        out.append(Verdict("P0-11", FAIL, [a.run_id for a in agent_runs],
                           "two indications returned the same target pool"))
    else:
        out.append(Verdict("P0-11", PASS, [a.run_id for a in agent_runs],
                           f"two indications, different real pools, single commit "
                           f"{list(commits)[0][:8]}"))

    # P0-12 honest failure branches
    out.extend(_grade_outcome_honesty(run))

    # Completeness row (gap identified in the rubric): selective logging must
    # not pass. Every evidence id cited in an assessment has to appear in the
    # tool action log, or provenance is invisible to the judge.
    out.append(_grade_log_completeness(run))

    return out


class _MissingHash:
    check = "run.no_input_hash"
    detail = "a design run has no input hash; duplicate guard cannot work"


def _grade_outcome_honesty(run: dict[str, Any]) -> list[Verdict]:
    agent_runs = run.get("agent_runs", [])
    candidates = run.get("candidates", [])
    out = []
    for a in agent_runs:
        oc = a.final_outcome
        if oc is None:
            out.append(Verdict("P0-12", UNVERIFIABLE, [a.run_id],
                               "run has no final_outcome recorded"))
        elif oc == "completed_candidate_report" and not candidates:
            out.append(Verdict("P0-12", FAIL, [a.run_id],
                               "outcome claims a completed candidate report but no "
                               "candidate records exist"))
        else:
            out.append(Verdict("P0-12", PASS, [a.run_id],
                               f"final_outcome {oc!r} consistent with persisted records"))
    return out or [Verdict("P0-12", UNVERIFIABLE, [], "no agent run recorded")]


def _grade_log_completeness(run: dict[str, Any]) -> Verdict:
    """Every cited evidence id must trace to a logged tool action.

    Closes the selective-logging gap: a run that logs only its successful steps
    would otherwise score well. Unlogged provenance is invisible, and invisible
    provenance is indistinguishable from fabricated provenance.
    """
    agent_runs = run.get("agent_runs", [])
    assessments = run.get("assessments", [])
    cited = {eid for a in assessments for r in a.ratings for eid in r.evidence_ids}
    if not cited:
        return Verdict("J-4", UNVERIFIABLE, [], "no evidence ids cited by any assessment")
    logged = set()
    for a in agent_runs:
        for action in a.tool_action_log:
            logged.update(action.result_refs)
    missing = sorted(cited - logged)
    if missing:
        return Verdict("J-4", FAIL, sorted(cited)[:5],
                       f"{len(missing)} cited evidence id(s) never appear in any "
                       f"tool_action_log entry: {missing[:5]}")
    return Verdict("J-4", PASS, sorted(cited)[:5],
                   f"all {len(cited)} cited evidence ids trace to logged tool actions")


# --- semantic rows: emitted for a model judge, not decided here -------------

SEMANTIC_ROWS = [
    ("J-1", "Trace authenticity",
     "Read agent_runs[].tool_action_log. Do the actions show real typed tool calls "
     "with arguments, results and per-action reasons that respond to what came back "
     "-- or is it narration over a fixed pipeline? Cite specific log entries."),
    ("J-2", "Claim<->evidence direction",
     "For each claim in the rendered report, resolve its evidence_ids and check the "
     "record's `direction` (supports/contradicts/context) matches the claim AS STATED. "
     "A contradicting record cited in support of a claim is a FAIL."),
    ("P0-1b", "Rendered vs stored spot check",
     "Pick >=5 values across the report views and confirm each equals its stored "
     "record value, and that cached values display their original run time."),
    ("P1-2", "Provenance walk",
     "Follow >=3 claims from the report to their evidence records and back. Every "
     "evidence_id must resolve to a real record."),
    ("P1-3", "Four data classes distinct",
     "Confirm asserted-literature, observed-metadata, computed and predicted values "
     "are never collapsed into one presentation."),
    ("P2-2", "Literature claim resolution",
     "For demo-critical claims, resolve the DOI/PMID and confirm the stored excerpt "
     "appears in the fetched source. Mark the rest 'identifier-verified'."),
]


def semantic_tasks(run: dict[str, Any]) -> list[dict[str, str]]:
    """The rows a deterministic check cannot decide. For the model judge."""
    return [{"id": rid, "name": name, "instruction": instr, "tier": "semantic"}
            for rid, name, instr in SEMANTIC_ROWS]


# --- aggregation ------------------------------------------------------------

P0_IDS = {f"P0-{i}" for i in range(1, 13)} | {"J-4"}


def overall_verdict(verdicts: Sequence[Verdict]) -> str:
    """Rubric aggregation. Fabrication override is applied by the caller, since
    only the semantic pass can detect most fabrication."""
    p0 = [v for v in verdicts if v.id in P0_IDS]
    if not p0:
        return "UNJUDGEABLE"
    if any(v.verdict == FAIL for v in p0):
        return "INCORRECT"
    judged = [v for v in p0 if v.verdict not in (NA, DESCOPED)]
    if not judged or sum(v.verdict == UNVERIFIABLE for v in judged) > len(judged) / 2:
        return "UNJUDGEABLE"
    if any(v.verdict == UNVERIFIABLE for v in judged):
        return "PARTIALLY CORRECT"
    return "CORRECT"


def grade(run: dict[str, Any]) -> dict[str, Any]:
    """Full harness pass: machine verdicts + the semantic worklist."""
    verdicts = grade_machine_rows(run)
    machine_overall = overall_verdict(verdicts)
    return {
        "machine_verdicts": [asdict(v) for v in verdicts],
        "machine_overall": machine_overall,
        "semantic_tasks": semantic_tasks(run),
        "unverifiable": [v.id for v in verdicts if v.verdict == UNVERIFIABLE],
        "descoped": {v.id: v.reasoning for v in verdicts if v.verdict == DESCOPED},
        "note": ("machine_overall covers deterministic rows only. The final verdict "
                 "requires the semantic pass; any fabrication found there forces "
                 "INCORRECT regardless of these rows."),
    }


def render(result: dict[str, Any]) -> str:
    lines = [f"MACHINE VERDICT: {result['machine_overall']}", ""]
    for v in result["machine_verdicts"]:
        cites = ", ".join(str(c) for c in v["evidence_cited"][:3]) or "-"
        lines.append(f"  {v['verdict']:13s} {v['id']:6s} [{cites}]")
        if v["reasoning"]:
            lines.append(f"                        {v['reasoning'][:150]}")
    descoped = [v["id"] for v in result["machine_verdicts"] if v["verdict"] == DESCOPED]
    if descoped:
        lines += ["", "DESCOPED (decided out of scope, not graded): " + ", ".join(descoped)]
    if result["unverifiable"]:
        lines += ["", "LOGGING DEBT (UNVERIFIABLE rows): " + ", ".join(result["unverifiable"])]
    lines += ["", f"SEMANTIC ROWS PENDING: {len(result['semantic_tasks'])} "
                  "(require a model judge over the rendered report)"]
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: python trust/judge_harness.py <run.json>")
    print(render(grade(json.load(open(sys.argv[1])))))
