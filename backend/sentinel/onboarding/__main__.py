"""Onboard a new bank's graph to Sentinel.

    cd backend
    python -m sentinel.onboarding propose ../bank3_core_banking_t24.gsql   # schema -> onboarding/banks/<Bank>/proposed.json
    #   edit onboarding/banks/<Bank>/review.json: the changes a person makes, each with a reason
    python -m sentinel.onboarding build Bank_CoreT24          # proposed + review -> mapping.json, validated
    python -m sentinel.onboarding capabilities Bank_CoreT24   # which detectors run, which are off and why
    python -m sentinel.onboarding score Bank_CoreT24          # test banks only: proposal vs the expected mapping
    python -m sentinel.onboarding approve Bank_CoreT24 --token <team lead / fraud manager token>
    python -m sentinel.onboarding dryrun Bank_CoreT24         # the unchanged detectors on the bank's sample data
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BANKS = ROOT / "onboarding" / "banks"


def _bank(name: str) -> Path:
    d = BANKS / name
    if not d.is_dir():
        sys.exit(f"no onboarded bank {name!r} in {BANKS}")
    return d


def cmd_propose(a) -> None:
    from dotenv import load_dotenv

    from sentinel.onboarding import mapping as M, propose as P, schema as S

    load_dotenv(ROOT / ".env")
    text = Path(a.schema).read_text(encoding="utf-8")
    s = S.parse(text)
    d = BANKS / s.graph
    d.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(a.schema, d / "schema.gsql")
    m = P.propose(s, text)
    M.save(m, d / "proposed.json")
    if not (d / "review.json").exists():
        (d / "review.json").write_text(json.dumps({"by": "", "at": "", "changes": []}, indent=1) + "\n", encoding="utf-8")
    print(f"{s.graph}: proposed by {m['proposal']['model']} in {m['proposal']['attempts']} attempt(s), "
          f"{m['proposal']['tokens']} tokens, {m['proposal']['seconds']} s -> {d / 'proposed.json'}")
    for p in m["proposal"]["problems"]:
        print("  still inconsistent:", p)
    for n in m.get("needs_review", []):
        print("  needs review:", n)


def cmd_build(a) -> None:
    from sentinel.onboarding import mapping as M, review as R, schema as S

    d = _bank(a.bank)
    s = S.load(d / "schema.gsql")
    m, problems = R.build(s, M.load(d / "proposed.json"), json.loads((d / "review.json").read_text(encoding="utf-8")))
    if problems:
        print("refused: the reviewed mapping does not match the schema:")
        for p in problems:
            print("  -", p)
        sys.exit(1)
    M.save(m, d / "mapping.json")
    print(f"{a.bank}: mapping.json written ({len(m['review']['changes'])} reviewed change(s)); it needs approval before use")


def cmd_capabilities(a) -> None:
    from sentinel.onboarding import capabilities as C, mapping as M

    d = _bank(a.bank)
    m = M.load(d / ("mapping.json" if (d / "mapping.json").exists() else "proposed.json"))
    r = C.report(m)
    for det, v in r["detectors"].items():
        mark = "runs" if v["runs"] else "OFF "
        extra = f"  missing: {', '.join(v['missing'])}" if v["missing"] else (
            f"  (approximate: {', '.join(v['approximate_inputs'])})" if v["approximate_inputs"] else "")
        print(f"  {mark}  {det}{extra}")


def cmd_score(a) -> None:
    from sentinel.onboarding import mapping as M, schema as S, truth as T

    d = _bank(a.bank)
    s = S.load(d / "schema.gsql")
    for name in ("proposed.json", "mapping.json"):
        if not (d / name).exists():
            continue
        r = T.score(s, M.load(d / name))
        print(f"{name:14} {r['items']} items · mapped-or-not agrees {r['mapped_agrees']} · status {r['status_agrees']} · "
              f"source {r['source_ok']}/{r['source_checked']} · wrong {r['wrong']} · missed {r['missed']}")


def cmd_approve(a) -> None:
    from dotenv import load_dotenv

    from sentinel.onboarding import review as R

    load_dotenv(ROOT / ".env")
    rec = R.approve(_bank(a.bank), a.token)
    print(f"{a.bank}: approved by {rec['approved_by']} ({rec['role']}), mapping {rec['mapping_sha256'][:12]}")


def cmd_dryrun(a) -> None:
    from sentinel.onboarding import adapter as A, capabilities as C, fixtures as F, mapping as M, schema as S
    from sentinel.typologies.detectors import DETECTORS

    d = _bank(a.bank)
    s, m = S.load(d / "schema.gsql"), M.load(d / "mapping.json")
    if a.bank not in F.BANKS:
        sys.exit(f"no sample data for {a.bank}; dry runs use onboarding.fixtures")
    src = A.MappedTxnSource(s, m, A.InMemoryGraph(s, F.BANKS[a.bank]()))
    acct, on = F.ACCOUNT0[a.bank], C.enabled(m)
    txns = src.card_txns(acct, F.AS_OF)
    print(f"{a.bank}: account {acct} has {len(txns)} transactions before {F.AS_OF:%Y-%m-%d}")
    for key, det in DETECTORS.items():
        if key not in on:
            print(f"  off   {key}  (not supported by this bank's mapping)")
            continue
        h = det(acct, txns, src, F.AS_OF)
        print(f"  {'FIRES' if h else 'quiet'} {key}" + (f": {h.description[:150]}… connected {list(h.connected_card_ids)}" if h else ""))


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m sentinel.onboarding", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("propose").add_argument("schema")
    for c in ("build", "capabilities", "score", "dryrun"):
        sub.add_parser(c).add_argument("bank")
    p = sub.add_parser("approve")
    p.add_argument("bank")
    p.add_argument("--token", required=True)
    a = ap.parse_args()
    {"propose": cmd_propose, "build": cmd_build, "capabilities": cmd_capabilities, "score": cmd_score,
     "approve": cmd_approve, "dryrun": cmd_dryrun}[a.cmd](a)


if __name__ == "__main__":
    main()
