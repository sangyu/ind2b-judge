"""Unit tests: the JUDGE harness itself.

The judge is the thing that says whether to trust the run, so it needs its own
adversarial tests -- most importantly that it cannot be fooled by a run which
simply omits its artifacts. Silence must score UNVERIFIABLE, never PASS.

    python tests/test_judge.py
"""

from __future__ import annotations

import sys

from ind2b_judge.records import (  # noqa: E402
    AgentRun, Assessment, Candidate, CellSlice, CellSummary, CriterionRating,
    DesignRequest, DesignRun, EmbeddingSlice, Evidence, Metric, Provenance,
    ResearchBrief, Target, ToolAction,
)
from ind2b_judge.harness import (  # noqa: E402
    FAIL, NA, PASS, UNVERIFIABLE, Verdict, grade, grade_machine_rows,
    overall_verdict, semantic_tasks,
)

REAL = Provenance(origin="real", source="opentargets:graphql:v4")
RELEASE = "2025-01-30"
EGFR = "ENSG00000146648"


def _by_id(verdicts):
    return {v.id: v for v in verdicts}


def _clean_run(**over):
    ev = Evidence(evidence_id="ev1", subject_id=EGFR, predicate="associated_with",
                  direction="supports", source_url="https://platform.opentargets.org/",
                  provenance=REAL)
    run = {
        "brief": ResearchBrief(brief_id="b1", raw_indication="lung adenocarcinoma",
                               disease_id="MONDO_0005061", status="confirmed",
                               provenance=REAL),
        "targets": [Target(target_id=EGFR, gene_symbol="EGFR", stable_gene_id=EGFR,
                           protein_accession="P00533", provenance=REAL)],
        "evidence": [ev],
        "assessments": [Assessment(
            target_id=EGFR, rubric_version="v1",
            ratings=[CriterionRating(criterion="genetic", rating="strong",
                                     evidence_ids=["ev1"])],
            design_feasibility="strong",
            design_feasibility_rationale="single-pass type I membrane protein")],
        "agent_runs": [AgentRun(
            run_id="ar1", brief_id="b1", final_outcome="evidence_report_only",
            git_commit="abc1234",
            tool_action_log=[ToolAction(tool_name="discover_targets",
                                        result_refs=["ev1"], reason="pool retrieval")])],
        "cell_slices": [], "cell_summaries": [], "embedding_slices": [],
        "design_request": None, "design_runs": [], "candidates": [],
    }
    run.update(over)
    return run


# --- the central property: silence is not success ---------------------------

def test_empty_run_is_unjudgeable_not_correct():
    """A run with no artifacts must never grade CORRECT."""
    result = grade({})
    assert result["machine_overall"] in {"UNJUDGEABLE", "PARTIALLY CORRECT"}
    assert result["machine_overall"] != "CORRECT"


def test_uncited_pass_is_downgraded():
    """Rubric: a PASS without citation is invalid."""
    v = Verdict("X-1", PASS, [], "looks fine")
    assert v.verdict == UNVERIFIABLE
    assert "downgraded" in v.reasoning


def test_cited_pass_survives():
    v = Verdict("X-1", PASS, ["ev1"], "checked")
    assert v.verdict == PASS


def test_missing_records_yield_na_or_unverifiable_never_pass():
    verdicts = _by_id(grade_machine_rows(_clean_run()))
    # no design request, no candidates, no design runs in the clean evidence-only run
    for rid in ("P0-3", "P0-6", "P0-9", "P0-10"):
        assert verdicts[rid].verdict in {NA, UNVERIFIABLE}, rid


# --- machine rows detect real violations ------------------------------------

def test_unsupported_favorable_rating_fails_p0_2():
    run = _clean_run(assessments=[Assessment(
        target_id=EGFR, rubric_version="v1",
        ratings=[CriterionRating(criterion="genetic", rating="strong", evidence_ids=[])],
        design_feasibility="strong", design_feasibility_rationale="membrane protein")])
    assert _by_id(grade_machine_rows(run))["P0-2"].verdict == FAIL


def test_clean_evidence_passes_p0_2():
    assert _by_id(grade_machine_rows(_clean_run()))["P0-2"].verdict == PASS


def test_unflagged_modality_mismatch_fails_p0_5():
    run = _clean_run(assessments=[Assessment(
        target_id=EGFR, rubric_version="v1",
        ratings=[CriterionRating(criterion="genetic", rating="strong",
                                 evidence_ids=["ev1"])],
        design_feasibility="weak")])
    assert _by_id(grade_machine_rows(run))["P0-5"].verdict == FAIL


def test_determinism_unverifiable_from_a_single_ranking():
    """P0-4 needs two runs on identical inputs; one is not enough to claim PASS."""
    v = _by_id(grade_machine_rows(_clean_run()))["P0-4"]
    assert v.verdict == UNVERIFIABLE


def test_determinism_fails_on_divergent_rankings():
    run = _clean_run(rankings=[["A", "B", "C"], ["B", "A", "C"]])
    assert _by_id(grade_machine_rows(run))["P0-4"].verdict == FAIL


def test_determinism_passes_on_identical_rankings():
    run = _clean_run(rankings=[["A", "B", "C"], ["A", "B", "C"]])
    assert _by_id(grade_machine_rows(run))["P0-4"].verdict == PASS


# --- P0-12: outcome honesty --------------------------------------------------

