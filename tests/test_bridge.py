"""Integration tests: ind2b run artifacts -> trust-layer records.

Grades the BRIDGE, on a synthetic run directory that mimics the real artifact
shapes (verified against a live `ind2b stages 0-2` run on MONDO_0005061). No
network: the fixtures are written to a tmp dir, so this stays runnable during
a demo and when the team repo is absent.

The property that matters most here is that the bridge cannot manufacture
citations. An earlier version derived evidence ids by prefix-matching datatype
names and produced 72 dangling references on a real run -- the validators
caught it, which is the whole point, but the test below is what keeps it caught.

    python tests/test_bridge.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ind2b_judge.bridge import (  # noqa: E402
    RUBRIC_VERSION,
    _band,
    load_assessments,
    load_brief,
    load_run,
    load_targets_and_evidence,
)
from ind2b_judge.validators import validate_all  # noqa: E402
from ind2b_judge.harness import grade  # noqa: E402

EGFR = "ENSG00000146648"
KRAS = "ENSG00000133703"


def _write_run(tmp: Path, *, include_datatypes=True) -> Path:
    """A minimal ind2b run directory in the real artifact shapes."""
    run = tmp / "runs" / "MONDO_0005061"
    run.mkdir(parents=True)

    (run / "stage0_disease.json").write_text(json.dumps({
        "schema_version": 1, "package_version": "0.1.0",
        "resolved_at": "2026-09-22T17:19:08+00:00",
        "source": "Open Targets Platform GraphQL (EFO/MONDO ontology)",
        "query": "lung adenocarcinoma", "selection_rule": "exact_name_match",
        "ambiguous": False,
        "disease": {"id": "MONDO_0005061", "name": "lung adenocarcinoma"},
        "association_scope": "direct", "candidates_considered": [],
    }))

    # the real Open Targets keys, as returned by a live stage-1 run
    datatypes = ({"genetic_association": 0.72, "somatic_mutation": 0.81,
                  "clinical": 0.66, "literature": 0.65,
                  "affected_pathway": 0.40, "animal_model": 0.30,
                  "rna_expression": 0.12} if include_datatypes else {})
    (run / "stage1_evidence.json").write_text(json.dumps({
        "schema_version": 1, "package_version": "0.1.0",
        "fetched_at": "2026-09-22T17:19:54+00:00",
        "disease_id": "MONDO_0005061", "disease_name": "lung adenocarcinoma",
        "association_total": 8760, "pool_size": 2, "n_surface_accessible": 1,
        "all_layers": False, "sources": ["Open Targets", "UniProt"],
        "targets": [
            {"ensembl_id": EGFR, "symbol": "EGFR", "name": "epidermal growth factor receptor",
             "uniprot": ["P00533"], "ot_overall_score": 0.82,
             "datatype_scores": datatypes, "surface_accessible": True,
             "rank_by_ost_score_placeholder": None, "rank_by_ot_score": 1,
             "accessibility": {"mode": "membrane_ectodomain",
                               "extracellular_spans": [[25, 645]],
                               "transmembrane": [[646, 668]], "reason": None,
                               "source": "UniProt topological domain"}},
            {"ensembl_id": KRAS, "symbol": "KRAS", "name": "KRAS proto-oncogene",
             "uniprot": ["P01116"], "ot_overall_score": 0.74,
             "datatype_scores": datatypes, "surface_accessible": False,
             "rank_by_ot_score": 2,
             "accessibility": {"mode": "none", "extracellular_spans": [],
                               "reason": "lipid-anchored, cytoplasmic face",
                               "source": "UniProt topological domain"}},
        ],
    }))

    rows = [
        {"rank": 1, "symbol": "EGFR", "ensembl_id": EGFR, "uniprot": "P00533",
         "name": "EGFR", "composite_score": 0.785, "passes_accessibility": True,
         "accessibility_mode": "membrane_ectodomain", "accessibility_reason": "",
         "extracellular_spans": "25-645", "ectodomain_residues": 621,
         "penalty_multiplier": 0.92, "penalty_reasons": "3 safety liabilities recorded",
         "target_class": "Kinase", "ot_rank": 1,
         "raw_ot_overall": 0.82, "raw_genetic_evidence": 0.72,
         "raw_known_drug": 0.91, "raw_literature": 0.65,
         "raw_expression_specificity": 0.11, "raw_pathway_and_model": 0.40,
         "raw_clinical_precedent": 0.88, "raw_antibody_tractability": 1.0},
        {"rank": "", "symbol": "KRAS", "ensembl_id": KRAS, "uniprot": "P01116",
         "name": "KRAS", "composite_score": 0.0, "passes_accessibility": False,
         "accessibility_mode": "none",
         "accessibility_reason": "lipid-anchored, cytoplasmic face",
         "extracellular_spans": "", "ectodomain_residues": 0,
         "penalty_multiplier": 1.0, "penalty_reasons": "",
         "target_class": "Other", "ot_rank": 2,
         "raw_ot_overall": 0.74, "raw_genetic_evidence": 0.72,
         "raw_known_drug": 0.91, "raw_literature": 0.65,
         "raw_expression_specificity": 0.05, "raw_pathway_and_model": 0.40,
         "raw_clinical_precedent": 0.88, "raw_antibody_tractability": 0.0},
    ]
    import csv
    with open(run / "stage2_ranked_targets.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return run


# --- banding ----------------------------------------------------------------

def test_missing_component_is_unknown_not_weak():
    """'not fetched' and 'fetched, found nothing' are different claims."""
    assert _band(None) == "unknown"
    assert _band(0.0) == "weak"


def test_bands_are_monotonic():
    assert [_band(v) for v in (0.9, 0.45, 0.1)] == ["strong", "moderate", "weak"]


# --- stage 0 ----------------------------------------------------------------

def test_brief_preserves_the_users_own_words():
    with tempfile.TemporaryDirectory() as td:
        run = _write_run(Path(td))
        b = load_brief(run)
        assert b.raw_indication == "lung adenocarcinoma"
        assert b.disease_id == "MONDO_0005061"
        assert "exact_name_match" in b.resolution_rationale


def test_census_mapping_is_not_invented_from_the_open_targets_id():
    """s17: never assume the two ontology systems use identical strings."""
    with tempfile.TemporaryDirectory() as td:
        b = load_brief(_write_run(Path(td)))
        assert b.census_disease_id is None
        assert b.census_mapping_provenance is None


# --- stage 1 ----------------------------------------------------------------

def test_targets_carry_accession_and_unknown_modulation():
    with tempfile.TemporaryDirectory() as td:
        targets, _, _ = load_targets_and_evidence(_write_run(Path(td)))
        egfr = next(t for t in targets if t.gene_symbol == "EGFR")
        assert egfr.protein_accession == "P00533"
        # s17: association does not establish direction of benefit.
        assert egfr.proposed_modulation == "unknown"


def test_intracellular_target_is_recorded_not_dropped():
    """Their pipeline keeps failing targets as recorded decisions."""
    with tempfile.TemporaryDirectory() as td:
        targets, evidence, _ = load_targets_and_evidence(_write_run(Path(td)))
        assert any(t.gene_symbol == "KRAS" for t in targets)
        acc = next(e for e in evidence if e.evidence_id == f"ev_{KRAS}_accessibility")
        assert acc.object_value is False
        assert "cytoplasmic" in acc.limitations


def test_every_evidence_record_has_provenance_and_a_source():
    with tempfile.TemporaryDirectory() as td:
        _, evidence, _ = load_targets_and_evidence(_write_run(Path(td)))
        assert evidence
        for e in evidence:
            assert e.provenance is not None
            assert e.source_url or e.source_locator


# --- the citation bug this test file exists for -----------------------------

def test_assessments_never_cite_evidence_that_does_not_exist():
    with tempfile.TemporaryDirectory() as td:
        run = _write_run(Path(td))
        _, evidence, _ = load_targets_and_evidence(run)
        known = {e.evidence_id for e in evidence}
        for a in load_assessments(run, known):
            for r in a.ratings:
                for eid in r.evidence_ids:
                    assert eid in known, f"dangling citation {eid}"


def test_every_criterion_maps_to_a_real_evidence_key():
    """Guard against guessing datatype keys from component names.

    The keys Open Targets returns are `clinical`, `genetic_association`,
    `somatic_mutation`, `literature`, `affected_pathway`, `animal_model`,
    `rna_expression` -- plus the stage-1 layer names. A component mapped to a
    key that appears in neither produces citations to records that never exist,
    and the rating is then silently downgraded to `unknown` despite resting on
    a real fetched score.
    """
    from ind2b_judge.bridge import (
        COMPONENT_CRITERIA, DATATYPE_MAP, LAYER_EVIDENCE)
    valid = set(DATATYPE_MAP) | set(LAYER_EVIDENCE) | {"accessibility"}
    for col, (criterion, datatypes) in COMPONENT_CRITERIA.items():
        assert datatypes, f"{criterion} cites nothing; it cannot be graded"
        unknown = set(datatypes) - valid
        assert not unknown, f"{criterion} cites non-existent key(s) {unknown}"


def test_scored_component_is_not_silently_downgraded():
    """A real non-zero score must produce a real band, not `unknown`.

    This is the finding class the trust layer exists to prevent: information
    loss that looks like honest missingness.
    """
    with tempfile.TemporaryDirectory() as td:
        run = _write_run(Path(td))
        _, evidence, _ = load_targets_and_evidence(run)
        a = next(x for x in load_assessments(run, {e.evidence_id for e in evidence})
                 if x.target_id == EGFR)
        by_crit = {r.criterion: r for r in a.ratings}
        # the fixture scores pathway_and_model at 0.40 and genetic at 0.72
        assert by_crit["pathway_and_model"].rating == "moderate"
        assert by_crit["genetic_support"].rating == "strong"
        for crit in ("pathway_and_model", "genetic_support"):
            assert by_crit[crit].rating != "unknown", \
                f"{crit} has a real score but reports unknown"


def test_genetic_support_cites_somatic_evidence():
    """For oncology, somatic evidence carries most of the weight (their stage-1
    docstring). Dropping its citation loses the dominant source."""
    with tempfile.TemporaryDirectory() as td:
        run = _write_run(Path(td))
        _, evidence, _ = load_targets_and_evidence(run)
        a = next(x for x in load_assessments(run, {e.evidence_id for e in evidence})
                 if x.target_id == EGFR)
        cites = next(r for r in a.ratings if r.criterion == "genetic_support").evidence_ids
        assert f"ev_{EGFR}_somatic_mutation" in cites
        assert f"ev_{EGFR}_genetic_association" in cites


def test_unbacked_favorable_score_is_downgraded_with_stated_missingness():
    """A strong number with no citable record is unverified, not strong."""
    with tempfile.TemporaryDirectory() as td:
        run = _write_run(Path(td), include_datatypes=False)
        _, evidence, _ = load_targets_and_evidence(run)
        a = load_assessments(run, {e.evidence_id for e in evidence})[0]
        genetic = next(r for r in a.ratings if r.criterion == "genetic_support")
        assert genetic.rating == "unknown"
        assert genetic.evidence_ids == []
        assert genetic.missingness


def test_bridged_records_pass_the_validators():
    with tempfile.TemporaryDirectory() as td:
        run = load_run(_write_run(Path(td)))
        rep = validate_all(brief=run["brief"], targets=run["targets"],
                           evidence=run["evidence"], assessments=run["assessments"])
        assert rep.ok, rep.render()


# --- the accessibility gate maps to feasibility, not to weak biology -------

def test_accessibility_gate_becomes_design_feasibility():
    with tempfile.TemporaryDirectory() as td:
        run = _write_run(Path(td))
        _, evidence, _ = load_targets_and_evidence(run)
        asmts = {a.target_id: a
                 for a in load_assessments(run, {e.evidence_id for e in evidence})}
        assert asmts[EGFR].design_feasibility == "strong"
        assert asmts[KRAS].design_feasibility == "weak"
        assert asmts[KRAS].excluded is True
        assert "cytoplasmic" in asmts[KRAS].exclusion_reason


def test_excluded_target_keeps_its_strong_biology():
    """s17: show strong biology AND the modality mismatch -- do not blend them."""
    with tempfile.TemporaryDirectory() as td:
        run = _write_run(Path(td))
        _, evidence, _ = load_targets_and_evidence(run)
        kras = next(a for a in load_assessments(run, {e.evidence_id for e in evidence})
                    if a.target_id == KRAS)
        assert any(r.rating in ("strong", "moderate") for r in kras.ratings)
        assert kras.excluded and kras.exclusion_reason


def test_penalty_reasons_become_liabilities():
    with tempfile.TemporaryDirectory() as td:
        run = _write_run(Path(td))
        _, evidence, _ = load_targets_and_evidence(run)
        egfr = next(a for a in load_assessments(run, {e.evidence_id for e in evidence})
                    if a.target_id == EGFR)
        assert any("safety" in l for l in egfr.liabilities)


# --- whole-run projection ---------------------------------------------------

def test_stage5_absence_yields_no_design_request():
    """Their stage 5 writes specs and stops; no spec means no launchable request."""
    with tempfile.TemporaryDirectory() as td:
        run = load_run(_write_run(Path(td)))
        assert run["design_request"] is None
        assert run["agent_runs"][0].final_outcome == "evidence_report_only"


def test_excluded_targets_are_recorded_on_the_agent_run():
    with tempfile.TemporaryDirectory() as td:
        run = load_run(_write_run(Path(td)))
        ex = run["agent_runs"][0].excluded_targets
        assert any(e["target_id"] == KRAS and e["reason"] for e in ex)


def test_pool_scope_records_retrieval_bounds():
    """s17: record the retrieval order, pagination and limits; no exhaustiveness claim."""
    with tempfile.TemporaryDirectory() as td:
        scope = load_run(_write_run(Path(td)))["agent_runs"][0].candidate_pool_scope
        assert scope["pool_size"] == 2
        assert scope["association_total"] == 8760
        assert "retrieval" in scope


def test_judge_grades_a_bridged_run_without_failing():
    with tempfile.TemporaryDirectory() as td:
        result = grade(load_run(_write_run(Path(td))))
        fails = [v for v in result["machine_verdicts"] if v["verdict"] == "FAIL"]
        assert not fails, fails


def test_single_run_cannot_prove_determinism_or_generality():
    """Cross-run rows must stay UNVERIFIABLE on one run, never PASS."""
    with tempfile.TemporaryDirectory() as td:
        v = {x["id"]: x["verdict"] for x in
             grade(load_run(_write_run(Path(td))))["machine_verdicts"]}
        assert v["P0-4"] == "UNVERIFIABLE"
        assert v["P0-11"] == "UNVERIFIABLE"


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
