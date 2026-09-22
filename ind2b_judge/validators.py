"""Deterministic contract checks (spec sections 13 and 18).

Every check here answers yes/no from the records alone -- no model call, no
network. That is deliberate: section 18 requires ranking behaviour to be
deterministic, so the thing that audits it must be too. A language model in this
layer would make the audit slower, unreproducible, and unable to fail a build.

Each Violation names the spec rule it enforces, so a failure tells you which
scientific guarantee broke rather than which assertion tripped.

Usage:
    report = validate_all(brief=b, targets=ts, evidence=es, assessments=asmts)
    if not report.ok:
        print(report.render())
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from .records import (
    TERMINAL_RUN_STATES,
    Assessment,
    Candidate,
    CellSlice,
    CellSummary,
    DesignRequest,
    DesignRun,
    EmbeddingSlice,
    Evidence,
    ResearchBrief,
    Target,
)

# Ratings that claim positive support. A criterion resting on zero evidence IDs
# may not hold one of these -- that is the section 13 rule "missing evidence
# cannot silently become a favorable score".
FAVORABLE_RATINGS = frozenset({"strong", "moderate"})

SEVERITY_ORDER = {"error": 0, "warning": 1}

# Identifier shapes. Checking the full format rather than a prefix: section 9
# requires explicit ID mapping, and a placeholder like "ENSG1" that passes a
# prefix test fails much later, at the Census join, where it is far more
# expensive to diagnose.
ENSEMBL_GENE_RE = re.compile(r"ENSG\d{11}")
UNIPROT_RE = re.compile(r"[OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9]([A-Z][A-Z0-9]{2}[0-9]){1,2}")


@dataclass
class Violation:
    check: str          # short stable id, e.g. "evidence.unlinked"
    rule: str           # the spec guarantee in one line
    detail: str         # what actually went wrong, with ids
    severity: str = "error"
    record_id: str | None = None


@dataclass
class Report:
    violations: list[Violation] = field(default_factory=list)
    checks_run: list[str] = field(default_factory=list)

    @property
    def errors(self) -> list[Violation]:
        return [v for v in self.violations if v.severity == "error"]

    @property
    def warnings(self) -> list[Violation]:
        return [v for v in self.violations if v.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def add(self, *violations: Violation) -> None:
        self.violations.extend(violations)

    def render(self) -> str:
        if not self.violations:
            return f"PASS  ({len(self.checks_run)} checks, no violations)"
        lines = [
            f"{'PASS' if self.ok else 'FAIL'}  "
            f"{len(self.errors)} error(s), {len(self.warnings)} warning(s) "
            f"across {len(self.checks_run)} checks"
        ]
        for v in sorted(self.violations, key=lambda x: SEVERITY_ORDER[x.severity]):
            tag = "ERROR" if v.severity == "error" else "warn "
            where = f" [{v.record_id}]" if v.record_id else ""
            lines.append(f"  {tag} {v.check}{where}: {v.detail}")
            lines.append(f"        rule: {v.rule}")
        return "\n".join(lines)


# --- section 13: contract validation ----------------------------------------

def check_evidence_links(
    assessments: Sequence[Assessment], evidence: Sequence[Evidence]
) -> list[Violation]:
    """Every favorable rating must cite evidence that actually exists.

    Two failure modes, both of which would let an unsupported target win:
    a rating with no evidence_ids at all, and a rating citing an id that is not
    in the evidence set (a typo or a dropped record).
    """
    known = {e.evidence_id for e in evidence}
    out: list[Violation] = []
    for a in assessments:
        for r in a.ratings:
            if r.rating in FAVORABLE_RATINGS and not r.evidence_ids:
                out.append(Violation(
                    check="evidence.unlinked",
                    rule="s13: missing evidence cannot silently become a favorable score",
                    detail=f"criterion {r.criterion!r} rated {r.rating!r} with no evidence_ids",
                    record_id=a.target_id,
                ))
            for eid in r.evidence_ids:
                if eid not in known:
                    out.append(Violation(
                        check="evidence.dangling",
                        rule="s8: validate stable IDs at module boundaries",
                        detail=f"criterion {r.criterion!r} cites unknown evidence_id {eid!r}",
                        record_id=a.target_id,
                    ))
    return out


def check_evidence_provenance(evidence: Sequence[Evidence]) -> list[Violation]:
    """Section 8/9: a scientific relation without a source is not evidence."""
    out: list[Violation] = []
    for e in evidence:
        if e.provenance is None:
            out.append(Violation(
                check="evidence.no_provenance",
                rule="s8: every scientific relation references evidence with provenance",
                detail="provenance is None",
                record_id=e.evidence_id,
            ))
            continue
        if not (e.source_url or e.source_locator):
            out.append(Violation(
                check="evidence.no_source",
                rule="s8: evidence carries source_url/DOI or source_locator",
                detail=f"neither source_url nor source_locator set (origin={e.provenance.origin})",
                record_id=e.evidence_id,
            ))
        if e.is_propagated and not e.disease_scope:
            out.append(Violation(
                check="evidence.propagated_scope_missing",
                rule="s17: preserve the disease each underlying item actually refers to",
                detail="is_propagated=True but disease_scope is unset",
                record_id=e.evidence_id,
            ))
    return out


def check_target_association(
    assessments: Sequence[Assessment],
    targets: Sequence[Target],
    evidence: Sequence[Evidence],
) -> list[Violation]:
    """Assessments and evidence must point at targets that exist in this run."""
    known = {t.target_id for t in targets}
    out: list[Violation] = []
    for a in assessments:
        if a.target_id not in known:
            out.append(Violation(
                check="assessment.unknown_target",
                rule="s13: wrong target/candidate association is rejected",
                detail=f"assessment references target {a.target_id!r} absent from the target set",
                record_id=a.target_id,
            ))
    for e in evidence:
        # Subjects may legitimately be non-targets (a paper, a population), so
        # only flag ids that look like targets but are unknown.
        if e.subject_id.startswith("ENSG") and e.subject_id not in known:
            out.append(Violation(
                check="evidence.unknown_subject",
                rule="s8: use stable IDs across modules; validate at boundaries",
                detail=f"evidence subject {e.subject_id!r} is not in the target set",
                record_id=e.evidence_id,
            ))
    return out


def check_identity(brief: ResearchBrief, targets: Sequence[Target]) -> list[Violation]:
    """Section 13/17: gene/protein identity verified; ontology systems kept apart."""
    out: list[Violation] = []
    if brief.status == "confirmed" and not brief.disease_id:
        out.append(Violation(
            check="brief.unresolved",
            rule="s8: a confirmed brief carries a resolved Open Targets disease ID",
            detail="status='confirmed' but disease_id is None",
            record_id=brief.brief_id,
        ))
    # Section 17: never assume the two systems use identical strings.
    if brief.census_disease_id and not brief.census_mapping_provenance:
        out.append(Violation(
            check="brief.census_mapping_unprovenanced",
            rule="s17: resolve CELLxGENE disease IDs through documented mappings",
            detail="census_disease_id set without census_mapping_provenance",
            record_id=brief.brief_id,
        ))
    for t in targets:
        # Full shape, not just the prefix: a truncated or placeholder id like
        # "ENSG1" would otherwise sail through and only fail at the Census join.
        if not ENSEMBL_GENE_RE.fullmatch(t.stable_gene_id):
            out.append(Violation(
                check="target.bad_gene_id",
                rule="s9: map Ensembl gene IDs explicitly; do not join on symbols",
                detail=f"stable_gene_id {t.stable_gene_id!r} is not a well-formed "
                       "Ensembl human gene id (ENSG + 11 digits)",
                record_id=t.target_id,
            ))
        if t.protein_accession and not UNIPROT_RE.fullmatch(t.protein_accession):
            out.append(Violation(
                check="target.bad_accession",
                rule="s9: verify species and isoform/construct differences",
                detail=f"protein_accession {t.protein_accession!r} is not a UniProt accession",
                record_id=t.target_id,
            ))
    return out


def check_design_target_identity(
    request: DesignRequest, targets: Sequence[Target]
) -> list[Violation]:
    """Section 9: a design request needs a protein accession, not just a symbol."""
    out: list[Violation] = []
    by_id = {t.target_id: t for t in targets}
    t = by_id.get(request.selected_target_id)
    if t is None:
        out.append(Violation(
            check="design.unknown_target",
            rule="s13: wrong target/candidate association is rejected",
            detail=f"design references unknown target {request.selected_target_id!r}",
            record_id=request.design_id,
        ))
        return out
    if not t.protein_accession:
        out.append(Violation(
            check="design.no_accession",
            rule="s9: map Ensembl gene IDs to the protein accession used for design",
            detail=f"target {t.gene_symbol} has no protein_accession; cannot verify construct",
            record_id=request.design_id,
        ))
    if not request.binding_region:
        out.append(Violation(
            check="design.no_binding_region",
            rule="s10: record the chosen binding region and why engagement produces the MoA",
            detail="binding_region is empty",
            record_id=request.design_id,
        ))
    if not request.moa_rationale:
        out.append(Violation(
            check="design.no_moa_rationale",
            rule="s10: binding-region choice is tied to the intended mechanism",
            detail="moa_rationale is unset",
            record_id=request.design_id,
        ))
    if not request.evidence_ids:
        out.append(Violation(
            check="design.no_evidence_trail",
            rule="s3: candidates link back to design input and supporting evidence",
            detail="design request cites no evidence_ids",
            record_id=request.design_id,
        ))
    return out


def check_cell_summary(
    summary: CellSummary, slices: Sequence[CellSlice]
) -> list[Violation]:
    """Section 9/20: summary matches its slice, and donors are not faked as replicates."""
    out: list[Violation] = []
    by_id = {s.slice_id: s for s in slices}
    sl = by_id.get(summary.cell_slice_id)
    if sl is None:
        out.append(Violation(
            check="cellsummary.unknown_slice",
            rule="s13: cell summary matches its selected slice and filters",
            detail=f"references unknown cell_slice_id {summary.cell_slice_id!r}",
            record_id=summary.summary_id,
        ))
    elif sl.census_release != summary.census_release:
        out.append(Violation(
            check="cellsummary.release_mismatch",
            rule="s20: do not silently join across Census releases",
            detail=f"summary release {summary.census_release!r} != slice release {sl.census_release!r}",
            record_id=summary.summary_id,
        ))
    if summary.donor_count is None:
        out.append(Violation(
            check="cellsummary.no_donor_count",
            rule="s9: record sample sizes; summarize per donor before condition",
            detail="donor_count is None",
            record_id=summary.summary_id,
        ))
    elif summary.donor_count <= 1 and summary.cell_count and summary.cell_count > 100:
        out.append(Violation(
            check="cellsummary.pseudoreplication",
            rule="s9: thousands of cells from one donor are not thousands of replicates",
            detail=f"{summary.cell_count} cells from {summary.donor_count} donor(s); "
                   "condition-level claims are not supported",
            severity="warning",
            record_id=summary.summary_id,
        ))
    if not summary.expression_metric or not summary.expression_method:
        out.append(Violation(
            check="cellsummary.undefined_metric",
            rule="s9: label percent-positive definition and normalization",
            detail="expression_metric/expression_method not both set",
            record_id=summary.summary_id,
        ))
    if summary.primary_data_handling is None:
        out.append(Violation(
            check="cellsummary.no_primary_handling",
            rule="s9: use primary-data handling to avoid duplicate counts",
            detail="primary_data_handling is unset",
            record_id=summary.summary_id,
        ))
    return out


def check_embedding_alignment(
    emb: EmbeddingSlice, slices: Sequence[CellSlice]
) -> list[Violation]:
    """Section 20: identity-based alignment, release match, no zero-filling.

    The acceptance test in section 20 is that a row-order perturbation is caught.
    That is only possible if vectors travel with their soma_joinids; this check
    enforces the precondition. See tests/ for the perturbation test itself.
    """
    out: list[Violation] = []
    by_id = {s.slice_id: s for s in slices}
    sl = by_id.get(emb.cell_slice_id)
    if sl is None:
        out.append(Violation(
            check="embedding.unknown_slice",
            rule="s20: validate row identity via release + organism + soma_joinid",
            detail=f"references unknown cell_slice_id {emb.cell_slice_id!r}",
            record_id=emb.embedding_slice_id,
        ))
    else:
        if sl.census_release != emb.census_release:
            out.append(Violation(
                check="embedding.release_mismatch",
                rule="s20: a release mismatch is rejected, not silently joined",
                detail=f"embedding release {emb.census_release!r} != slice {sl.census_release!r}",
                record_id=emb.embedding_slice_id,
            ))
        if sl.organism != emb.organism:
            out.append(Violation(
                check="embedding.organism_mismatch",
                rule="s20: composite key is release + organism + soma_joinid",
                detail=f"{emb.organism!r} != slice organism {sl.organism!r}",
                record_id=emb.embedding_slice_id,
            ))
    if not emb.aligned_cell_ids_ref:
        out.append(Violation(
            check="embedding.no_cell_ids",
            rule="s20: never join vectors by an assumed incidental row order",
            detail="aligned_cell_ids_ref is unset; row identity cannot be verified",
            record_id=emb.embedding_slice_id,
        ))
    if emb.vectors_ref and not emb.valid_row_mask_ref:
        out.append(Violation(
            check="embedding.no_valid_mask",
            rule="s20: absent rows may contain NaNs; never fill missing vectors with zero",
            detail="vectors present without a valid_row_mask_ref",
            record_id=emb.embedding_slice_id,
        ))
    # Coverage must be reported as requested/retrieved/valid (section 20).
    counts = {"requested": emb.requested_count, "retrieved": emb.retrieved_count,
              "valid": emb.valid_count}
    missing = [k for k, v in counts.items() if v is None]
    if missing:
        out.append(Violation(
            check="embedding.coverage_unreported",
            rule="s20: report requested, retrieved and valid-embedding cell counts",
            detail=f"missing counts: {', '.join(missing)}",
            record_id=emb.embedding_slice_id,
        ))
    else:
        if emb.valid_count > emb.retrieved_count or emb.retrieved_count > emb.requested_count:
            out.append(Violation(
                check="embedding.impossible_coverage",
                rule="s20: counts and coverage are inspectable and consistent",
                detail=f"requested={emb.requested_count} retrieved={emb.retrieved_count} "
                       f"valid={emb.valid_count} is not monotonically decreasing",
                record_id=emb.embedding_slice_id,
            ))
    return out


def check_run_state(run: DesignRun, candidates: Sequence[Candidate]) -> list[Violation]:
    """Section 7/8: job state is well-formed, and compute success != scientific success."""
    out: list[Violation] = []
    if run.status in TERMINAL_RUN_STATES and not run.finished_at:
        out.append(Violation(
            check="run.terminal_without_timestamp",
            rule="s8: show timestamps and errors in understandable language",
            detail=f"status={run.status!r} but finished_at is None",
            record_id=run.run_id,
        ))
    if run.status in {"failed", "timed_out"} and not run.error:
        out.append(Violation(
            check="run.failed_without_reason",
            rule="s10: retain failed outputs and reasons",
            detail=f"status={run.status!r} with no error message",
            record_id=run.run_id,
        ))
    if run.status != "succeeded" and candidates:
        out.append(Violation(
            check="run.candidates_before_success",
            rule="s8: represent result availability and scientific assessment separately",
            detail=f"{len(candidates)} candidate(s) attached to a run with status {run.status!r}",
            record_id=run.run_id,
        ))
    if not run.input_hash:
        out.append(Violation(
            check="run.no_input_hash",
            rule="s7: reuse identical completed jobs by input/model/config hash",
            detail="input_hash is unset; duplicate-submission guard cannot work",
            record_id=run.run_id,
        ))
    if not run.execution_handle and run.status not in {"draft", "awaiting_selection", "ready"}:
        out.append(Violation(
            check="run.no_execution_handle",
            rule="s7: persist the association between app run ID and execution handle",
            detail=f"status={run.status!r} without an execution_handle",
            record_id=run.run_id,
        ))
    return out


def check_candidates(
    candidates: Sequence[Candidate], run: DesignRun
) -> list[Violation]:
    """Section 10: metrics are labelled predictions; candidates stay tied to their run."""
    out: list[Violation] = []
    for c in candidates:
        if c.run_id != run.run_id:
            out.append(Violation(
                check="candidate.wrong_run",
                rule="s18: real design outputs remain tied to the target actually submitted",
                detail=f"candidate run_id {c.run_id!r} != {run.run_id!r}",
                record_id=c.candidate_id,
            ))
        if c.provenance is None or c.provenance.origin == "fixture":
            out.append(Violation(
                check="candidate.fixture_origin",
                rule="s3: fixtures never count as biological results",
                detail="candidate has fixture or missing origin; cannot be reported as a result",
                severity="warning" if c.provenance else "error",
                record_id=c.candidate_id,
            ))
        for m in c.metrics:
            if m.value is not None and not m.method:
                out.append(Violation(
                    check="metric.no_method",
                    rule="s10: metrics are model-specific; state the method",
                    detail=f"metric {m.name!r} has a value but no method",
                    record_id=c.candidate_id,
                ))
            # Section 10: do not invent an affinity or report a score as a Kd.
            if m.is_prediction and m.name.lower() in {"kd", "ki", "ic50", "ec50", "affinity"}:
                out.append(Violation(
                    check="metric.predicted_affinity_as_measurement",
                    rule="s10: do not invent an affinity or report a model score as a measured Kd",
                    detail=f"predicted metric named {m.name!r} implies a measured binding constant",
                    record_id=c.candidate_id,
                ))
        if c.evaluation_status == "passed" and not c.metrics:
            out.append(Violation(
                check="candidate.passed_without_metrics",
                rule="s13: missing evidence cannot silently become a favorable score",
                detail="evaluation_status='passed' with no metrics recorded",
                record_id=c.candidate_id,
            ))
    return out


# --- section 18: agentic / gating checks ------------------------------------

def check_design_gate(request: DesignRequest) -> list[Violation]:
    """Section 18: only a validated, reviewed request may trigger the paid tool."""
    out: list[Violation] = []
    if request.review_status != "approved":
        out.append(Violation(
            check="gate.unreviewed_design",
            rule="s18: only a scientifically reviewed request can trigger the paid design tool",
            detail=f"review_status={request.review_status!r}; launch must be refused",
            record_id=request.design_id,
        ))
    if request.review_status == "approved" and not request.reviewed_by:
        out.append(Violation(
            check="gate.anonymous_approval",
            rule="s10: review with the scientific/design owner is the decision gate",
            detail="approved without recording who reviewed it",
            record_id=request.design_id,
        ))
    return out


def check_no_duplicate_paid_run(
    request: DesignRequest, existing_runs: Sequence[DesignRun]
) -> list[Violation]:
    """Section 7/18: an identical request must reuse, not relaunch.

    Returns a violation when a live or completed run already carries this input
    hash -- the caller is expected to attach to it rather than submit again.
    """
    h = request.input_hash()
    clashes = [r for r in existing_runs if r.input_hash == h
               and r.status not in {"failed", "cancelled", "timed_out"}]
    if clashes:
        return [Violation(
            check="gate.duplicate_paid_run",
            rule="s7: reuse identical completed jobs by input/model/config hash",
            detail=f"input_hash {h[:12]} already has run(s) "
                   f"{', '.join(r.run_id for r in clashes)}; attach instead of resubmitting",
            record_id=request.design_id,
        )]
    return []


def check_modality_fit(assessments: Sequence[Assessment]) -> list[Violation]:
    """Section 17/18: a strong-association intracellular target must be flagged.

    The rule is not "exclude it" -- the spec wants its strong biology shown
    alongside the modality mismatch. So a target with favorable biology and weak
    design feasibility must carry either an exclusion reason or a feasibility
    rationale; silently ranking it first is the failure.
    """
    out: list[Violation] = []
    for a in assessments:
        favorable = any(r.rating in FAVORABLE_RATINGS for r in a.ratings)
        if favorable and a.design_feasibility in {"weak", "unknown"}:
            if not (a.excluded and a.exclusion_reason) and not a.design_feasibility_rationale:
                out.append(Violation(
                    check="rank.unflagged_modality_mismatch",
                    rule="s18: a strong-association target with incompatible modality is "
                         "flagged or excluded with a reason",
                    detail=f"design_feasibility={a.design_feasibility!r} alongside favorable "
                           "biology, with no rationale or exclusion reason",
                    record_id=a.target_id,
                ))
    return out


def check_determinism(rank_fn, assessments: Sequence[Assessment], rounds: int = 3) -> list[Violation]:
    """Section 18: the same assessments must always produce the same order.

    Catches ranking that depends on dict iteration order, set ordering, an
    unseeded shuffle or a model call.
    """
    import random
    orders = []
    for _ in range(rounds):
        shuffled = list(assessments)
        random.shuffle(shuffled)
        orders.append([a.target_id for a in rank_fn(shuffled)])
    if len({tuple(o) for o in orders}) > 1:
        return [Violation(
            check="rank.nondeterministic",
            rule="s18: ranking behaviour is deterministic and versioned",
            detail=f"{rounds} runs over the same assessments gave {len({tuple(o) for o in orders})} "
                   f"distinct orders: {orders}",
        )]
    return []


def check_origin_labels(records: Iterable[Any]) -> list[Violation]:
    """Section 8/11: no unmarked demo data anywhere in the report path."""
    out: list[Violation] = []
    for r in records:
        prov = getattr(r, "provenance", None)
        rid = getattr(r, "evidence_id", None) or getattr(r, "run_id", None) \
            or getattr(r, "summary_id", None) or getattr(r, "target_id", None)
        if prov is None:
            out.append(Violation(
                check="origin.unlabelled",
                rule="s8: include explicit real/cached_real/fixture origin labels",
                detail=f"{type(r).__name__} carries no provenance",
                record_id=rid,
            ))
    return out


# --- aggregate ---------------------------------------------------------------

def validate_all(
    *,
    brief: ResearchBrief | None = None,
    targets: Sequence[Target] = (),
    evidence: Sequence[Evidence] = (),
    assessments: Sequence[Assessment] = (),
    cell_slices: Sequence[CellSlice] = (),
    cell_summaries: Sequence[CellSummary] = (),
    embedding_slices: Sequence[EmbeddingSlice] = (),
    design_request: DesignRequest | None = None,
    design_runs: Sequence[DesignRun] = (),
    candidates: Sequence[Candidate] = (),
    rank_fn=None,
) -> Report:
    """Run every check that applies to the records supplied. Absent inputs skip.

    Never raises on bad data -- it reports. A validator that crashes on the
    malformed record it was meant to catch is worse than no validator.
    """
    rep = Report()

    def run(name: str, fn, *args):
        rep.checks_run.append(name)
        try:
            rep.add(*fn(*args))
        except Exception as exc:  # a broken check must not mask the others
            rep.add(Violation(
                check=f"{name}.crashed",
                rule="validators must report, not raise",
                detail=f"{type(exc).__name__}: {exc}",
                severity="warning",
            ))

    if evidence:
        run("evidence_provenance", check_evidence_provenance, evidence)
    if assessments:
        run("evidence_links", check_evidence_links, assessments, evidence)
        run("modality_fit", check_modality_fit, assessments)
    if assessments or evidence:
        run("target_association", check_target_association, assessments, targets, evidence)
    if brief is not None:
        run("identity", check_identity, brief, targets)
    for s in cell_summaries:
        run("cell_summary", check_cell_summary, s, cell_slices)
    for e in embedding_slices:
        run("embedding_alignment", check_embedding_alignment, e, cell_slices)
    if design_request is not None:
        run("design_identity", check_design_target_identity, design_request, targets)
        run("design_gate", check_design_gate, design_request)
        run("duplicate_paid_run", check_no_duplicate_paid_run, design_request, design_runs)
    for r in design_runs:
        run("run_state", check_run_state, r, [c for c in candidates if c.run_id == r.run_id])
        run("candidates", check_candidates, [c for c in candidates if c.run_id == r.run_id], r)
    if rank_fn is not None and assessments:
        run("determinism", check_determinism, rank_fn, assessments)

    return rep