def test_claimed_candidate_report_without_candidates_fails():
    run = _clean_run(agent_runs=[AgentRun(
        run_id="ar1", brief_id="b1", final_outcome="completed_candidate_report",
        git_commit="abc1234")])
    assert _by_id(grade_machine_rows(run))["P0-12"].verdict == FAIL


def test_honest_evidence_only_outcome_passes():
    assert _by_id(grade_machine_rows(_clean_run()))["P0-12"].verdict == PASS


# --- J-4: selective logging cannot pass -------------------------------------

def test_cited_evidence_missing_from_log_fails():
    """The gap the rubric left open: a run that logs only its wins."""
    run = _clean_run(agent_runs=[AgentRun(
        run_id="ar1", brief_id="b1", final_outcome="evidence_report_only",
        git_commit="abc1234", tool_action_log=[])])   # cites ev1, logs nothing
    v = _by_id(grade_machine_rows(run))["J-4"]
    assert v.verdict == FAIL and "ev1" in v.reasoning


def test_fully_logged_evidence_passes():
    assert _by_id(grade_machine_rows(_clean_run()))["J-4"].verdict == PASS


# --- P0-11: generality ------------------------------------------------------

def test_one_indication_is_unverifiable_for_generality():
    assert _by_id(grade_machine_rows(_clean_run()))["P0-11"].verdict == UNVERIFIABLE


def test_two_indications_same_pool_fails():
    runs = [AgentRun(run_id=f"ar{i}", brief_id=f"b{i}", git_commit="abc1234",
                     final_outcome="evidence_report_only",
                     candidate_pool_scope={"target_ids": [EGFR]},
                     tool_action_log=[ToolAction(tool_name="t", result_refs=["ev1"])])
            for i in (1, 2)]
    assert _by_id(grade_machine_rows(_clean_run(agent_runs=runs)))["P0-11"].verdict == FAIL


def test_two_indications_different_pools_same_commit_passes():
    runs = [AgentRun(run_id="ar1", brief_id="b1", git_commit="abc1234",
                     final_outcome="evidence_report_only",
                     candidate_pool_scope={"target_ids": [EGFR]},
                     tool_action_log=[ToolAction(tool_name="t", result_refs=["ev1"])]),
            AgentRun(run_id="ar2", brief_id="b2", git_commit="abc1234",
                     final_outcome="evidence_report_only",
                     candidate_pool_scope={"target_ids": ["ENSG00000141510"]},
                     tool_action_log=[ToolAction(tool_name="t", result_refs=["ev1"])])]
    assert _by_id(grade_machine_rows(_clean_run(agent_runs=runs)))["P0-11"].verdict == PASS


def test_code_edit_between_runs_fails_generality():
    runs = [AgentRun(run_id="ar1", brief_id="b1", git_commit="abc1234",
                     candidate_pool_scope={"target_ids": [EGFR]},
                     final_outcome="evidence_report_only",
                     tool_action_log=[ToolAction(tool_name="t", result_refs=["ev1"])]),
            AgentRun(run_id="ar2", brief_id="b2", git_commit="def5678",
                     candidate_pool_scope={"target_ids": ["ENSG00000141510"]},
                     final_outcome="evidence_report_only",
                     tool_action_log=[ToolAction(tool_name="t", result_refs=["ev1"])])]
    assert _by_id(grade_machine_rows(_clean_run(agent_runs=runs)))["P0-11"].verdict == FAIL


# --- aggregation ------------------------------------------------------------

def test_any_p0_fail_makes_the_run_incorrect():
    vs = [Verdict("P0-1", PASS, ["x"]), Verdict("P0-2", FAIL, ["y"])]
    assert overall_verdict(vs) == "INCORRECT"


def test_unverifiable_gives_partially_correct():
    vs = [Verdict("P0-1", PASS, ["x"]), Verdict("P0-2", PASS, ["y"]),
          Verdict("P0-3", UNVERIFIABLE, [])]
    assert overall_verdict(vs) == "PARTIALLY CORRECT"


def test_mostly_unverifiable_is_unjudgeable():
    vs = [Verdict("P0-1", PASS, ["x"]), Verdict("P0-2", UNVERIFIABLE, []),
          Verdict("P0-3", UNVERIFIABLE, []), Verdict("P0-4", UNVERIFIABLE, [])]
    assert overall_verdict(vs) == "UNJUDGEABLE"


def test_na_rows_do_not_count_against_the_verdict():
    vs = [Verdict("P0-1", PASS, ["x"]), Verdict("P0-2", PASS, ["y"]),
          Verdict("P0-3", NA, [])]
    assert overall_verdict(vs) == "CORRECT"


# --- semantic separation ----------------------------------------------------

def test_semantic_rows_are_emitted_not_decided():
    tasks = semantic_tasks(_clean_run())
    ids = {t["id"] for t in tasks}
    assert {"J-1", "J-2", "P2-2"} <= ids
    assert all(t["tier"] == "semantic" for t in tasks)
    # none of them appear in the machine verdicts
    machine_ids = {v.id for v in grade_machine_rows(_clean_run())}
    assert not (ids & machine_ids)


def test_grade_reports_machine_scope_honestly():
    result = grade(_clean_run())
    assert "semantic pass" in result["note"]
    assert result["semantic_tasks"]


if __name__ == "__main__":
    import traceback
    fns = {k: v for k, v in sorted(globals().items()) if k.startswith("test_")}
    failed = 0
    for name, fn in fns.items():
        try:
            fn()
            print(f"  pass  {name}")
        except Exception:
            failed += 1
            print(f"  FAIL  {name}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
