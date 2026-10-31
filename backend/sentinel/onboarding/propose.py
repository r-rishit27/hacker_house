"""Propose a mapping for a new bank's schema.

The model reads the schema's structure only — vertex and edge names, attribute
names and types, edge directions — and the canonical model. It never sees the
schema file's comments, which for a test bank hold the expected answer. What it
returns is checked by ``mapping.validate``; anything that names a vertex,
attribute or edge the bank does not have, or a path that does not connect, is
sent back with the reasons, at most twice. A proposal that still fails is
returned with its problems listed, never silently trimmed: a person decides.

Value filters (``IsCstmr = true``, ``APPLICATION = "SIGN.ON"``) usually depend on
data values the schema does not show; the model is asked to say so in
``needs_review`` rather than invent values.
"""

from __future__ import annotations

import json
import os
import time

from sentinel.onboarding import mapping as M
from sentinel.onboarding.canonical import ATTRIBUTES, CONCEPTS, RELATIONS, TRANSFORMS
from sentinel.onboarding.schema import Schema

SYSTEM = """You map a bank's graph schema onto a fraud-investigation tool's canonical model.
You see only the schema's structure. Use exact vertex, attribute and edge names from it; never invent one.

Rules:
- status: "exact" when the bank has the thing as such; "approximate" when only a proxy exists (a flag instead of a
  case, an attribute instead of a vertex, a party-level rating for an account-level score, a date without time);
  "unmapped" when nothing fits. Prefer "unmapped" to a wrong mapping.
- A concept may have several sources (two account types; transfers and teller cash are both transactions).
- A concept may come from an attribute of another vertex (source.attribute) when the bank has no vertex for it.
- A source may need a value filter on an attribute (a party type, a customer flag, an application code). If the
  values are not visible in the schema, give the attribute, your best value guess and say so in needs_review.
- Watch for traps: payment orders/instructions/batches vs executed transfers; ledger/statement postings derived
  from transactions; returns of earlier transactions; "ultimate" parties vs the actual debtor/creditor; bank-issued
  cards named "devices"; fraud scores or alert flags vs investigation cases; external counterparties stored in the
  same vertex type as customers.
- A relation is a path of edge names from a vertex mapped to its FROM concept to a vertex mapped to its TO concept,
  in the canonical direction. To traverse a directed edge backwards use its reverse edge name. Paths may have
  several steps. Give one path per (start, end) source pair that applies.
- A transaction often has no direct edge to the device or address it came from. Follow the chain from the
  executed transaction through what it executes or was authenticated by (an order, an authentication, a session,
  a sign-on) to the device or address, and map FROM_DEVICE / FROM_IP to that multi-hop path as "approximate".
- Transaction.timestamp must carry the time of day when the schema has one: prefer an acceptance, execution or
  creation date-time over a settlement, value or booking date. Fraud patterns are minutes apart.
- Attributes: name the vertex and attribute; give the transform from: """ + ", ".join(TRANSFORMS) + """.
  String dates need yyyymmdd / yymmddhhmm / yyyymmddhhmmss. Use "code" for numeric or letter codes whose meanings
  need a lookup table.
- Write reasons briefly, in plain words."""

_FILTER = {"type": ["object", "null"], "additionalProperties": False, "required": ["vertex", "attribute", "op", "values"],
           "properties": {"vertex": {"type": ["string", "null"]}, "attribute": {"type": "string"},
                          "op": {"type": "string", "enum": list(M.OPS)}, "values": {"type": "array", "items": {"type": "string"}}}}
_SOURCE = {"type": "object", "additionalProperties": False, "required": ["vertex", "attribute", "transform", "filter"],
           "properties": {"vertex": {"type": "string"}, "attribute": {"type": ["string", "null"]},
                          "transform": {"type": ["string", "null"], "enum": [*TRANSFORMS, None]}, "filter": _FILTER}}
