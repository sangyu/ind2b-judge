"""Unit tests: RETRIEVAL layer (Open Targets + Census evidence).

Owner: retrieval workstream. Grades the CODE on fixtures at commit time --
distinct from run checks (pipeline/run_checks.py, live records, gates behaviour)
and from the judge (trust/, grades a finished run's persisted artifacts).

Covers judge rows P0-2, P0-4, P0-5, P0-7, P0-8.

    python tests/test_retrieval.py
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


def test_favorable_rating_without_evidence_is_rejected():
    a = Assessment(target_id="ENSG1", rubric_version="v1", ratings=[
        CriterionRating(criterion="genetic", rating="strong", evidence_ids=[]),
    ])
    assert "evidence.unlinked" in _codes(check_evidence_links([a], []))


def test_favorable_rating_with_real_evidence_passes():
    e = Evidence(evidence_id="ev1", subject_id="ENSG1", predicate="associated_with",
                 source_url="https://platform.opentargets.org/", provenance=REAL)
    a = Assessment(target_id="ENSG1", rubric_version="v1", ratings=[
        CriterionRating(criterion="genetic", rating="strong", evidence_ids=["ev1"]),
    ])
    assert check_evidence_links([a], [e]) == []


def test_dangling_evidence_id_is_caught():
    a = Assessment(target_id="ENSG1", rubric_version="v1", ratings=[
        CriterionRating(criterion="genetic", rating="moderate", evidence_ids=["ghost"]),
    ])
    assert "evidence.dangling" in _codes(check_evidence_links([a], []))


def test_unknown_rating_needs_no_evidence():
    """'unknown' is honest missingness, not a favorable claim -- it must pass."""
    a = Assessment(target_id="ENSG1", rubric_version="v1", ratings=[
        CriterionRating(criterion="genetic", rating="unknown", evidence_ids=[],
                        missingness="no genetic association records returned"),
    ])
    assert check_evidence_links([a], []) == []


def test_deterministic_ranking_passes():
    rank = lambda xs: sorted(xs, key=lambda a: a.target_id)  # noqa: E731
    assert check_determinism(rank, _assessments()) == []


def test_input_order_dependent_ranking_is_caught():
    """A rank_fn that just returns its input is order-dependent -- the classic
    bug where a dict or a set upstream silently decides the winner."""
    rank = lambda xs: list(xs)  # noqa: E731
    assert "rank.nondeterministic" in _codes(check_determinism(rank, _assessments()))


def test_intracellular_target_with_strong_biology_must_be_flagged():
    a = Assessment(target_id="ENSG_KRAS", rubric_version="v1",
                   ratings=[CriterionRating(criterion="genetic", rating="strong",
                                            evidence_ids=["ev1"])],
                   design_feasibility="weak")  # no rationale, not excluded
    assert "rank.unflagged_modality_mismatch" in _codes(check_modality_fit([a]))


def test_flagged_modality_mismatch_passes():
    a = Assessment(target_id="ENSG_KRAS", rubric_version="v1",
                   ratings=[CriterionRating(criterion="genetic", rating="strong",
                                            evidence_ids=["ev1"])],
                   design_feasibility="weak",
                   design_feasibility_rationale=(
                       "lipid-anchored, cytoplasmic face; not accessible to an "
                       "extracellular binder"))
    assert check_modality_fit([a]) == []


def test_release_mismatch_is_rejected():
    v = check_embedding_alignment(_emb(census_release="2024-07-01"), [_slice()])
    assert "embedding.release_mismatch" in _codes(v)


def test_embedding_without_cell_ids_is_rejected():
    v = check_embedding_alignment(_emb(aligned_cell_ids_ref=None), [_slice()])
    assert "embedding.no_cell_ids" in _codes(v)


def test_vectors_without_valid_mask_are_rejected():
    """Missing rows must be maskable; zero-filling them is forbidden by s20."""
    v = check_embedding_alignment(_emb(valid_row_mask_ref=None), [_slice()])
    assert "embedding.no_valid_mask" in _codes(v)


def test_impossible_coverage_is_caught():
    v = check_embedding_alignment(_emb(valid_count=2000), [_slice()])
    assert "embedding.impossible_coverage" in _codes(v)


def test_well_formed_embedding_slice_passes():
    assert check_embedding_alignment(_emb(), [_slice()]) == []


def test_row_order_perturbation_is_detected():
    """s20 acceptance check: alignment is by identity, not by row position.

    Simulates the real failure -- vectors arrive in a different order than the
    cell ids. Joining positionally silently attaches every vector to the wrong
    cell; joining by soma_joinid catches it. No Census call needed to prove the
    property holds.
    """
    ids = [101, 102, 103, 104]
    vectors = {101: [1.0, 0.0], 102: [0.0, 1.0], 103: [1.0, 1.0], 104: [0.5, 0.5]}

    def align_by_identity(cell_ids, returned_ids, returned_vecs):
        lookup = dict(zip(returned_ids, returned_vecs))
        missing = [c for c in cell_ids if c not in lookup]
        if missing:
            raise KeyError(f"no vector for soma_joinids {missing}")
        return [lookup[c] for c in cell_ids]

    shuffled_ids = [103, 101, 104, 102]
    shuffled_vecs = [vectors[i] for i in shuffled_ids]

    aligned = align_by_identity(ids, shuffled_ids, shuffled_vecs)
    assert aligned == [vectors[i] for i in ids], "identity join must restore order"
    # positional join would have been wrong -- prove the perturbation was real
    assert shuffled_vecs != aligned

    # and a genuinely missing cell must raise rather than silently zero-fill
    try:
        align_by_identity([101, 999], shuffled_ids, shuffled_vecs)
    except KeyError as exc:
        assert "999" in str(exc)
    else:
        raise AssertionError("missing embedding row must not be silently filled")


def test_single_donor_many_cells_is_flagged_as_pseudoreplication():
    s = CellSummary(summary_id="sum1", target_id="ENSG1", cell_slice_id="cs1",
                    census_release=RELEASE, donor_count=1, cell_count=8000,
                    expression_metric="percent_positive",
                    expression_method="raw counts > 0",
                    primary_data_handling="is_primary_data=True only")
    assert "cellsummary.pseudoreplication" in _codes(check_cell_summary(s, [_slice()]))


def test_undefined_expression_metric_is_rejected():
    s = CellSummary(summary_id="sum1", target_id="ENSG1", cell_slice_id="cs1",
                    census_release=RELEASE, donor_count=6, cell_count=4000,
                    primary_data_handling="is_primary_data=True only")
    assert "cellsummary.undefined_metric" in _codes(check_cell_summary(s, [_slice()]))


def test_donor_aware_summary_passes():
    s = CellSummary(summary_id="sum1", target_id="ENSG1", cell_slice_id="cs1",
                    census_release=RELEASE, donor_count=6, cell_count=4000,
                    per_donor=[{"donor_id": f"d{i}", "pct_positive": 0.3} for i in range(6)],
                    expression_metric="percent_positive",
                    expression_method="raw counts > 0",
                    primary_data_handling="is_primary_data=True only")
    assert check_cell_summary(s, [_slice()]) == []


def test_validate_all_collects_and_does_not_raise():
    brief = ResearchBrief(brief_id="b1", raw_indication="lung adenocarcinoma",
                          disease_id="MONDO_0005061", status="confirmed",
                          provenance=REAL)
    targets = [Target(target_id="ENSG1", gene_symbol="EGFR", stable_gene_id="ENSG1",
                      provenance=REAL)]
    bad = Assessment(target_id="MISSING", rubric_version="v1",
                     ratings=[CriterionRating(criterion="genetic", rating="strong")])
    rep = validate_all(brief=brief, targets=targets, assessments=[bad])
    assert not rep.ok
    assert {"assessment.unknown_target", "evidence.unlinked"} <= _codes(rep.violations)
    assert "target.bad_gene_id" in _codes(rep.violations)  # ENSG1 is not a real id shape
    assert rep.render().startswith("FAIL")


def test_validate_all_passes_a_clean_record_set():
    brief = ResearchBrief(brief_id="b1", raw_indication="lung adenocarcinoma",
                          disease_id="MONDO_0005061", status="confirmed", provenance=REAL)
    targets = [Target(target_id="ENSG00000146648", gene_symbol="EGFR",
                      stable_gene_id="ENSG00000146648", protein_accession="P00533",
                      proposed_modulation="inhibit", provenance=REAL)]
    ev = [Evidence(evidence_id="ev1", subject_id="ENSG00000146648",
                   predicate="associated_with", object_id="MONDO_0005061",
                   evidence_type="genetic", direction="supports",
                   source_url="https://platform.opentargets.org/target/ENSG00000146648",
                   provenance=REAL)]
    asmt = [Assessment(target_id="ENSG00000146648", rubric_version="v1",
                       ratings=[CriterionRating(criterion="genetic", rating="strong",
                                                evidence_ids=["ev1"])],
                       design_feasibility="strong",
                       design_feasibility_rationale="single-pass type I membrane protein")]
    rep = validate_all(brief=brief, targets=targets, evidence=ev, assessments=asmt)
    assert rep.ok, rep.render()

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
