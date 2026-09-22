"""Record -> view-model projections: the only supported way to render a record.

Judge row P1-1 requires that every rendered value equals its stored value, that
null renders as a gap rather than 0, and that origin survives the trip from
store to screen. Those are properties of a *projection*, so they are testable
without a browser -- provided the UI actually goes through one.

Hence this module. The UI imports these functions instead of reaching into
records directly; tests/test_ui.py then grades the projection, and any UI built
on it inherits the guarantees. A view that formats records by hand is outside
the contract and P1-1 cannot be verified for it.

Every projection returns plain JSON-safe dicts and preserves three things:
  * the value, unrounded (format at the last moment, in the template)
  * `null` as None -- never coerced to 0, "", or "N/A" in the data layer
  * the origin label, so a fixture can never render as a live result
"""

from __future__ import annotations

from typing import Any

from .records import Candidate, CellSummary, DesignRun, Metric, Provenance

# What the UI must show when a value is genuinely absent. A gap, not a zero.
NULL_DISPLAY = "not measured"


def _origin_of(prov: Provenance | None) -> str:
    """Unlabelled data renders as 'unlabelled', never as real.

    Failing closed here means a record that slipped past validation still
    cannot masquerade as a live result on screen.
    """
    return prov.origin if prov else "unlabelled"


def project_provenance(prov: Provenance | None) -> dict[str, Any]:
    if prov is None:
        return {"origin": "unlabelled", "source": None, "retrieved_at": None,
                "is_live": False, "display_note": "origin not recorded"}
    # Section 11: a cached result must show its original run time and never be
    # presented as a newly completed run.
    is_live = prov.origin == "real"
    note = None
    if prov.origin == "cached_real":
        note = f"cached from {prov.retrieved_at}"
    elif prov.origin == "fixture":
        note = "FIXTURE — not a biological result"
    return {
        "origin": prov.origin,
        "source": prov.source,
        "retrieved_at": prov.retrieved_at,
        "release": prov.release,
        "is_live": is_live,
        "display_note": note,
    }


def project_metric(m: Metric) -> dict[str, Any]:
    """A metric as the UI may show it.

    `value` stays unrounded and stays None when absent. `label` carries the
    section 10 distinction: a predicted score is never displayed as a bare
    number that could read as a measurement.
    """
    predicted_prefix = "predicted " if m.is_prediction else "measured "
    return {
        "name": m.name,
        "value": m.value,                       # None stays None
        "display": NULL_DISPLAY if m.value is None else m.value,
        "units": m.units,
        "method": m.method,
        "model_version": m.model_version,
        "is_prediction": m.is_prediction,
        "label": f"{predicted_prefix}{m.name}",
        "qualifier": ("model prediction, not a measured quantity"
                      if m.is_prediction else None),
    }


def project_candidate(c: Candidate) -> dict[str, Any]:
    return {
        "candidate_id": c.candidate_id,
        "run_id": c.run_id,
        "sequence": c.sequence,
        "sequence_length": len(c.sequence) if c.sequence else None,
        "structure_ref": c.structure_ref,
        "metrics": [project_metric(m) for m in c.metrics],
        "evaluation_status": c.evaluation_status,
        "limitations": c.limitations,
        "proposed_validation": c.proposed_validation,
        "provenance": project_provenance(c.provenance),
    }


def project_cell_summary(s: CellSummary) -> dict[str, Any]:
    """Section 9: sample sizes are displayed, and n means donors, not cells."""
    donors = s.donor_count
    cells = s.cell_count
    # The caveat is computed here rather than left to the template, so every
    # view that shows this summary shows the same warning.
    caveat = None
    if donors is not None and donors <= 1 and cells and cells > 100:
        caveat = (f"{cells} cells from {donors} donor — cells are not "
                  "independent replicates; condition-level claims unsupported")
    return {
        "summary_id": s.summary_id,
        "target_id": s.target_id,
        "tissue": s.tissue,
        "condition": s.condition,
        "population": s.canonical_population_label,
        "original_labels": list(s.original_labels),
        "donor_count": donors,
        "cell_count": cells,
        "n_display": (f"n = {donors} donors" if donors is not None
                      else "n = donors not recorded"),
        "per_donor": list(s.per_donor),
        "expression_metric": s.expression_metric,
        "expression_method": s.expression_method,
        "values": dict(s.values),
        "caveat": caveat,
        "limitations": s.limitations,
        "provenance": project_provenance(s.provenance),
    }


def project_run(run: DesignRun) -> dict[str, Any]:
    """Section 11: pending, failed, empty and timed-out all need real states."""
    human = {
        "draft": "not yet submitted",
        "awaiting_selection": "waiting for target selection",
        "ready": "ready to submit",
        "queued": "queued on the cluster",
        "running": "running",
        "succeeded": "compute finished",      # NOT "candidates found"
        "failed": "failed",
        "timed_out": "timed out",
        "cancelled": "cancelled",
    }
    return {
        "run_id": run.run_id,
        "design_id": run.design_id,
        "status": run.status,
        "status_display": human.get(run.status, run.status),
        "execution_route": run.execution_route,
        "execution_handle": run.execution_handle,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "error": run.error,
        "model": run.model,
        "model_version": run.model_version,
        "artifact_refs": list(run.artifact_refs),
        "provenance": project_provenance(run.provenance),
    }


def project_chart_series(
    records: list[Any], key: str, value_attr: str
) -> list[dict[str, Any]]:
    """Chart data straight from records, with nulls preserved as gaps.

    P1-1 requires element count == record count and null rendered as a gap.
    Dropping null points would satisfy neither, so they are emitted with
    value=None and the renderer is expected to break the line there.
    """
    out = []
    for r in records:
        v = getattr(r, value_attr, None)
        out.append({
            "key": getattr(r, key, None),
            "value": v,                        # None => gap, never 0
            "is_gap": v is None,
            "origin": _origin_of(getattr(r, "provenance", None)),
        })
    return out
