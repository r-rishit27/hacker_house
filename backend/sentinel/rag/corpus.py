"""Building the document corpus the agent reasons over.

GraphRAG is a required component and `evidence[].source == "document"` is its
visible footprint in the graded file. Neither is reachable while `PolicyDoc` has
zero rows, so this module is the difference between claiming GraphRAG and
having it.

The corpus is **parsed from Guide.md rather than transcribed into Python.** That
is the one design decision here worth defending: a hand-typed copy of R5 in a
source file is a second source of truth for the policy the agent is graded
against, and the moment it drifts the agent is citing a rule the bank does not
have. The parser is coupled to Guide.md's heading structure, which is stable
because Guide.md is an input we were given, not a file we maintain.

Three corpora, one vertex type:

- **Policy** — the 10 rules, the action table, the routing table, §3a, and the
  four procedural sections. Roughly 20 chunks. This is what the `PolicyDoc`
  requirement, the `APPLIED_RULE` edges and the SAR narrative rest on.
- **Patterns and reference** — the five documented patterns, the glossary, the
  "Things to know" list, the regulatory reference index.
- **Closed cases** — the 5,565 `analyst_notes`, embedded in place on their own
  vertices rather than copied into `PolicyDoc`. They are already in the graph;
  what they are missing is `emb`.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sentinel.config.settings import Settings
from sentinel.graph.normalize import ResponseNormalizer
from sentinel.graph.repository import GraphRepository, VertexUpsert
from sentinel.rag.embeddings import (
    DIMENSIONS_META_KEY,
    MODEL_META_KEY,
    EmbeddingService,
)

logger = logging.getLogger(__name__)

#: Upserted per POST. 200 chunks of policy text is a small body; 200 closed
#: cases at 256 dimensions is about 1 MB, which REST++ takes without complaint.
UPSERT_BATCH = 200

#: Closed case ids sort lexicographically, so a range scan pages them stably.
CLOSED_CASE_PAGE = 250


@dataclass(frozen=True, slots=True)
class DocumentChunk:
    """One retrievable passage, and where in the source it came from."""

    doc_id: str
    source: str
    section: str
    title: str
    text: str
    emb: list[float] = field(default_factory=list)

    @property
    def fingerprint(self) -> str:
        return EmbeddingService.fingerprint(self.text)

    def as_attrs(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {
            "source": self.source,
            "section": self.section,
            "title": self.title,
            "text": self.text,
        }
        if self.emb:
            attrs["emb"] = self.emb
        return attrs

    def with_embedding(self, emb: Sequence[float]) -> DocumentChunk:
        return DocumentChunk(
            doc_id=self.doc_id,
            source=self.source,
            section=self.section,
            title=self.title,
            text=self.text,
            emb=list(emb),
        )


@dataclass(frozen=True, slots=True)
class IngestReport:
    """What one ingest pass did, in the terms a resumed run cares about."""

    kind: str
    seen: int
    embedded: int
    skipped: int
    written: int
    dimensions: int

    def __str__(self) -> str:
        return (
            f"{self.kind}: {self.seen} seen, {self.embedded} embedded, "
            f"{self.skipped} already current, {self.written} written at {self.dimensions}d"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "seen": self.seen,
            "embedded": self.embedded,
            "skipped": self.skipped,
            "written": self.written,
            "dimensions": self.dimensions,
        }


# ── parsing Guide.md ─────────────────────────────────────────────────────────


_RULE = re.compile(r"^\*\*(R\d+)\.\s+(.+?)\*\*", re.MULTILINE)
_PATTERN = re.compile(r"^\*\*(\d+)\.\s+(.+?)\.\*\*", re.MULTILINE)

#: The five documented patterns, in Guide.md's order, mapped to the enum values
#: the answer file must use. The order is load-bearing: Guide.md numbers them.
PATTERN_SLUGS: tuple[str, ...] = (
    "card_testing",
    "card_not_present_fraud",
    "card_not_present_new_device",
    "out_of_region_use",
    "account_takeover",
)

#: `### N. Title` in the Fraud Policy section -> the doc_id suffix it becomes.
#: Anything not named here is skipped rather than given a generated id, so a new
#: section in Guide.md is a visible gap rather than a silent `POL-SECTION-9`.
_POLICY_SECTIONS: dict[str, str] = {
    "0": "START",
    "1": "ACTIONS",
    "2": "ROUTING",
    "3a": "3A",
    "3b": "3B",
    "4": "EXPOSURE",
    "5": "EVIDENCE",
    "6": "STOPPING",
    "7": "EXPLAINING",
}


def _split_headings(text: str, level: int) -> list[tuple[str, str]]:
    """``[(heading, body)]`` for every heading at exactly this level."""
    marker = "#" * level
    pattern = re.compile(rf"^{marker} (?!#)(.+)$", re.MULTILINE)
    matches = list(pattern.finditer(text))
    out: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        out.append((match.group(1).strip(), text[start:end].strip()))
    return out


def parse_policy(guide: str) -> list[DocumentChunk]:
    """Every chunk Guide.md's `# Fraud Policy` section yields.

    Raises when the section is absent: an empty corpus that ingests cleanly is
    the failure mode this whole module exists to prevent.
    """
    sections = dict(_split_headings(guide, 1))
    body = sections.get("Fraud Policy")
    if body is None:
        raise ValueError("Guide.md has no '# Fraud Policy' section; the parser is out of date")

    chunks: list[DocumentChunk] = []
    for heading, text in _split_headings(body, 3):
        number, _, title = heading.partition(".")
        number, title = number.strip(), title.strip()
        if number == "3":
            chunks.extend(_parse_rules(text))
            continue
        suffix = _POLICY_SECTIONS.get(number)
        if suffix is None:
            logger.warning("Guide.md policy section '%s' has no doc_id mapping; skipped", heading)
            continue
        chunks.append(
            DocumentChunk(
                doc_id=f"POL-{suffix}",
                source="policy",
                section=number,
                title=f"Fraud Policy §{number}. {title}",
                text=f"Fraud Policy §{number}. {title}\n\n{text}",
            )
        )
    return chunks


def _parse_rules(text: str) -> list[DocumentChunk]:
    """R1..R10, one chunk each, each carrying its own heading."""
    matches = list(_RULE.finditer(text))
    if len(matches) < 10:
        raise ValueError(f"Guide.md §3 yielded {len(matches)} rules, expected 10")
    chunks: list[DocumentChunk] = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        rule_id, title = match.group(1), match.group(2).rstrip(".")
        chunks.append(
            DocumentChunk(
                doc_id=f"POL-{rule_id}",
                source="policy",
                section=rule_id,
                title=f"{rule_id}. {title}",
                text=text[start:end].strip(),
            )
        )
    return chunks


def parse_reference(guide: str) -> list[DocumentChunk]:
    """The patterns, the glossary, the things-to-know list and the regulators."""
    sections = dict(_split_headings(guide, 2))
    chunks: list[DocumentChunk] = []

    patterns = sections.get("The five known fraud patterns", "")
    matches = list(_PATTERN.finditer(patterns))
    if len(matches) != len(PATTERN_SLUGS):
        raise ValueError(
            f"Guide.md yielded {len(matches)} fraud patterns, expected {len(PATTERN_SLUGS)}"
        )
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(patterns)
        slug = PATTERN_SLUGS[index]
        chunks.append(
            DocumentChunk(
                doc_id=f"PAT-{slug}",
                source="pattern",
                section=slug,
                title=f"Pattern {match.group(1)}: {match.group(2)}",
                text=patterns[start:end].strip(),
            )
        )

    glossary = sections.get("Glossary", "")
    for term, meaning in _table_rows(glossary):
        slug = _slug(term)
        if not slug:
            continue
        chunks.append(
            DocumentChunk(
                doc_id=f"GLO-{slug}",
                source="glossary",
                section=slug,
                title=f"Glossary: {term}",
                text=f"{term}: {meaning}",
            )
        )

    for index, bullet in enumerate(_bullets(sections.get("Things to know", "")), start=1):
        chunks.append(
            DocumentChunk(
                doc_id=f"KNOW-{index:02d}",
                source="guidance",
                section=f"know-{index:02d}",
                title=_first_bold(bullet) or f"Things to know {index}",
                text=bullet,
            )
        )

    regulatory = sections.get("Regulatory references", "")
    if regulatory:
        chunks.append(
            DocumentChunk(
                doc_id="REG-index",
                source="regulatory",
                section="index",
                title="Regulatory references",
                text=f"Regulatory references\n\n{regulatory}",
            )
        )
    return chunks


def parse_local_regulatory(directory: Path) -> list[DocumentChunk]:
    """Regulatory text already extracted to disk, windowed for retrieval.

    The FinCEN and FFIEC documents are public PDFs. Fetching and extracting them
    at demo time would put a network call and a PDF parser on the critical path,
    so extracted ``.txt``/``.md`` files are read from ``backend/var/corpus/``
    when present and the corpus is simply smaller when they are not. Nothing
    downstream requires them: the SAR narrative's who/what/when/where/how/why
    requirement is stated in the policy itself, at ``POL-3A``.
    """
    if not directory.is_dir():
        return []
    chunks: list[DocumentChunk] = []
    for path in sorted(directory.glob("*")):
        if path.suffix.lower() not in {".txt", ".md"}:
            continue
        stem = _slug(path.stem).upper()
        text = path.read_text(encoding="utf-8", errors="replace")
        for index, window in enumerate(_windows(text), start=1):
            chunks.append(
                DocumentChunk(
                    doc_id=f"REG-{stem}#{index:02d}",
                    source="regulatory",
                    section=f"{stem}-{index:02d}",
                    title=f"{path.stem} (part {index})",
                    text=window,
                )
            )
    return chunks


# ── ingest ───────────────────────────────────────────────────────────────────


@dataclass
class CorpusBuilder:
    """Builds the corpus, embeds it and writes it into the graph.

    Responsibility: parse, embed, upsert, and report honestly about what it
    skipped. It performs no retrieval.
    Collaborators: ``EmbeddingService`` for the vectors; ``GraphRepository`` for
    the write; ``Settings`` for the guide path and the corpus directory.
    """

    settings: Settings
    graph: GraphRepository
    embeddings: EmbeddingService
    #: `run_query` flattens and sanitises, but a printed vertex set arrives as
    #: `{v_id, v_type, attributes}` with alias-prefixed attribute keys. Unpacking
    #: that is the caller's job, and this is the object that does it.
    normalizer: ResponseNormalizer = field(default_factory=ResponseNormalizer)

    @property
    def guide_path(self) -> Path:
        return self.settings.root / "Guide.md"

    async def ingest_policy(self, *, force: bool = False) -> IngestReport:
        """Every `PolicyDoc` chunk, embedded and written."""
        guide = self.guide_path.read_text(encoding="utf-8")
        chunks = [
            *parse_policy(guide),
            *parse_reference(guide),
            *parse_local_regulatory(self.settings.corpus_dir),
        ]
        existing = {} if force else await self._existing_policy_docs()
        fresh = [c for c in chunks if not self._current(c, existing.get(c.doc_id))]
        logger.info("policy corpus: %d chunks, %d need embedding", len(chunks), len(fresh))

        embedded: list[DocumentChunk] = []
        for start in range(0, len(fresh), UPSERT_BATCH):
            batch = fresh[start : start + UPSERT_BATCH]
            vectors = await self.embeddings.embed([c.text for c in batch])
            embedded.extend(c.with_embedding(v) for c, v in zip(batch, vectors, strict=True))

        written = await self._upsert(
            [VertexUpsert("PolicyDoc", c.doc_id, c.as_attrs()) for c in embedded]
        )
        await self._write_meta()
        return IngestReport(
            kind="policy",
            seen=len(chunks),
            embedded=len(embedded),
            skipped=len(chunks) - len(fresh),
            written=written,
            dimensions=self.embeddings.dimensions,
        )

    async def ingest_closed_cases(
        self, *, limit: int | None = None, force: bool = False
    ) -> IngestReport:
        """Embed `ClosedCase.analyst_notes` in place, resumably.

        The note is the only free text the bank's own investigations carry, and
        it is what makes "has this shape of case been seen before?" a vector
        question rather than a pattern-equality one.
        """
        seen = embedded = skipped = written = 0
        for page in _id_ranges(CLOSED_CASE_PAGE):
            rows = await self._closed_case_page(*page)
            if not rows:
                continue
            pending: list[tuple[str, str]] = []
            for row in rows:
                seen += 1
                note = str(row.get("analyst_notes") or "").strip()
                if not note:
                    skipped += 1
                    continue
                if not force and self._has_current_vector(row):
                    skipped += 1
                    continue
                pending.append((str(row["case_id"]), _closed_case_text(row)))
                if limit is not None and seen >= limit:
                    break

            if pending:
                vectors = await self.embeddings.embed([text for _, text in pending])
                written += await self._upsert(
                    [
                        VertexUpsert("ClosedCase", case_id, {"emb": vector})
                        for (case_id, _), vector in zip(pending, vectors, strict=True)
                    ]
                )
                embedded += len(pending)
                logger.info(
                    "closed cases %s..%s: +%d embedded (%d total)",
                    page[0],
                    page[1],
                    len(pending),
                    embedded,
                )
            if limit is not None and seen >= limit:
                break

        await self._write_meta()
        return IngestReport(
            kind="closed_cases",
            seen=seen,
            embedded=embedded,
            skipped=skipped,
            written=written,
            dimensions=self.embeddings.dimensions,
        )

    # ── graph access ─────────────────────────────────────────────────────────

    async def _existing_policy_docs(self) -> dict[str, dict[str, Any]]:
        payload = await self.graph.run_query("policy_docs_all", {})
        rows = self.normalizer.rows(payload.get("docs"))
        return {str(row["doc_id"]): row for row in rows if row.get("doc_id")}

    async def _closed_case_page(self, from_id: str, to_id: str) -> list[dict[str, Any]]:
        payload = await self.graph.run_query(
            "closed_case_range", {"from_id": from_id, "to_id": to_id, "with_emb": True}
        )
        return self.normalizer.rows(payload.get("cases"))

    async def _upsert(self, vertices: Sequence[VertexUpsert]) -> int:
        written = 0
        for start in range(0, len(vertices), UPSERT_BATCH):
            result = await self.graph.upsert_batch(vertices=vertices[start : start + UPSERT_BATCH])
            written += result.vertices
        return written

    async def _write_meta(self) -> None:
        """Record the width and model, so a later run can refuse a mismatch."""
        await self.graph.upsert_batch(
            vertices=[
                VertexUpsert(
                    "MetaDoc", DIMENSIONS_META_KEY, {"value": str(self.embeddings.dimensions)}
                ),
                VertexUpsert("MetaDoc", MODEL_META_KEY, {"value": self.embeddings.model}),
            ]
        )

    def _current(self, chunk: DocumentChunk, existing: dict[str, Any] | None) -> bool:
        """Whether the stored row already holds a vector for this exact text."""
        if existing is None:
            return False
        if str(existing.get("text") or "") != chunk.text:
            return False
        return self._has_current_vector(existing)

    def _has_current_vector(self, row: dict[str, Any]) -> bool:
        emb = row.get("emb")
        return isinstance(emb, list) and len(emb) == self.embeddings.dimensions


# ── text shaping ─────────────────────────────────────────────────────────────


def _closed_case_text(row: dict[str, Any]) -> str:
    """What a closed case is embedded as.

    The note plus its structured facts, because "confirmed_fraud" and
    "card_testing" are exactly the words a query about a suspected card-testing
    episode will carry, and they are not always in the prose.
    """
    parts = [
        f"{row.get('pattern') or 'unknown pattern'} · {row.get('outcome') or 'unknown outcome'}",
        f"exposure ${float(row.get('exposure_usd') or 0.0):.2f} over "
        f"{int(row.get('n_txns') or 0)} transaction(s)",
        f"actions {row.get('actions_taken') or 'none'}",
        str(row.get("analyst_notes") or "").strip(),
    ]
    return "\n".join(part for part in parts if part)


def _id_ranges(page: int, total: int = 6000) -> Iterable[tuple[str, str]]:
    """``('CC-0001', 'CC-0251')`` … — half-open ranges over the closed-case ids."""
    for start in range(1, total, page):
        yield f"CC-{start:04d}", f"CC-{start + page:04d}"


def _table_rows(markdown: str) -> list[tuple[str, str]]:
    """Two-column markdown table rows, header and separator dropped."""
    rows: list[tuple[str, str]] = []
    for line in markdown.splitlines():
        line = line.strip()
        if not line.startswith("|") or set(line) <= set("|-: "):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 2 or cells[0].lower() == "term":
            continue
        rows.append((cells[0].replace("**", "").strip(), cells[1]))
    return rows


def _bullets(markdown: str) -> list[str]:
    """Top-level ``- `` bullets, each whole."""
    out: list[str] = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            out.append(stripped[2:].strip())
        elif out and stripped and not stripped.startswith("#"):
            out[-1] = f"{out[-1]} {stripped}"
    return out


def _first_bold(text: str) -> str:
    match = re.search(r"\*\*(.+?)\*\*", text)
    return match.group(1).rstrip(".") if match else ""


def _windows(text: str, size: int = 3_200, overlap: int = 400) -> list[str]:
    """Character windows with overlap — roughly 800 tokens with 100 of context."""
    body = " ".join(text.split())
    if not body:
        return []
    step = max(size - overlap, 1)
    return [body[start : start + size] for start in range(0, len(body), step)]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
