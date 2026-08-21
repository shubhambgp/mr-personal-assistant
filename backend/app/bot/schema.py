"""Reads etl/manifest.yaml and exposes what the agent layer needs from it.

Nothing here hardcodes a table or column name. The manifest is the single
source of truth (see CLAUDE.md), so pointing at a different dataset changes
this module's output without changing its code.

CACHING: the manifest is parsed once and cached against the file's mtime.
This is not premature optimisation — it was measured. The previous version
re-parsed the YAML on every call, and `run_sql` calls both pii_columns() and
queryable_columns(), so ~17-25ms of every run_sql invocation was YAML parsing
against ~0.5-1ms of actual SQL. The parse was 20x the query it guarded.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

_ETL_DIR = Path(__file__).resolve().parents[2] / "etl"
_configured = os.environ.get("MR_BOT_MANIFEST")
MANIFEST_PATH = (
    Path(_configured)
    if _configured and Path(_configured).is_absolute()
    else (Path(__file__).resolve().parents[2] / _configured)
    if _configured
    else _ETL_DIR / "manifest.yaml"
)


@lru_cache(maxsize=4)
def _parse(path: str, mtime: float) -> dict:
    """Cached parse. `mtime` is part of the key so editing the file invalidates it."""
    del mtime  # only present to key the cache
    with Path(path).open() as f:
        return yaml.safe_load(f)


def _manifest() -> dict:
    return _parse(str(MANIFEST_PATH), MANIFEST_PATH.stat().st_mtime)


def _target_columns(cfg: dict) -> list[tuple[str, bool]]:
    """[(canonical_column_name, is_pii), ...] for one table's manifest entry."""
    out = []
    for spec in cfg["columns"].values():
        if isinstance(spec, str):
            out.append((spec, False))
        else:
            out.append((spec["rename"], bool(spec.get("pii"))))
    if cfg.get("name_norm_from"):
        out.append(("name_norm", False))
    return out


def pii_columns() -> frozenset[str]:
    """Canonical column names flagged `pii: true` anywhere in the manifest.

    Callers must read the denylist from here rather than hardcoding it —
    a second copy could only drift.
    """
    manifest = _manifest()
    return frozenset(
        col
        for cfg in manifest["tables"].values()
        for col, is_pii in _target_columns(cfg)
        if is_pii
    )


def queryable_columns() -> dict[str, list[str]]:
    """{relation: [non-PII canonical columns]} for every table AND view.

    This is what run_sql's scoped CTEs enumerate. Listing columns explicitly
    instead of `SELECT *` is what keeps PII out of the *result rows*: the PII
    guard can only inspect the query text, so `SELECT * FROM my_doctors` would
    never type `mobile` and would hand it back anyway. That was a real leak;
    this function is the fix.
    """
    manifest = _manifest()
    out: dict[str, list[str]] = {
        name: [col for col, is_pii in _target_columns(cfg) if not is_pii]
        for name, cfg in manifest["tables"].items()
    }
    pii = pii_columns()
    for name, vcfg in (manifest.get("views") or {}).items():
        out[name] = [c for c in vcfg["columns"] if c not in pii]
    return out


def scope_kinds() -> dict[str, str]:
    """{relation: 'chair' | 'doctor' | 'global'} — how run_sql scopes each one.

    Anything absent defaults to 'chair' at the call site, i.e. it fails closed:
    a new relation is rep-scoped unless the manifest says otherwise.
    """
    manifest = _manifest()
    out = {name: cfg.get("scope", "chair") for name, cfg in manifest["tables"].items()}
    for name, vcfg in (manifest.get("views") or {}).items():
        out[name] = vcfg.get("scope", "chair")
    return out


def base_relations() -> list[str]:
    """Every physical table and view name — the run_sql denylist.

    The model must go through the `my_*` scoped aliases; naming a base relation
    directly would read every rep's rows.
    """
    manifest = _manifest()
    return list(manifest["tables"].keys()) + list((manifest.get("views") or {}).keys())


def internal_names() -> frozenset[str]:
    """Scoped aliases plus every multi-word canonical column name.

    Used by guardrails.check_internal_disclosure to detect the data model
    leaking into a rep-facing answer. Built from the manifest, so a new column
    is covered without anyone remembering to add it here.
    """
    columns = queryable_columns()
    names = {f"my_{relation}" for relation in columns}
    names |= {c for cols in columns.values() for c in cols if "_" in c}
    return frozenset(names)


BUSINESS_GLOSSARY = """
RCPA — Rx Call Point Analysis; the prescription value attributed to a doctor for a brand.
MCR — Monthly Call Rate; MCR Coverage is measured against mcr_threshold.
MVC / MV Frequency — Monthly Visit Compliance / visit frequency, measured against mvc_threshold.
V1/V2/... (visit_freq) — required visits per month for a doctor (V1 = 1/month, V2 = 2/month).
CM / PM — Current Month / Previous Month, prefixing brand metrics (e.g. cm_prescribed_qty).
Brand priority P1..P6 — P1 is the highest-priority brand for that doctor; brand_rank orders within it.
growth_booster ("GB") — flags a brand under an active growth push for that doctor.
persona_prescriber — Consistent / New / Non-Prescriber tiers.
persona_loyalty — loyalty tier (e.g. Potential Loyalist, Non Loyalist).
persona_digital — Digital Active / Inactive.
accompanied_by — which manager(s) joined a visit: ABM/RBM/ZBM/HO/BO, singly or combined.
""".strip()