_STATUS = {"type": "string", "enum": list(M.STATUSES)}
SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["concepts", "relations", "attributes", "needs_review"],
    "properties": {
        "concepts": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                "required": ["concept", "status", "sources", "reason"],
                                                "properties": {"concept": {"type": "string", "enum": list(CONCEPTS)},
                                                               "status": _STATUS, "sources": {"type": "array", "items": _SOURCE},
                                                               "reason": {"type": "string"}}}},
        "relations": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                 "required": ["relation", "status", "paths", "reason"],
                                                 "properties": {"relation": {"type": "string", "enum": list(RELATIONS)},
                                                                "status": _STATUS, "reason": {"type": "string"},
                                                                "paths": {"type": "array", "items": {
                                                                    "type": "object", "additionalProperties": False,
                                                                    "required": ["start", "steps", "end", "filter"],
                                                                    "properties": {"start": {"type": "string"},
                                                                                   "steps": {"type": "array", "items": {"type": "string"}},
                                                                                   "end": {"type": "string"}, "filter": _FILTER}}}}}},
        "attributes": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                  "required": ["target", "status", "sources", "reason"],
                                                  "properties": {"target": {"type": "string", "enum": list(ATTRIBUTES)},
                                                                 "status": _STATUS, "sources": {"type": "array", "items": _SOURCE},
                                                                 "reason": {"type": "string"}}}},
        "needs_review": {"type": "array", "items": {"type": "string"}},
    },
}


def _clean(src: dict) -> dict:
    return {k: v for k, v in src.items() if v not in (None, [], "")}


def to_mapping(reply: dict, schema: Schema, fingerprint: str, model: str) -> dict:
    out = {"bank": schema.graph, "schema_sha256": fingerprint, "proposed_by": model, "concepts": {}, "relations": {},
           "attributes": {}, "needs_review": reply.get("needs_review", [])}
    for c in reply["concepts"]:
        out["concepts"][c["concept"]] = {"status": c["status"], "sources": [_clean(s) for s in c["sources"]], "reason": c["reason"]}
    for r in reply["relations"]:
        out["relations"][r["relation"]] = {"status": r["status"], "paths": [_clean(p) | {"steps": p["steps"]} for p in r["paths"]],
                                           "reason": r["reason"]}
    for a in reply["attributes"]:
        out["attributes"][a["target"]] = {"status": a["status"], "sources": [_clean(s) for s in a["sources"]], "reason": a["reason"]}
    for name in CONCEPTS:
        out["concepts"].setdefault(name, {"status": "unmapped", "sources": [], "reason": "not proposed"})
    for name in RELATIONS:
        out["relations"].setdefault(name, {"status": "unmapped", "paths": [], "reason": "not proposed"})
    return out


def model_name() -> str:
    return os.getenv("OPENAI_ONBOARDING_MODEL") or os.getenv("OPENAI_DECISION_MODEL") or "gpt-4.1-mini"


def propose(schema: Schema, schema_text: str, *, client=None, repairs: int = 2) -> dict:
    from openai import OpenAI

    client = client or OpenAI(timeout=float(os.getenv("OPENAI_TIMEOUT_S", "180"))).chat.completions
    model = model_name()
    spec = {"concepts": CONCEPTS, "relations": {k: {"from": a, "to": b, "meaning": m} for k, (a, b, m) in RELATIONS.items()},
            "attributes": ATTRIBUTES}
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": json.dumps({"canonical_model": spec, "bank_schema": schema.describe()})}]
    tokens, t0, mapping, problems = 0, time.perf_counter(), {}, []
    for attempt in range(1 + repairs):
        r = client.create(model=model, messages=messages,
                          response_format={"type": "json_schema", "json_schema": {"name": "mapping", "strict": True, "schema": SCHEMA}})
        tokens += int(getattr(r.usage, "total_tokens", 0) or 0)
        reply = json.loads(r.choices[0].message.content)
        mapping = to_mapping(reply, schema, M.schema_fingerprint(schema_text), model)
        problems = M.validate(schema, mapping)
        if not problems:
            break
        messages += [{"role": "assistant", "content": r.choices[0].message.content},
                     {"role": "user", "content": "These parts do not match the schema. Fix them and return the whole "
                                                 "mapping again:\n- " + "\n- ".join(problems[:40])}]
    mapping["proposal"] = {"model": model, "attempts": attempt + 1, "tokens": tokens,
                           "seconds": round(time.perf_counter() - t0, 1), "problems": problems}
    return mapping
