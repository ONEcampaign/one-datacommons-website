#!/usr/bin/env python3
"""Extract the QRE golden corpus from golden-corpus.md into goldens.json, and
drift-check the result against golden.schema.json + the frozen contract enums.

Usage:
    eval_corpus.py export   # golden-corpus.md -> goldens.json
    eval_corpus.py check    # validate goldens.json vs schema + contract enums
    eval_corpus.py inventory  # print all keys/enum values seen (schema authoring aid)

ponytail: deterministic YAML parse, not LLM extraction -- the corpus is two clean
```yaml blocks, so a real parser is the faithful path. Run with a venv that has
PyYAML (server/.venv or dc_search_server/.venv).
"""
import json
import re
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).parent
CORPUS_MD = HERE / "golden-corpus.md"
GOLDENS_JSON = HERE / "goldens.json"
SCHEMA_JSON = HERE / "golden.schema.json"
CONTRACT_MD = HERE / ".design" / "contract.md"

# Frozen v1 enums, per contract.md decision 9. The drift check asserts the schema
# and the corpus only ever use these values; a contract change must edit them here.
FROZEN_ENUMS = {
    "status": ["definite", "candidates", "no_data"],
    "binding_kind": ["value", "set", "unbound", "absent"],
    "axis": ["what", "how", "where", "when", "source"],
    # Coverage.kind is frozen too but coverage is not carried per-golden here.
}


def _parse_yaml_blocks():
    """Return (main_records, holdout_records) from the two fenced yaml blocks."""
    text = CORPUS_MD.read_text()
    blocks = re.findall(r"```yaml\n(.*?)\n```", text, re.DOTALL)
    if len(blocks) != 2:
        sys.exit(f"expected 2 yaml blocks in {CORPUS_MD.name}, found {len(blocks)}")
    main = yaml.safe_load(blocks[0])
    holdout = yaml.safe_load(blocks[1])
    return main, holdout


def export():
    main, holdout = _parse_yaml_blocks()
    records = []
    for r in main:
        records.append({"slice": "main", **r})
    for r in holdout:
        records.append({"slice": "holdout", **r})
    GOLDENS_JSON.write_text(json.dumps(records, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {GOLDENS_JSON.name}: {len(records)} goldens "
          f"({len(main)} main + {len(holdout)} holdout)")


def inventory():
    main, holdout = _parse_yaml_blocks()
    records = main + holdout
    top_keys = set()
    slot_keys, slot_axis, slot_kind = set(), set(), set()
    ent_keys, ent_role, ent_dir = set(), set(), set()
    statuses, no_data_reasons = set(), set()
    for r in records:
        top_keys |= set(r)
        statuses.add(r.get("expected_status"))
        if r.get("expected_no_data_reason"):
            no_data_reasons.add(r["expected_no_data_reason"])
        for s in r.get("expected_slots") or []:
            slot_keys |= set(s)
            slot_axis.add(s.get("axis"))
            slot_kind.add(s.get("binding_kind"))
        for e in r.get("expected_entities") or []:
            ent_keys |= set(e)
            ent_role.add(e.get("role_kind"))
            ent_dir.add(e.get("direction"))
    out = {
        "n_records": len(records),
        "top_keys": sorted(top_keys),
        "slot_keys": sorted(slot_keys),
        "slot_axis_values": sorted(v for v in slot_axis if v is not None),
        "slot_binding_kinds": sorted(v for v in slot_kind if v is not None),
        "entity_keys": sorted(ent_keys),
        "entity_role_kinds": sorted(v for v in ent_role if v is not None),
        "entity_directions": sorted(str(v) for v in ent_dir),
        "statuses": sorted(v for v in statuses if v is not None),
        "no_data_reasons": sorted(no_data_reasons),
    }
    print(json.dumps(out, indent=2))


def _contract_enums():
    """Pull the frozen enum value lists out of contract.md so the check is anchored
    to the doc, not only to this file's FROZEN_ENUMS copy."""
    text = CONTRACT_MD.read_text()
    found = {}
    # e.g. `status` (`definite | candidates | no_data`)
    for name, pat in (
        ("status", r"`status`\s*\(`([^`]+)`\)"),
        ("binding_kind", r"`Binding\.kind`\s*\(`([^`]+)`\)"),
        ("axis", r"`SlotKey\.axis`\s*\(?`([^`]+)`\)?"),
    ):
        m = re.search(pat, text)
        if m:
            found[name] = [v.strip() for v in m.group(1).split("|")]
    return found


def check():
    failures = []

    if not GOLDENS_JSON.exists():
        sys.exit("goldens.json missing -- run `eval_corpus.py export` first")
    records = json.loads(GOLDENS_JSON.read_text())

    # 1. goldens.json is in sync with the markdown source.
    main, holdout = _parse_yaml_blocks()
    expected = [{"slice": "main", **r} for r in main] + \
               [{"slice": "holdout", **r} for r in holdout]
    if records != expected:
        failures.append("goldens.json is stale -- re-run `eval_corpus.py export`")

    # 2. The schema's enums match the frozen contract enums (decision 9 drift guard).
    contract = _contract_enums()
    for name, vals in FROZEN_ENUMS.items():
        if name in contract and contract[name] != vals:
            failures.append(
                f"enum '{name}' drift: contract.md={contract[name]} FROZEN_ENUMS={vals}")
    if SCHEMA_JSON.exists():
        schema = json.loads(SCHEMA_JSON.read_text())
        for name in ("status", "binding_kind", "axis"):
            got = _schema_enum(schema, name)
            if got is not None and got != FROZEN_ENUMS[name]:
                failures.append(
                    f"schema enum '{name}' drift: schema={got} FROZEN_ENUMS={FROZEN_ENUMS[name]}")

    # 3. Every value used in the corpus is in the frozen sets.
    for r in records:
        rid = r.get("id", "?")
        if r["expected_status"] not in FROZEN_ENUMS["status"]:
            failures.append(f"{rid}: bad status {r['expected_status']!r}")
        for s in r.get("expected_slots") or []:
            if s.get("axis") not in FROZEN_ENUMS["axis"]:
                failures.append(f"{rid}: bad axis {s.get('axis')!r}")
            if s.get("binding_kind") not in FROZEN_ENUMS["binding_kind"]:
                failures.append(f"{rid}: bad binding_kind {s.get('binding_kind')!r}")

    # 4. Structural validation against the JSON Schema, if jsonschema is installed.
    try:
        from jsonschema import Draft202012Validator
        schema = json.loads(SCHEMA_JSON.read_text())
        v = Draft202012Validator(schema)
        for r in records:
            for err in v.iter_errors(r):
                failures.append(f"{r.get('id','?')}: schema: {err.message}")
    except ModuleNotFoundError:
        print("note: jsonschema not installed; skipped structural validation")

    if failures:
        print(f"DRIFT CHECK FAILED ({len(failures)} issue(s)):")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print(f"DRIFT CHECK PASSED: {len(records)} goldens, enums match frozen contract")


def _schema_enum(schema, name):
    """Best-effort fetch of an enum list named in the schema's $defs."""
    defs = schema.get("$defs", {})
    node = defs.get(name)
    if isinstance(node, dict) and "enum" in node:
        return node["enum"]
    return None


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "export"
    {"export": export, "check": check, "inventory": inventory}.get(
        cmd, lambda: sys.exit(f"unknown command {cmd!r}"))()
