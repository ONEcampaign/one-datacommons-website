"""De-bespoke spike: can detect+confirm replace construct_sv_dcid for every dev-finance golden?

For each of the 13 dev-finance goldens (domain: development_finance in goldens.json), runs:
  1. detect_svs(query) — replayed from fixture under --offline; live against staging under --live.
  2. node_arcs per candidate SV → confirm existence + read five-tuple + constraint values.
  3. Group confirmed SVs by five-tuple; keep those whose constraint values match the golden's
     bound slots (scheme, purpose, recipient from expected_slots).
  4. Compare the resulting SV set to the golden's expected_stat_vars.
  5. Print a per-golden line: id, MATCH or MISMATCH(reason), and for a mismatch whether
     construct_sv_dcid would have produced the golden SV.

Hard cases specifically examined:
  - df-04: aggregate OfficialDevelopmentAssistance (66-SV noisy detect; expected SV not in fixture)
  - df-09: unbound scheme, expected_stat_vars=[] with has_data proven by probing
  - df-12: no_observations to Nauru (SV confirmed but no obs)

Usage:
    uv run --extra eval --extra engine python scripts/spike_debespoke.py --offline
    uv run --env-file .env --extra eval --extra engine python scripts/spike_debespoke.py --live
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Resolve project root and add src to path (script runs from query_engine/)
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).parent.parent
_FIXTURES = _ROOT / "tests" / "fixtures"
_GOLDENS = _ROOT / "goldens.json"

sys.path.insert(0, str(_ROOT / "src"))

from qre.engine.families.dev_finance import construct_sv_dcid  # noqa: E402
from qre.engine.graph import LiveGraphClient  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers: read five-tuple and constraint values from node arcs
# ---------------------------------------------------------------------------

_FIVE_TUPLE_PROPS = (
    "populationType",
    "measuredProperty",
    "statType",
    "measurementQualifier",
    "measurementDenominator",
)

_CONSTRAINT_PROPS = (
    "DevelopmentFinanceScheme",
    "DevelopmentFinancePurpose",
    "DevelopmentFinanceRecipient",
)


def _first_dcid(arcs: dict, prop: str) -> str | None:
    nodes = arcs.get(prop, {}).get("nodes", [])
    return nodes[0].get("dcid") if nodes else None


def _constraint_props_from_arcs(arcs: dict) -> list[str]:
    """Read the constraintProperties list from node arcs."""
    nodes = arcs.get("constraintProperties", {}).get("nodes", [])
    return [n["dcid"] for n in nodes if "dcid" in n]


def _five_tuple(arcs: dict) -> tuple[str | None, ...]:
    return tuple(_first_dcid(arcs, p) for p in _FIVE_TUPLE_PROPS)


def _constraint_values(arcs: dict, constraint_props: list[str]) -> dict[str, str | None]:
    return {p: _first_dcid(arcs, p) for p in constraint_props}


# ---------------------------------------------------------------------------
# Slot matching: given expected_slots from a golden, extract bound values
# ---------------------------------------------------------------------------

def _bound_slots(expected_slots: list[dict]) -> dict[str, str | None]:
    """Return {property_dcid: value_dcid | None} for bound constraint slots.

    binding_kind == 'unbound' maps to None.
    """
    result: dict[str, str | None] = {}
    for slot in expected_slots:
        prop = slot.get("property_dcid")
        if prop in _CONSTRAINT_PROPS:
            if slot.get("binding_kind") == "value":
                result[prop] = slot.get("value_dcid")
            else:
                result[prop] = None  # unbound
    return result


# ---------------------------------------------------------------------------
# Core spike logic per golden
# ---------------------------------------------------------------------------

def _run_one(
    golden: dict,
    *,
    graph,
) -> dict:
    """Run detect+confirm+match for one golden. Returns a verdict dict."""
    gid = golden["id"]
    query = golden["query"]
    expected_svs = set(golden.get("expected_stat_vars") or [])
    expected_slots = golden.get("expected_slots", [])
    bound = _bound_slots(expected_slots)

    # Step 1: detect candidates
    candidate_svs, _entities = graph.detect_svs(query)
    df_candidates = [sv for sv in candidate_svs if sv.startswith("ONE/CRS_DAC/")]

    # Step 2: confirm via node_arcs and read five-tuple + constraint values
    confirmed: list[dict] = []
    for sv in df_candidates:
        arcs = graph.node_arcs(sv)
        if arcs is None:
            continue
        ft = _five_tuple(arcs)
        cprops = _constraint_props_from_arcs(arcs)
        cvals = _constraint_values(arcs, cprops)
        confirmed.append({"sv": sv, "five_tuple": ft, "cvals": cvals})

    # Step 3: filter by bound slots
    # Keep SVs whose constraint values match every bound slot.
    # Unbound slots (None) accept any value → no filter applied for that prop.
    # When bound is empty (no constraint slots extracted — e.g. nd-02, df-11),
    # every confirmed SV passes, revealing the inherent limit of detect+confirm alone.
    matched_svs: list[str] = []
    for entry in confirmed:
        sv = entry["sv"]
        cvals = entry["cvals"]
        keep = True
        for prop, expected_val in bound.items():
            if expected_val is None:
                # unbound: no filter on this property
                continue
            if cvals.get(prop) != expected_val:
                keep = False
                break
        if keep:
            matched_svs.append(sv)

    matched_set = set(matched_svs)
    expected_no_data_reason = golden.get("expected_no_data_reason")

    # Step 4: compare to expected_stat_vars
    if matched_set == expected_svs:
        verdict = "MATCH"
        reason = None
        construct_would_hit = None
    else:
        missing = expected_svs - matched_set
        extra = matched_set - expected_svs
        parts = []
        if missing:
            parts.append(f"missing={sorted(missing)}")
        if extra:
            parts.append(f"extra={sorted(extra)}")
        reason = "; ".join(parts) or "unknown"

        # Classify why the mismatch occurs
        if expected_no_data_reason == "entity_not_resolved":
            # nd-02: entity resolution is upstream of SV selection; detect+confirm
            # is not responsible for this guard — the engine's entity resolution
            # stage handles it regardless of SV construction path.
            reason += " [upstream: entity_not_resolved — not a construct_sv_dcid concern]"
        elif expected_no_data_reason == "denominator_not_available":
            # df-11: the per-capita denominator check is an upstream engine guard.
            # Neither detect+confirm nor construct_sv_dcid controls this.
            reason += " [upstream: denominator gate — not a construct_sv_dcid concern]"
        elif not bound:
            # No slots were extracted, so no filter applied — every confirmed SV passes.
            reason += " [no slots extracted: all confirmed SVs pass through]"

        verdict = f"MISMATCH({reason})"

        # Check if construct_sv_dcid would have produced the golden SV
        construct_would_hit = False
        if "DevelopmentFinanceScheme" in bound and "DevelopmentFinancePurpose" in bound:
            scheme_val = bound.get("DevelopmentFinanceScheme")
            purpose_val = bound.get("DevelopmentFinancePurpose")
            recipient_val = bound.get("DevelopmentFinanceRecipient")

            if scheme_val and purpose_val and recipient_val:
                # Handle set bindings (df-10 has multiple purposes)
                purpose_slot = next(
                    (
                        s
                        for s in expected_slots
                        if s.get("property_dcid") == "DevelopmentFinancePurpose"
                    ),
                    None,
                )
                if purpose_slot and purpose_slot.get("binding_kind") == "set":
                    val = purpose_slot.get("value_dcid")
                    purpose_dcids = [val] if val else []
                else:
                    purpose_dcids = [purpose_val] if purpose_val else []

                for p in purpose_dcids:
                    constructed = construct_sv_dcid(scheme_val, p, recipient_val)
                    if constructed in expected_svs:
                        construct_would_hit = True
                        break
            elif scheme_val is None and "DevelopmentFinanceScheme" in bound:
                # Unbound scheme (df-09): construct probes with SCHEMES[0] and returns
                # sv_dcids=[] (open scheme). The expected_svs=[] is exactly this outcome.
                if not expected_svs:
                    construct_would_hit = True  # construct's unbound path produces []
        else:
            # No constraint slots at all: upstream guard (entity/denominator) fires before
            # SV construction. construct_sv_dcid is not relevant here.
            construct_would_hit = None  # not applicable

    return {
        "id": gid,
        "query": query,
        "detect_count": len(df_candidates),
        "confirmed_count": len(confirmed),
        "matched_svs": sorted(matched_svs),
        "expected_svs": sorted(expected_svs),
        "verdict": verdict,
        "reason": reason,
        "construct_would_hit": construct_would_hit,
    }


# ---------------------------------------------------------------------------
# Offline fixture-backed graph wrapper
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


class _FixtureGraph:
    """Thin fixture-backed graph for offline mode (mirrors FakeGraph)."""

    def __init__(self) -> None:
        self._nodes = _load_json(_FIXTURES / "graph_nodes.json")
        self._detect = _load_json(_FIXTURES / "graph_detect.json")

    def detect_svs(self, query: str) -> tuple[list[str], list[str]]:
        entry = self._detect.get(query, {})
        return entry.get("svs", []), entry.get("entities", [])

    def node_arcs(self, dcid: str) -> dict | None:
        node = self._nodes.get(dcid)
        if node is None:
            return None
        arcs = node.get("arcs")
        return arcs if arcs else None


# ---------------------------------------------------------------------------
# Print verdict table and return pass/fail
# ---------------------------------------------------------------------------

_HARD_CASES = {"df-04", "df-09", "df-12"}


def _print_results(verdicts: list[dict], *, mode: str) -> bool:
    """Print a verdict table. Returns True if all matched."""
    print(f"\n=== spike_debespoke {mode.upper()} ===\n")
    header = f"{'ID':<8} {'VERDICT':<12} {'DETECT':>7} {'CONF':>5} {'MATCHED SVs / EXPECTED'}"
    print(header)
    print("-" * 80)

    all_match = True
    for v in verdicts:
        gid = v["id"]
        verdict_str = v["verdict"]
        is_match = verdict_str == "MATCH"
        if not is_match:
            all_match = False
        hard = " *" if gid in _HARD_CASES else ""
        print(
            f"{gid:<8} {verdict_str[:40]:<42} "
            f"{v['detect_count']:>3}det {v['confirmed_count']:>3}conf{hard}"
        )
        if not is_match:
            print(f"         expected: {v['expected_svs']}")
            print(f"         matched:  {v['matched_svs']}")
            if v["construct_would_hit"] is not None:
                hit = "YES" if v["construct_would_hit"] else "NO"
                print(f"         construct_sv_dcid would hit: {hit}")
    print()

    if all_match:
        print("RESULT: all 13 goldens MATCH via detect+confirm.")
        print("RECOMMENDATION: RETIRE construct_sv_dcid")
    else:
        mismatches = [v["id"] for v in verdicts if v["verdict"] != "MATCH"]
        print(f"RESULT: {len(mismatches)} mismatch(es): {mismatches}")
        construct_hits = [
            v["id"]
            for v in verdicts
            if v["verdict"] != "MATCH" and v["construct_would_hit"]
        ]
        construct_misses = [
            v["id"]
            for v in verdicts
            if v["verdict"] != "MATCH" and not v["construct_would_hit"]
        ]
        if construct_hits:
            print(f"  construct_sv_dcid recovers: {construct_hits}")
        if construct_misses:
            print(f"  construct_sv_dcid also misses: {construct_misses}")
        print("RECOMMENDATION: KEEP construct_sv_dcid as registered fallback")

    return all_match


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="De-bespoke spike: detect+confirm vs construct_sv_dcid"
    )
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument(
        "--offline", action="store_true", help="Use fixture-backed graph (no creds)"
    )
    mode_group.add_argument(
        "--live", action="store_true", help="Use live staging graph (needs GEMINI_API_KEY)"
    )
    args = parser.parse_args()

    goldens = json.loads(_GOLDENS.read_text())
    df_goldens = [
        g
        for g in goldens
        if any(t.get("domain") == "development_finance" for t in g.get("tags", []))
    ]

    # nd-02 is dev-finance domain but entity_not_resolved; include it for completeness.
    print(f"Running spike on {len(df_goldens)} dev-finance goldens...")

    if args.offline:
        graph = _FixtureGraph()
        mode = "offline"
    else:
        graph = LiveGraphClient()
        mode = "live"

    verdicts: list[dict] = []
    for golden in df_goldens:
        result = _run_one(golden, graph=graph)
        verdicts.append(result)

    all_match = _print_results(verdicts, mode=mode)

    # Special notes on hard cases
    print("--- Hard case notes ---")
    for v in verdicts:
        if v["id"] in _HARD_CASES:
            note = ""
            if v["id"] == "df-04":
                note = (
                    "df-04 (aggregate OfficialDevelopmentAssistance): "
                    f"{v['detect_count']} detect SVs, {v['confirmed_count']} confirmed in graph. "
                    "The expected SV ONE/CRS_DAC/Health-OfficialDevelopmentAssistance-ETH "
                    "may not be confirmed via detect (fixture coverage gap) — construct covers it."
                )
            elif v["id"] == "df-09":
                note = (
                    "df-09 (unbound scheme, expected_stat_vars=[]): "
                    "detect+confirm finds SVs but expected is empty (all schemes open). "
                    "The unbound path probes has_data but does not enumerate SVs — "
                    "detect+confirm cannot reproduce this; construct handles unbound probing."
                )
            elif v["id"] == "df-12":
                note = (
                    "df-12 (Nauru no_observations): detect finds "
                    "ONE/CRS_DAC/Health-ODAGrants-NRU; if confirmed, the SV matches. "
                    "The no_observations verdict comes from obs probing (zero facets), "
                    "not from detect+confirm failure — both paths require obs probing."
                )
            print(f"  {note}")
    print()

    sys.exit(0 if all_match else 1)


if __name__ == "__main__":
    main()
