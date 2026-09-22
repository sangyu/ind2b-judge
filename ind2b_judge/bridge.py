"""Bridge: indication2binder run artifacts -> trust-layer records.

Reads a run directory written by the team pipeline (github.com/lnairGT/
Boston-CompBio-Hackathon, `ind2b` stages 0-5) and projects it into the typed
records in contracts/records.py, so the validators and the judge harness can
grade a real run rather than a fixture.

Deliberately read-only and out-of-tree: nothing here imports ind2b or modifies
it, so the two repositories stay independently mergeable. The coupling is the
on-disk artifact contract -- stage0_disease.json, stage1_evidence.json,
stage2_ranked_targets.csv, stage4_epitopes.csv, stage5_specs/manifest.json --
which is the same surface their own `read()` helpers use.

Mapping notes, where their vocabulary and the spec's differ:

  their `composite_score`        -> Assessment.ratings (banded, see _band)
  their `passes_accessibility`   -> Assessment.design_feasibility
  their `accessibility.reason`   -> design_feasibility_rationale / exclusion
  their `datatype_scores`        -> Evidence records, one per datatype
  their `selection_rule`         -> ResearchBrief.resolution_rationale

Their pipeline already separates biological support from binder reachability
(accessibility is a hard gate that zeroes the composite, not a weighted term),
which is exactly the section 9 separation the trust layer checks for. The
bridge preserves that split rather than flattening it back into one number.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pandas as pd

from .records import (
    AgentRun,
    Assessment,
    CriterionRating,
    DesignRequest,
    Evidence,
    Provenance,
    Rating,
    ResearchBrief,
    Target,
    ToolAction,
    new_id,
)

# Their stage filenames, from ind2b.config.STAGE_FILES. Duplicated rather than
# imported so this module never needs their package on the path.
STAGE_FILES = {
    0: "stage0_disease.json",
    1: "stage1_evidence.json",
    2: "stage2_ranked_targets.csv",
    3: "stage3_complexes.json",
    4: "stage4_epitopes.csv",
    5: "stage5_specs",
}

# Evidence-strength bands for a 0-1 component score. The thresholds are a
# judgement call and are recorded in the Assessment's rubric_version so a
# re-band is visible rather than silent. `unknown` is reserved for a component
# their pipeline could not fetch at all -- a genuine 0.0 ("fetched, found
# nothing") is `weak`, which is a different claim and must not be merged.
BANDS = ((0.60, "strong"), (0.30, "moderate"), (0.0, "weak"))

RUBRIC_VERSION = "ind2b-bridge-0.1"


def _band(value: float | None) -> Rating:
    if value is None:
        return "unknown"
    for threshold, label in BANDS:
        if value >= threshold:
            return label  # type: ignore[return-value]
    return "weak"


def _git_commit(repo: Path) -> str | None:
    """The commit the pipeline ran at, for judge row P0-11."""
    try:
        out = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or None
    except Exception:
        return None


def _prov(stage: dict[str, Any], source: str, origin: str = "real") -> Provenance:
    """Their stage headers already carry the fields a Provenance needs."""
    return Provenance(
        origin=origin,                      # type: ignore[arg-type]
        source=source,
        retrieved_at=stage.get("fetched_at") or stage.get("resolved_at") or "",
        release=f"ind2b/{stage.get('package_version')} "
                f"schema{stage.get('schema_version')}",
        query={"disease_id": stage.get("disease_id") or
               (stage.get("disease") or {}).get("id")},
    )


# --- stage 0 -> ResearchBrief ------------------------------------------------

def load_brief(run_dir: str | Path, *, moa: str | None = None,
               modality: str | None = None) -> ResearchBrief:
    run_dir = Path(run_dir)
    s0 = json.loads((run_dir / STAGE_FILES[0]).read_text())
    disease = s0.get("disease") or {}
    return ResearchBrief(
        brief_id=new_id("brief"),
        raw_indication=s0.get("query") or "",
        disease_id=disease.get("id"),
        disease_label=disease.get("name"),
        resolution_candidates=s0.get("candidates_considered") or [],
        resolution_rationale=(
            f"{s0.get('selection_rule')}; ambiguous={s0.get('ambiguous')}; "
            f"association_scope={s0.get('association_scope')}"),
        # Their stage 0 resolves the Open Targets id only. The Census mapping is
        # a separate system (spec s17) and stays None until someone maps it --
        # asserting it here would be the exact silent-identity bug the
        # validators exist to catch.
        census_disease_id=None,
        desired_effect=None,
        moa=moa,
        modality=modality,
        status="confirmed" if disease.get("id") else "pending",
        provenance=_prov(s0, "opentargets:graphql:v4 (via ind2b stage0)"),
    )


# --- stage 1 -> Targets + Evidence ------------------------------------------

# Their datatype_scores keys -> (spec evidence_type, human predicate)
# Verified against a live stage-1 run: the keys Open Targets actually returns
# are `clinical` (NOT `known_drug`), `genetic_association`, `somatic_mutation`,
# `literature`, `affected_pathway`, `animal_model`, `rna_expression`. Guessing
# these from the stage-2 component names produces citations to records that do
# not exist, which the validators flag -- correctly -- as dangling.
DATATYPE_MAP = {
    "genetic_association": ("genetic", "genetically_associated_with"),
    "somatic_mutation": ("genetic", "somatically_mutated_in"),
    "clinical": ("clinical", "clinical_evidence_for"),
    "known_drug": ("clinical", "drug_evidence_for"),
    "literature": ("literature", "reported_associated_with"),
    "affected_pathway": ("experimental", "pathway_implicated_in"),
    "animal_model": ("experimental", "model_evidence_for"),
    "rna_expression": ("expression", "expression_associated_with"),
}

# Stage-2 components that derive from a fetched LAYER rather than from a
# datatype score: known_drug from the known_drugs layer, clinical_precedent
# from trials/chembl, expression_specificity from the expression layer. These
# need their own Evidence records or the rating has nothing citable and is
# silently downgraded to unknown despite resting on real fetched data.
LAYER_EVIDENCE = {
    "known_drugs": ("clinical", "has_drug_precedent",
                    "https://platform.opentargets.org/target/{ens}"),
    "trials": ("clinical", "has_trial_precedent",
               "https://clinicaltrials.gov/search?intr={symbol}"),
    "chembl": ("clinical", "has_mechanism_precedent",
               "https://www.ebi.ac.uk/chembl/target_report_card/{ens}"),
    "expression": ("expression", "has_expression_specificity",
                   "https://platform.opentargets.org/target/{ens}"),
}


def load_targets_and_evidence(
    run_dir: str | Path,
) -> tuple[list[Target], list[Evidence], dict[str, Any]]:
    run_dir = Path(run_dir)
    s1 = json.loads((run_dir / STAGE_FILES[1]).read_text())
    prov = _prov(s1, "opentargets:graphql:v4 + uniprot (via ind2b stage1)")
    disease_id = s1.get("disease_id")

    targets: list[Target] = []
    evidence: list[Evidence] = []

    for t in s1.get("targets", []):
        ens = t.get("ensembl_id")
        accessions = t.get("uniprot") or []
        targets.append(Target(
            target_id=ens,
            gene_symbol=t.get("symbol") or "",
            stable_gene_id=ens,
            protein_accession=accessions[0] if accessions else None,
            # Their pipeline ranks by association and gates on reachability; it
            # does not infer whether inhibition or activation helps. Section 17
            # says that stays explicit rather than assumed.
            proposed_modulation="unknown",
            provenance=prov,
        ))

        for key, score in (t.get("datatype_scores") or {}).items():
            etype, predicate = DATATYPE_MAP.get(key, ("unknown", key))
            evidence.append(Evidence(
                evidence_id=f"ev_{ens}_{key}",
                subject_id=ens,
                predicate=predicate,
                object_id=disease_id,
                object_value=score,
                evidence_type=etype,
                direction="supports" if (score or 0) > 0 else "context",
                source_url=f"https://platform.opentargets.org/evidence/{ens}/{disease_id}",
                source_locator=f"associatedTargets.datatypeScores.{key}",
                measurement={"datatype_score": score},
                # Their stage 0 records association_scope (direct vs including
                # descendants); propagated evidence must carry the disease it
                # actually refers to, per s17.
                disease_scope=disease_id,
                is_propagated=bool(s1.get("association_scope") == "indirect"),
                limitations=("Open Targets datatype score: weight of recorded "
                             "evidence, not causality or direction of effect"),
                provenance=prov,
            ))

        # Layer-derived evidence. Only emitted when the layer was actually
        # fetched for this target -- stage 1 skips the per-target layers for
        # intracellular targets, and a record asserting "no drugs" for a target
        # nobody looked up would be a fabricated negative.
        for layer, (etype, predicate, url_tpl) in LAYER_EVIDENCE.items():
            payload = t.get(layer)
            if not payload:
                continue
            evidence.append(Evidence(
                evidence_id=f"ev_{ens}_{layer}",
                subject_id=ens,
                predicate=predicate,
                object_id=disease_id if layer != "expression" else None,
                object_value=(payload.get("count") if layer == "known_drugs"
                              else payload.get("total_trials") if layer == "trials"
                              else payload.get("n_distinct_mechanisms")
                              if layer == "chembl" else
                              (payload.get("max_specificity") or {}).get(
                                  "specificity_score")),
                evidence_type=etype,
                direction="context",
                source_url=url_tpl.format(ens=ens, symbol=t.get("symbol") or ""),
                source_locator=f"ind2b stage1 layer: {layer}",
                measurement=payload,
                limitations=("a trial or drug entry is precedent for engaging the "
                             "target, not evidence of efficacy in this indication"
                             if etype == "clinical" else
                             "RNA baseline expression; not surface protein abundance"),
                provenance=prov,
            ))

        acc = t.get("accessibility") or {}
        spans = acc.get("extracellular_spans") or []
        evidence.append(Evidence(
            evidence_id=f"ev_{ens}_accessibility",
            subject_id=ens,
            predicate="is_surface_accessible",
            object_value=bool(t.get("surface_accessible")),
            evidence_type="structural",
            direction="context",
            source_url=(f"https://rest.uniprot.org/uniprotkb/{accessions[0]}"
                        if accessions else None),
            source_locator=acc.get("source") or "UniProt topology",
            measurement={
                "mode": acc.get("mode"),
                "extracellular_spans": spans,
                "ectodomain_residues": sum(e - s + 1 for s, e in spans),
                "transmembrane": acc.get("transmembrane"),
                "location_keyword_hint": t.get("location_keyword_hint"),
            },
            limitations=(acc.get("reason") or
                         "topology-derived; a location string alone cannot "
                         "distinguish which side of the membrane a protein faces"),
            provenance=prov,
        ))

    return targets, evidence, s1


# --- stage 2 -> Assessments -------------------------------------------------

# column -> (criterion name, the stage-1 datatype keys that back it).
# Explicit rather than derived: their component names and the datatype keys do
# not share a prefix pattern (raw_known_drug <- known_drug, but
# raw_genetic_evidence <- genetic_association AND somatic_mutation), and a
# heuristic here silently invents citations for datatypes a given target does
# not have. Components with no datatype backing cite the accessibility record
# or nothing at all.
COMPONENT_CRITERIA: dict[str, tuple[str, tuple[str, ...]]] = {
    # ot_overall aggregates every datatype, so it cites all of them rather than
    # claiming one source.
    "raw_ot_overall": ("overall_association",
                       ("genetic_association", "somatic_mutation", "clinical",
                        "literature", "affected_pathway", "animal_model",
                        "rna_expression")),
    "raw_genetic_evidence": ("genetic_support",
                             ("genetic_association", "somatic_mutation")),
    "raw_known_drug": ("drug_precedent", ("clinical", "known_drugs")),
    "raw_literature": ("literature_support", ("literature",)),
    "raw_expression_specificity": ("expression_specificity",
                                   ("rna_expression", "expression")),
    "raw_pathway_and_model": ("pathway_and_model",
                              ("affected_pathway", "animal_model")),
    "raw_clinical_precedent": ("clinical_precedent",
                               ("clinical", "trials", "chembl")),
    "raw_antibody_tractability": ("biologic_tractability", ("accessibility",)),
}


def load_assessments(
    run_dir: str | Path, known_evidence_ids: set[str] | None = None
) -> list[Assessment]:
    """Project the ranked table into Assessments, keeping the gate separate.

    Their accessibility gate zeroes the composite rather than penalising it, so
    a failing target is not "low scoring" -- it is out of scope for this
    modality with a recorded reason. That maps onto design_feasibility plus an
    exclusion reason, never onto a weaker biology rating.

    `known_evidence_ids` is the set of Evidence records that actually exist for
    this run. A citation is emitted only if the record is in it -- a favorable
    rating whose evidence was never fetched is reported `unknown` with stated
    missingness, which is the honest form and what the validator requires.
    """
    df = pd.read_csv(Path(run_dir) / STAGE_FILES[2])
    known = known_evidence_ids if known_evidence_ids is not None else set()
    out: list[Assessment] = []

    for _, row in df.iterrows():
        ens = row["ensembl_id"]
        ratings: list[CriterionRating] = []
        for col, (criterion, datatypes) in COMPONENT_CRITERIA.items():
            val = row.get(col)
            val = None if pd.isna(val) else float(val)
            candidate_ids = [f"ev_{ens}_{k}" for k in datatypes]
            eids = [e for e in candidate_ids if e in known]
            band = _band(val)
            # A favorable band with nothing citable is not a favorable finding
            # -- it is an unverified one. Downgrade rather than invent a source.
            rating = band if (eids or band in ("weak", "unknown")) else "unknown"
            missing = None
            if val is None:
                missing = "component not fetched"
            elif not eids and band in ("strong", "moderate"):
                missing = (f"score {val:.3f} is derived from "
                           f"{', '.join(datatypes) or 'aggregate columns'}; no "
                           "citable per-datatype evidence record for this target")
            ratings.append(CriterionRating(
                criterion=criterion,
                rating=rating,
                evidence_ids=eids,
                missingness=missing,
                rationale=f"{col.replace('raw_', '')}={val!r} "
                          f"(band {band}, {RUBRIC_VERSION})",
            ))

        passes = bool(row.get("passes_accessibility"))
        reason = row.get("accessibility_reason")
        reason = None if pd.isna(reason) else str(reason)
        mode = row.get("accessibility_mode")
        mode = None if pd.isna(mode) else str(mode)
        spans = row.get("extracellular_spans")
        spans = "" if pd.isna(spans) else str(spans)

        liabilities: list[str] = []
        pen = row.get("penalty_reasons")
        if isinstance(pen, str) and pen.strip():
            liabilities = [p.strip() for p in pen.split(";") if p.strip()]

        out.append(Assessment(
            target_id=ens,
            rubric_version=RUBRIC_VERSION,
            ratings=ratings,
            disease_rationale=(f"composite {row.get('composite_score')}, "
                               f"OT rank {row.get('ot_rank')}"),
            moa_rationale=None,      # their pipeline does not assert direction
            liabilities=liabilities,
            design_feasibility="strong" if passes else "weak",
            design_feasibility_rationale=(
                f"{mode}: {row.get('ectodomain_residues')} extracellular residues "
                f"({spans})" if passes else
                (reason or "no extracellular topology of sufficient span")),
            excluded=not passes,
            exclusion_reason=(None if passes else
                              (reason or "fails surface-accessibility gate; a de novo "
                                         "mini-binder cannot reach it without a "
                                         "delivery strategy")),
            review_status="unreviewed",
        ))
    return out


# --- stage 5 -> DesignRequest -----------------------------------------------

def load_design_requests(run_dir: str | Path, brief_id: str) -> list[DesignRequest]:
    """Stage 5 emits BindCraft2 specs and stops. Each becomes an UNREVIEWED
    request: their pipeline correctly declines to launch, and the trust layer's
    gate is what records that a human approved it."""
    manifest = Path(run_dir) / STAGE_FILES[5] / "manifest.json"
    if not manifest.exists():
        return []
    data = json.loads(manifest.read_text())
    out = []
    for spec in data.get("specs", []):
        out.append(DesignRequest(
            design_id=new_id("design"),
            brief_id=brief_id,
            selected_target_id=spec.get("ensembl_id") or spec.get("symbol"),
            structure_id=spec.get("entry_id"),
            protein_sequence_ref=spec.get("structure_path"),
            chain_residue_mapping={
                "chain": spec.get("target_chain"),
                "author_numbering": spec.get("hotspots_author"),
                "uniprot_numbering": spec.get("hotspots_uniprot"),
            },
            binding_region={"hotspots": spec.get("hotspots_author"),
                            "chain": spec.get("target_chain")},
            moa_rationale=None,           # must be supplied at review time
            evidence_ids=[f"ev_{spec.get('ensembl_id')}_accessibility"],
            model="bindcraft2",
            model_config={"binder_lengths": spec.get("binder_lengths"),
                          "n_designs": spec.get("number_of_final_designs")},
            review_status="unreviewed",
        ))
    return out


# --- whole run ---------------------------------------------------------------

def load_multi(
    run_dirs: list[str | Path], *, repo: str | Path | None = None,
    rankings: list[list[str]] | None = None,
) -> dict[str, Any]:
    """Project SEVERAL ind2b runs into one record set for the judge.

    Judge rows P0-4 (determinism) and P0-11 (generality) are cross-run
    properties: one asks whether identical inputs give identical order, the
    other whether different indications give different pools at the same
    commit. Neither is decidable from a single run directory, so the harness
    correctly reports UNVERIFIABLE until it is given both.

    `rankings` carries the ordered symbol lists from repeated scoring passes
    over the SAME evidence -- that is the determinism evidence, and it has to
    be collected by re-running stage 2, not inferred from stored output.
    """
    runs = [load_run(d, repo=repo) for d in run_dirs]
    merged: dict[str, Any] = {
        "brief": runs[0]["brief"],
        "targets": [t for r in runs for t in r["targets"]],
        "evidence": [e for r in runs for e in r["evidence"]],
        "assessments": [a for r in runs for a in r["assessments"]],
        "agent_runs": [a for r in runs for a in r["agent_runs"]],
        "design_request": next((r["design_request"] for r in runs
                                if r["design_request"]), None),
        "design_runs": [], "candidates": [],
        "cell_slices": [], "cell_summaries": [], "embedding_slices": [],
    }
    if rankings:
        merged["rankings"] = rankings
    return merged


def load_run(run_dir: str | Path, *, repo: str | Path | None = None,
             moa: str | None = None) -> dict[str, Any]:
    """Project a complete ind2b run into the record set the judge grades."""
    run_dir = Path(run_dir)
    brief = load_brief(run_dir, moa=moa)
    targets, evidence, s1 = load_targets_and_evidence(run_dir)
    # Pass the real evidence ids so assessments can only cite records that exist.
    assessments = load_assessments(run_dir, {e.evidence_id for e in evidence})
    designs = load_design_requests(run_dir, brief.brief_id)

    # Reconstruct a tool_action_log from the stage headers. Their stages record
    # what they fetched and when, which is what judge row J-4 needs; a real
    # agentic run would emit these live instead.
    actions = [
        ToolAction(tool_name="resolve_indication", tool_version="ind2b.stage0",
                   arguments={"indication": brief.raw_indication},
                   result_refs=[brief.brief_id],
                   reason="resolve free text to an ontology id"),
        ToolAction(tool_name="discover_targets", tool_version="ind2b.stage1",
                   arguments={"disease_id": brief.disease_id,
                              "pool_size": s1.get("pool_size"),
                              "association_total": s1.get("association_total")},
                   result_refs=[e.evidence_id for e in evidence],
                   reason=f"bounded pool of {s1.get('pool_size')} of "
                          f"{s1.get('association_total')} associated targets"),
        ToolAction(tool_name="compare_targets", tool_version="ind2b.stage2",
                   arguments={"rubric_version": RUBRIC_VERSION},
                   result_refs=[a.target_id for a in assessments],
                   reason="deterministic weighted scoring with accessibility gate"),
    ]

    agent_run = AgentRun(
        run_id=new_id("agentrun"),
        brief_id=brief.brief_id,
        current_stage="compare_targets",
        status="completed",
        plan_summary=f"ind2b stages 0-2 over {brief.disease_label}",
        tool_action_log=actions,
        evidence_ids=[e.evidence_id for e in evidence],
        excluded_targets=[{"target_id": a.target_id,
                           "reason": a.exclusion_reason or ""}
                          for a in assessments if a.excluded],
        candidate_pool_scope={
            "target_ids": [t.target_id for t in targets],
            "pool_size": s1.get("pool_size"),
            "association_total": s1.get("association_total"),
            "retrieval": "associatedTargets paged by ot_overall_score",
        },
        # Stage 5 writes specs and stops, so an ind2b run that has not been
        # through the design gate is honestly an evidence report.
        final_outcome=("evidence_report_only" if not designs
                       else "needs_clarification"),
        git_commit=_git_commit(Path(repo)) if repo else None,
    )

    return {
        "brief": brief,
        "targets": targets,
        "evidence": evidence,
        "assessments": assessments,
        "agent_runs": [agent_run],
        "design_request": designs[0] if designs else None,
        "design_requests": designs,
        "design_runs": [],
        "candidates": [],
        "cell_slices": [],
        "cell_summaries": [],
        "embedding_slices": [],
    }
