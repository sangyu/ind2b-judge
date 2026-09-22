"""Unit tests: BINDING layer (design gate, run state, candidate honesty).

Owner: binder-design workstream. Grades the CODE on fixtures at commit time.
No GPU, no Modal, no network -- these must stay runnable during a demo.

Covers judge rows P0-6, P0-9 (partial), P0-10.

    python tests/test_binding.py
"""

from __future__ import annotations

import sys

from ind2b_judge.records import (  # noqa: E402
    Assessment, Candidate, CellSlice, CellSummary, CriterionRating,
    DesignRequest, DesignRun, EmbeddingSlice, Evidence, Metric, Provenance,
    ResearchBrief, Target,
)
from ind2b_judge.validators import (  # noqa: E402
    check_candidates, check_cell_summary, check_design_gate, check_determinism,
    check_embedding_alignment, check_evidence_links, check_modality_fit,
    check_no_duplicate_paid_run, check_run_state, validate_all,
)

RELEASE = "2025-01-30"
REAL = Provenance(origin="real", source="opentargets:graphql:v4")


def _codes(violations) -> set[str]:
    return {v.check for v in violations}


def _slice(**kw):
    base = dict(slice_id="cs1", census_release=RELEASE, organism="Homo sapiens")
    base.update(kw)
    return CellSlice(**base)


def _emb(**kw):
    base = dict(embedding_slice_id="es1", cell_slice_id="cs1", embedding_name="scvi",
                census_release=RELEASE, organism="Homo sapiens",
                aligned_cell_ids_ref="vol://ids.npy", vectors_ref="vol://vec.npy",
                valid_row_mask_ref="vol://mask.npy",
                requested_count=1000, retrieved_count=980, valid_count=950)
    base.update(kw)
    return EmbeddingSlice(**base)


def _assessments():
    return [
        Assessment(target_id=f"ENSG{i:011d}", rubric_version="v1",
                   ratings=[CriterionRating(criterion="genetic", rating="moderate",
                                            evidence_ids=["ev1"])])
        for i in range(5)
    ]


def _request(**kw):
    base = dict(design_id="d1", brief_id="b1", selected_target_id="ENSG00000146648",
                binding_region={"chain": "A", "residues": [1, 2, 3]},
                moa_rationale="blocks ligand engagement", evidence_ids=["ev1"],
                model="bindcraft", model_config={"n": 8}, sequence_checksum="abc")
    base.update(kw)
    return DesignRequest(**base)


def test_unreviewed_request_cannot_launch():
    assert "gate.unreviewed_design" in _codes(check_design_gate(_request()))


def test_approved_request_passes_gate():
    r = _request(review_status="approved", reviewed_by="scientific owner")
    assert check_design_gate(r) == []


def test_approval_without_reviewer_is_caught():
    assert "gate.anonymous_approval" in _codes(
        check_design_gate(_request(review_status="approved")))


def test_duplicate_submission_is_blocked():
    """s13: repeated submission must not start duplicate paid jobs."""
    r = _request(review_status="approved", reviewed_by="owner")
    existing = DesignRun(run_id="run1", design_id="d1", execution_route="modal:gpu",
                         status="running", input_hash=r.input_hash())
    assert "gate.duplicate_paid_run" in _codes(check_no_duplicate_paid_run(r, [existing]))


def test_failed_run_does_not_block_resubmission():
    r = _request(review_status="approved", reviewed_by="owner")
    failed = DesignRun(run_id="run1", design_id="d1", execution_route="modal:gpu",
                       status="failed", input_hash=r.input_hash(), error="OOM")
    assert check_no_duplicate_paid_run(r, [failed]) == []


def test_config_change_produces_a_new_hash():
    a = _request(model_config={"n": 8})
    b = _request(model_config={"n": 16})
    assert a.input_hash() != b.input_hash()


def test_hash_is_insensitive_to_key_order():
    a = _request(model_config={"n": 8, "seed": 1})
    b = _request(model_config={"seed": 1, "n": 8})
    assert a.input_hash() == b.input_hash()


def test_candidates_cannot_precede_success():
    run = DesignRun(run_id="r1", design_id="d1", execution_route="modal:gpu",
                    status="running", input_hash="h", execution_handle="fc-1")
    c = Candidate(candidate_id="c1", run_id="r1")
    assert "run.candidates_before_success" in _codes(check_run_state(run, [c]))


def test_succeeded_run_with_zero_candidates_is_valid():
    """s8: a succeeded compute job may legitimately have no acceptable candidate."""
    run = DesignRun(run_id="r1", design_id="d1", execution_route="modal:gpu",
                    status="succeeded", input_hash="h", execution_handle="fc-1",
                    finished_at="2026-09-22T16:00:00Z")
    assert check_run_state(run, []) == []


def test_predicted_affinity_named_as_kd_is_rejected():
    run = DesignRun(run_id="r1", design_id="d1", execution_route="modal:gpu",
                    status="succeeded", input_hash="h")
    c = Candidate(candidate_id="c1", run_id="r1", sequence="MKV",
                  metrics=[Metric(name="Kd", value=1e-9, method="model score",
                                  is_prediction=True)],
                  provenance=Provenance(origin="real", source="bindcraft"))
    assert "metric.predicted_affinity_as_measurement" in _codes(check_candidates([c], run))


def test_labelled_prediction_passes():
    run = DesignRun(run_id="r1", design_id="d1", execution_route="modal:gpu",
                    status="succeeded", input_hash="h")
    c = Candidate(candidate_id="c1", run_id="r1", sequence="MKV",
                  metrics=[Metric(name="ipae", value=7.2, method="AF2 interface pAE",
                                  model_version="af2-multimer-v3", is_prediction=True)],
                  provenance=Provenance(origin="real", source="bindcraft"))
    assert check_candidates([c], run) == []


def test_fixture_candidate_is_flagged():
    run = DesignRun(run_id="r1", design_id="d1", execution_route="modal:gpu",
                    status="succeeded", input_hash="h")
    c = Candidate(candidate_id="c1", run_id="r1", sequence="MKV",
                  provenance=Provenance(origin="fixture", source="demo"))
    assert "candidate.fixture_origin" in _codes(check_candidates([c], run))

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
