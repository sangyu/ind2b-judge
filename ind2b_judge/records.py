"""Typed records shared across every module (spec sections 8 and 20).

Two rules the whole team codes against, from section 8:

  * Unknown numeric values are None, NEVER 0. A zero is a measurement; a None is
    an absence. Collapsing them is how a target with no evidence quietly ranks
    above one with weak-but-real evidence.
  * Every record carries `schema_version` and an `origin` label. A fixture that
    loses its label becomes a fabricated biological result the moment it reaches
    a slide.

Dataclasses, not pydantic: zero new dependencies on either the laptop or the
Modal image, and the checks that actually matter here are semantic rather than
type-level -- they live in contracts/validators.py.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

SCHEMA_VERSION = "0.1.0"

# --- controlled vocabularies (spec sections 7, 8, 9, 16) ---------------------

# Where a value came from. Section 3: "Fixtures never count as biological
# results." Section 11: "never present a replay as a newly completed model run."
Origin = Literal["real", "cached_real", "fixture"]

# Section 8, design job states. Compute status only -- see evaluation_status for
# the scientific verdict, which is deliberately a separate axis: a job can
# succeed and yield zero acceptable candidates.
RunStatus = Literal[
    "draft", "awaiting_selection", "ready", "queued",
    "running", "succeeded", "failed", "timed_out", "cancelled",
]
TERMINAL_RUN_STATES = frozenset({"succeeded", "failed", "timed_out", "cancelled"})

# Section 16, agent workflow outcomes -- distinct from RunStatus.
AgentOutcome = Literal[
    "completed_candidate_report", "evidence_report_only", "needs_clarification",
    "insufficient_evidence", "no_binder_feasible_target", "source_unavailable",
    "budget_exhausted", "design_failed",
]

# Section 8: evidence direction. "context" is neither support nor contradiction.
Direction = Literal["supports", "contradicts", "context"]

# Section 9 rubric. "unknown" is a first-class rating, not a synonym for "weak".
Rating = Literal["strong", "moderate", "weak", "unknown"]

# Section 17: association does not establish whether inhibition or activation
# helps. Unknown direction stays explicit.
Modulation = Literal["inhibit", "activate", "degrade", "block_interaction", "unknown"]

ReviewStatus = Literal["unreviewed", "in_review", "approved", "rejected"]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    import uuid
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def stable_hash(payload: Any) -> str:
    """Deterministic hash of a job's inputs, for the section 7 idempotency rule.

    Sorted keys so that dict ordering can never make the same request look new
    and start a second paid design job.
    """
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


@dataclass
class Provenance:
    """Where a record came from and whether it is real (section 8)."""

    origin: Origin
    source: str                       # "opentargets:graphql:v4", "census:2025-01-30"
    retrieved_at: str = field(default_factory=_utcnow)
    query: dict[str, Any] | None = None     # exact query/variables sent
    release: str | None = None              # API/data release, where exposed
    notes: str | None = None


@dataclass
class Record:
    """Base: schema_version on everything, per section 8."""

    schema_version: str = field(default=SCHEMA_VERSION, init=False)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["schema_version"] = self.schema_version
        return d


# --- brief and targets -------------------------------------------------------

@dataclass
class ResearchBrief(Record):
    brief_id: str
    raw_indication: str                       # preserve the user's own words
    desired_effect: str | None = None
    moa: str | None = None
    modality: str | None = None
    species: str = "Homo sapiens"
    # Open Targets resolution
    disease_id: str | None = None             # e.g. "MONDO_0005061"
    disease_label: str | None = None
    resolution_candidates: list[dict[str, Any]] = field(default_factory=list)
    resolution_rationale: str | None = None
    # CELLxGENE mapping is a SEPARATE field: section 17 forbids assuming the two
    # systems use identical strings.
    census_disease_id: str | None = None
    census_disease_label: str | None = None
    census_mapping_provenance: Provenance | None = None
    status: Literal["pending", "confirmed"] = "pending"
    provenance: Provenance | None = None


@dataclass
class Target(Record):
    target_id: str
    gene_symbol: str
    stable_gene_id: str                        # Ensembl, e.g. ENSG00000146648
    protein_accession: str | None = None       # UniProt; None until mapped
    species: str = "Homo sapiens"
    proposed_modulation: Modulation = "unknown"
    provenance: Provenance | None = None


@dataclass
class Evidence(Record):
    evidence_id: str
    subject_id: str                            # usually a target_id
    predicate: str                             # "associated_with", "modulates", ...
    object_id: str | None = None
    object_value: Any = None
    evidence_type: str = "unknown"             # genetic|clinical|literature|expression
    direction: Direction = "context"
    source_url: str | None = None
    source_locator: str | None = None          # DOI, section, figure, API path
    supporting_excerpt: str | None = None
    measurement: dict[str, Any] | None = None
    limitations: str | None = None
    # Section 17: keep direct vs ontology-propagated evidence distinguishable.
    disease_scope: str | None = None           # the disease this item REALLY refers to
    is_propagated: bool = False
    provenance: Provenance | None = None


# --- cellular context (sections 9 and 20) ------------------------------------

@dataclass
class CellSlice(Record):
    """A bounded, reproducible Census selection (section 20)."""

    slice_id: str
    census_release: str                        # pinned; never "latest"
    organism: str
    filters: dict[str, Any] = field(default_factory=dict)
    dataset_ids: list[str] = field(default_factory=list)
    soma_joinids_ref: str | None = None        # artifact ref, not inline vectors
    gene_ids: list[str] = field(default_factory=list)
    eligible_count: int | None = None          # full population before sampling
    sampled_count: int | None = None
    sampling_method: str | None = None         # section 20: no arbitrary first-N
    seed: int | None = None
    is_primary_data: bool | None = None
    provenance: Provenance | None = None


@dataclass
class EmbeddingSlice(Record):
    """Precomputed Census embeddings aligned to a CellSlice (section 20)."""

    embedding_slice_id: str
    cell_slice_id: str
    embedding_name: str
    census_release: str                        # MUST equal the CellSlice release
    organism: str
    dimension: int | None = None
    aligned_cell_ids_ref: str | None = None    # soma_joinids, in vector row order
    vectors_ref: str | None = None
    valid_row_mask_ref: str | None = None      # missing rows are NOT zero-filled
    requested_count: int | None = None
    retrieved_count: int | None = None
    valid_count: int | None = None
    coverage: dict[str, Any] = field(default_factory=dict)  # by donor/condition
    model_provenance: dict[str, Any] | None = None
    checksum: str | None = None
    provenance: Provenance | None = None


@dataclass
class CellSummary(Record):
    """Donor-aware expression summary (section 9)."""

    summary_id: str
    target_id: str
    cell_slice_id: str
    census_release: str
    tissue: str | None = None
    condition: str | None = None
    canonical_population_id: str | None = None
    canonical_population_label: str | None = None
    original_labels: list[str] = field(default_factory=list)
    donor_count: int | None = None
    cell_count: int | None = None
    per_donor: list[dict[str, Any]] = field(default_factory=list)
    expression_metric: str | None = None       # e.g. "percent_positive"
    expression_method: str | None = None       # normalization / pct-positive defn
    values: dict[str, Any] = field(default_factory=dict)
    primary_data_handling: str | None = None
    limitations: str | None = None
    provenance: Provenance | None = None


@dataclass
class NeighborhoodEvidence(Record):
    evidence_id: str
    embedding_slice_id: str
    query_description: str
    reference_universe: str                    # the ACTUAL search space, not "Census"
    distance_metric: str = "cosine"
    preprocessing: str | None = None
    k: int | None = None
    neighbor_ids: list[int] = field(default_factory=list)
    distances: list[float] = field(default_factory=list)
    biological_filters: dict[str, Any] = field(default_factory=dict)
    caveats: str | None = None
    provenance: Provenance | None = None


# --- assessment and design ---------------------------------------------------

@dataclass
class CriterionRating(Record):
    criterion: str
    rating: Rating
    evidence_ids: list[str] = field(default_factory=list)
    missingness: str | None = None
    rationale: str | None = None


@dataclass
class Assessment(Record):
    target_id: str
    rubric_version: str
    ratings: list[CriterionRating] = field(default_factory=list)
    disease_rationale: str | None = None
    moa_rationale: str | None = None
    liabilities: list[str] = field(default_factory=list)
    # Section 9: design feasibility stays SEPARATE from biological support.
    design_feasibility: Rating = "unknown"
    design_feasibility_rationale: str | None = None
    review_status: ReviewStatus = "unreviewed"
    excluded: bool = False
    exclusion_reason: str | None = None


@dataclass
class DesignRequest(Record):
    design_id: str
    brief_id: str
    selected_target_id: str
    protein_sequence_ref: str | None = None
    sequence_checksum: str | None = None
    structure_id: str | None = None
    structure_version: str | None = None
    chain_residue_mapping: dict[str, Any] = field(default_factory=dict)
    binding_region: dict[str, Any] = field(default_factory=dict)
    moa_rationale: str | None = None
    evidence_ids: list[str] = field(default_factory=list)
    model: str | None = None
    model_config: dict[str, Any] = field(default_factory=dict)
    # Section 18: only a validated, scientifically reviewed request may launch a
    # paid design job.
    review_status: ReviewStatus = "unreviewed"
    reviewed_by: str | None = None

    def input_hash(self) -> str:
        """Idempotency key (section 7): same target+structure+site+model+config."""
        return stable_hash({
            "target": self.selected_target_id,
            "sequence_checksum": self.sequence_checksum,
            "structure": (self.structure_id, self.structure_version),
            "binding_region": self.binding_region,
            "model": self.model,
            "config": self.model_config,
        })


@dataclass
class DesignRun(Record):
    run_id: str
    design_id: str
    execution_route: str                       # "modal:gpu", "external:<service>"
    execution_handle: str | None = None
    status: RunStatus = "draft"
    input_hash: str | None = None
    model: str | None = None
    model_version: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    seed: int | None = None
    git_commit: str | None = None
    started_at: str | None = None
    updated_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    artifact_refs: list[str] = field(default_factory=list)
    provenance: Provenance | None = None


@dataclass
class Metric(Record):
    name: str
    value: float | None                        # None when not computed, never 0
    method: str | None = None
    model_version: str | None = None
    # Section 10: do not report a model score as a measured Kd.
    is_prediction: bool = True
    units: str | None = None


@dataclass
class Candidate(Record):
    candidate_id: str
    run_id: str
    sequence: str | None = None
    structure_ref: str | None = None
    metrics: list[Metric] = field(default_factory=list)
    evaluation_status: Literal["pending", "passed", "failed", "not_evaluated"] = "pending"
    limitations: str | None = None
    proposed_validation: str | None = None
    provenance: Provenance | None = None


# --- agent run log (section 16) ----------------------------------------------

@dataclass
class ToolAction(Record):
    tool_name: str
    tool_version: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)   # redacted as needed
    result_refs: list[str] = field(default_factory=list)
    elapsed_s: float | None = None
    error: str | None = None
    reason: str | None = None                  # brief rationale, not private CoT
    at: str = field(default_factory=_utcnow)


@dataclass
class AgentRun(Record):
    run_id: str
    brief_id: str
    current_stage: str = "resolve_indication"
    status: str = "running"
    plan_summary: str | None = None
    pending_questions: list[str] = field(default_factory=list)
    tool_action_log: list[ToolAction] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    excluded_targets: list[dict[str, str]] = field(default_factory=list)
    candidate_pool_scope: dict[str, Any] = field(default_factory=dict)
    budgets_remaining: dict[str, int] = field(default_factory=dict)
    design_run_ids: list[str] = field(default_factory=list)
    final_outcome: AgentOutcome | None = None
    # Judge row P0-11 (generality) asks for two runs on different indications
    # with no code edits between them. That is only checkable if each AgentRun
    # records the commit it ran at -- DesignRun.git_commit is too late, since a
    # run that never reaches design still has to prove it wasn't hardcoded.
    git_commit: str | None = None
    started_at: str = field(default_factory=_utcnow)
    updated_at: str | None = None
