"""What Sentinel can and cannot do for a bank, given its mapping.

A detector runs only when everything it reads is mapped. When something is
missing — a payments bank with no devices, a core-banking system with no cases —
the detector is switched off for that bank and the report says what it lacks,
so nobody reads "no device ring found" as "no device ring".
"""

from __future__ import annotations

from sentinel.onboarding.canonical import DETECTOR_NEEDS


def _have(mapping: dict, name: str) -> bool:
    for part in name.split("|"):
        for section in ("concepts", "relations", "attributes"):
            item = mapping.get(section, {}).get(part)
            if item and item.get("status") in ("exact", "approximate"):
                return True
    return False


def report(mapping: dict) -> dict:
    detectors = {}
    for det, needs in DETECTOR_NEEDS.items():
        missing = [n for n in needs if not _have(mapping, n)]
        approx = [n for n in needs if n not in missing and any(
            (mapping.get(s, {}).get(p) or {}).get("status") == "approximate"
            for p in n.split("|") for s in ("concepts", "relations", "attributes"))]
        detectors[det] = {"runs": not missing, "missing": missing, "approximate_inputs": approx}
    return {"bank": mapping.get("bank"), "detectors": detectors,
            "runs": sorted(d for d, v in detectors.items() if v["runs"]),
            "off": sorted(d for d, v in detectors.items() if not v["runs"])}


def enabled(mapping: dict) -> set[str]:
    return set(report(mapping)["runs"])
