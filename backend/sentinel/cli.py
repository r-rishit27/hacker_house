"""``python -m sentinel`` — the composition root for a terminal run.

Everything the system does from a shell goes through here: run one case, run the
benchmark, validate what is on disk, ingest the GraphRAG corpus, and check that
the environment is actually in the state the run assumes.

Wiring lives here rather than in the orchestrator on purpose. The orchestrator
takes its collaborators; this module is the only place that decides which
concrete ones — which is why the same orchestrator serves a live run, a run
against the fake, and the API.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Sequence
from contextlib import AsyncExitStack
from datetime import datetime
from pathlib import Path

from sentinel.agents.orchestrator import SentinelOrchestrator
from sentinel.config.settings import Settings, get_settings
from sentinel.domain.alert import Alert, CasePackLoader
from sentinel.domain.errors import SentinelError
from sentinel.evidence.table import EvidenceLikelihoodTable
from sentinel.graph.repository import GraphRepository
from sentinel.graph.tigergraph import TigerGraphRestRepository
from sentinel.llm.client import LlmClient
from sentinel.memory.store import CaseMemoryStore
from sentinel.rag.embeddings import EmbeddingService
from sentinel.rag.retriever import GraphRagRetriever
from sentinel.runner import BatchReport, CaseRunner, new_run_id
from sentinel.validation.graph_check import GraphIdentityChecker
from sentinel.validation.validator import AnswerValidator

logger = logging.getLogger("sentinel")

EXIT_OK = 0
EXIT_INVALID = 1
EXIT_ERROR = 2


# ── argument parsing ─────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m sentinel",
        description="Sentinel — agentic fraud investigation on TigerGraph",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="investigate one case or the whole benchmark")
    target = run.add_mutually_exclusive_group(required=True)
    target.add_argument("--case", metavar="ID", help="one case id, e.g. HHG-003")
    target.add_argument("--all", action="store_true", help="every case in the pack")
    run.add_argument(
        "--no-write",
        action="store_true",
        help="skip the graph write-back; answers are still written and validated",
    )
    run.add_argument(
        "--no-graph-check",
        action="store_true",
        help="skip proving every id against the live graph (faster, weaker)",
    )
    run.add_argument(
        "--out",
        type=Path,
        metavar="DIR",
        help="write answers here instead of cases/ — use for a rehearsal run",
    )
    run.add_argument(
        "--no-retrieval",
        action="store_true",
        help="skip GraphRAG; `recall` falls back to the structural lookup",
    )
    run.add_argument("--run-id", metavar="ID", help="name this run; defaults to a timestamp")

    validate = sub.add_parser("validate", help="validate the answer files on disk")
    validate.add_argument(
        "--dir", type=Path, metavar="DIR", help="directory of answer files; defaults to cases/"
    )
    validate.add_argument(
        "--no-graph", action="store_true", help="shape and policy only; no network"
    )

    sub.add_parser("doctor", help="check the environment a run assumes")

    ingest = sub.add_parser("ingest", help="build the GraphRAG corpus in the graph")
    ingest.add_argument(
        "--what",
        choices=("policy", "cases", "all"),
        default="all",
        help="policy documents, closed-case embeddings, or both",
    )
    ingest.add_argument("--limit", type=int, default=0, help="stop after N closed cases (0 = all)")
    ingest.add_argument(
        "--force", action="store_true", help="re-embed rows whose fingerprint already matches"
    )

    explore = sub.add_parser(
        "explore", help="questions the twenty do not ask; output goes to exploration/"
    )
    explore.add_argument("what", choices=("rings",), help="which sweep to run")
    explore.add_argument(
        "--max-device-cards",
        type=int,
        default=None,
        help="profiles above this are browser configurations, not devices (default 20)",
    )

    return parser


# ── composition ──────────────────────────────────────────────────────────────


async def _build_orchestrator(
    settings: Settings,
    stack: AsyncExitStack,
    *,
    write_back: bool,
    retrieval: bool = True,
) -> tuple[SentinelOrchestrator, GraphRepository]:
    """The live stack: real graph, real model, real write-back."""
    repository = await stack.enter_async_context(TigerGraphRestRepository.from_settings(settings))
    llm = LlmClient(settings=settings)
    # One meter across the model and the embeddings, so `answer.tokens` counts
    # every token the case actually spent rather than only the chat ones.
    embeddings = EmbeddingService(settings=settings, meter=llm.meter)
    memory = CaseMemoryStore(graph=repository, embeddings=embeddings) if write_back else None
    retriever: GraphRagRetriever | None = None
    if retrieval:
        retriever = GraphRagRetriever(settings=settings, graph=repository, embeddings=embeddings)
        # Warmed once here, not lazily inside the first case: 5,611 vectors is
        # a few seconds, and charging it to case one would make that one case's
        # `latency_s` a lie about the agent.
        logger.info("warming the retriever…")
        await retriever.warm()
    orchestrator = SentinelOrchestrator(
        settings=settings,
        repository=repository,
        llm=llm,
        table=EvidenceLikelihoodTable(settings.elt_path),
        memory=memory,
        retriever=retriever,
    )
    return orchestrator, repository


def _select(alerts: Sequence[Alert], case_id: str | None) -> list[Alert]:
    if case_id is None:
        return list(alerts)
    chosen = [a for a in alerts if a.alert_id == case_id]
    if not chosen:
        known = ", ".join(a.alert_id for a in alerts)
        raise SystemExit(f"no case '{case_id}' in the pack. Known: {known}")
    return chosen


# ── commands ─────────────────────────────────────────────────────────────────


async def cmd_run(args: argparse.Namespace, settings: Settings) -> int:
    alerts = _select(CasePackLoader(settings.case_pack_path).load(), args.case)
    run_id = args.run_id or new_run_id()

    async with AsyncExitStack() as stack:
        orchestrator, repository = await _build_orchestrator(
            settings, stack, write_back=not args.no_write, retrieval=not args.no_retrieval
        )
        runner = CaseRunner(
            settings=settings,
            orchestrator=orchestrator,
            run_id=run_id,
            checker=None if args.no_graph_check else GraphIdentityChecker(repository),
            cases_dir=args.out,
        )
        report = await runner.run_batch(alerts)

    _print_report(report, runner.run_dir)
    return EXIT_OK if len(report.valid) == len(report.outcomes) else EXIT_INVALID


async def cmd_validate(args: argparse.Namespace, settings: Settings) -> int:
    directory = args.dir or settings.cases_dir
    paths = sorted(directory.glob("*.json"))
    if not paths:
        print(f"no answer files in {directory}")
        return EXIT_INVALID

    validator = AnswerValidator()
    failures = 0
    async with AsyncExitStack() as stack:
        checker: GraphIdentityChecker | None = None
        if not args.no_graph:
            repository = await stack.enter_async_context(
                TigerGraphRestRepository.from_settings(settings)
            )
            checker = GraphIdentityChecker(repository)

        for path in paths:
            payload = json.loads(path.read_text(encoding="utf-8"))
            report = validator.validate(payload)
            if checker is not None:
                await checker.check(payload, report)
            mark = "ok " if report.ok else "FAIL"
            print(f"{mark} {path.name}")
            for finding in report.errors:
                print(f"       error   {finding}")
            for finding in report.warnings:
                print(f"       warning {finding}")
            failures += 0 if report.ok else 1

    print(
        f"\n{len(paths) - failures}/{len(paths)} valid"
        + ("" if args.no_graph else " (with graph checks)")
    )
    return EXIT_OK if failures == 0 else EXIT_INVALID


async def cmd_doctor(_: argparse.Namespace, settings: Settings) -> int:
    """Check every assumption a run makes, and say which one is false."""
    lines: list[tuple[bool, str]] = []

    lines.append((settings.case_pack_path.exists(), f"case pack {settings.case_pack_path}"))
    lines.append((settings.elt_path.exists(), f"likelihood table {settings.elt_path}"))
    try:
        alerts = CasePackLoader(settings.case_pack_path).load()
        lines.append((len(alerts) == 20, f"{len(alerts)} alerts in the pack (expected 20)"))
    except (OSError, ValueError, KeyError) as exc:
        lines.append((False, f"case pack unreadable: {exc}"))

    lines.append((bool(settings.openai_model), f"model {settings.openai_model}"))
    lines.append(
        (
            bool(settings.openai_embedding_model),
            f"embeddings {settings.openai_embedding_model} @ {settings.openai_embedding_dimensions}d",
        )
    )

    async with AsyncExitStack() as stack:
        try:
            repository = await stack.enter_async_context(
                TigerGraphRestRepository.from_settings(settings)
            )
            counts = await repository.stat_vertex_counts()
            lines.append(
                (counts.get("Transaction", 0) > 0, f"graph reachable, {len(counts)} vertex types")
            )
            for vtype in ("Transaction", "Card", "ClosedCase", "Alert", "FraudCase", "PolicyDoc"):
                count = counts.get(vtype, 0)
                expected = vtype not in {"PolicyDoc"}
                lines.append((count > 0 or not expected, f"  {vtype:14} {count:>7}"))
            if counts.get("PolicyDoc", 0) == 0:
                lines.append((False, "  PolicyDoc is empty — run `python -m sentinel ingest`"))
        except SentinelError as exc:
            lines.append((False, f"graph unreachable: {exc.message}"))

    for ok, text in lines:
        print(f"{'✓' if ok else '✗'} {text}")
    failures = sum(1 for ok, _ in lines if not ok)
    print(f"\n{len(lines) - failures}/{len(lines)} checks passed")
    return EXIT_OK if failures == 0 else EXIT_INVALID


async def cmd_ingest(args: argparse.Namespace, settings: Settings) -> int:
    from sentinel.rag.corpus import CorpusBuilder

    async with AsyncExitStack() as stack:
        repository = await stack.enter_async_context(
            TigerGraphRestRepository.from_settings(settings)
        )
        builder = CorpusBuilder(
            settings=settings,
            graph=repository,
            embeddings=EmbeddingService(settings=settings),
        )
        if args.what in ("policy", "all"):
            report = await builder.ingest_policy(force=args.force)
            print(f"policy: {report}")
        if args.what in ("cases", "all"):
            report = await builder.ingest_closed_cases(limit=args.limit or None, force=args.force)
            print(f"closed cases: {report}")
    return EXIT_OK


async def cmd_explore(args: argparse.Namespace, settings: Settings) -> int:
    from sentinel.exploration.rings import MAX_DEVICE_CARDS, RingExplorer, write_report

    alerts = CasePackLoader(settings.case_pack_path).load()
    async with AsyncExitStack() as stack:
        repository = await stack.enter_async_context(
            TigerGraphRestRepository.from_settings(settings)
        )
        explorer = RingExplorer(
            graph=repository,
            max_device_cards=args.max_device_cards or MAX_DEVICE_CARDS,
        )
        report = await explorer.explore(alerts)

    json_path, md_path = write_report(report, settings.exploration_dir, datetime.now())
    print(report.as_markdown())
    print(f"\nwritten to {json_path} and {md_path}")
    return EXIT_OK


# ── output ───────────────────────────────────────────────────────────────────


def _print_report(report: BatchReport, run_dir: Path) -> None:
    print()
    print(
        f"{'case':10} {'verdict':11} {'p':>7} {'pattern':28} {'sar':4} {'calls':>5} {'tokens':>7}  actions"
    )
    print("─" * 118)
    for outcome in report.outcomes:
        answer = outcome.answer
        if answer is None:
            print(f"{outcome.case_id:10} {'FAILED':11} {'':>7} {outcome.error[:28]:28}")
            continue
        print(
            f"{outcome.case_id:10} "
            f"{answer.case.verdict.value:11} "
            f"{answer.case.fraud_probability:7.4f} "
            f"{answer.case.pattern.value:28} "
            f"{'yes' if answer.sar.file else '·':4} "
            f"{answer.tool_calls:5} "
            f"{answer.tokens:7}  "
            + ", ".join(r.action.value for r in answer.next_best_actions.final)
        )
    print("─" * 118)
    summary = report.as_dict()
    print(
        f"{summary['valid']}/{summary['cases']} valid · "
        f"block {summary['block_rate']:.0%} · SAR {summary['sar_rate']:.0%} · "
        f"changed {summary['changed_rate']:.0%} · "
        f"{summary['tokens']} tokens · {summary['elapsed_s']}s"
    )
    print(f"verdicts: {summary['verdict_mix']}")
    for warning in report.warnings():
        print(f"  ⚠ {warning}")
    print(f"trace and report: {run_dir}")


# ── entry point ──────────────────────────────────────────────────────────────


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )
    # httpx logs every request at INFO, which drowns a 20-case run.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)

    commands = {
        "run": cmd_run,
        "validate": cmd_validate,
        "doctor": cmd_doctor,
        "ingest": cmd_ingest,
        "explore": cmd_explore,
    }
    try:
        settings = get_settings()
    except Exception as exc:  # noqa: BLE001 - pydantic-settings raises several
        # A configuration problem is the single most common way a run fails, and
        # a traceback is the least useful way to say so.
        print(f"configuration is incomplete: {exc}", file=sys.stderr)
        return EXIT_ERROR

    try:
        return asyncio.run(commands[args.command](args, settings))
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return EXIT_ERROR
    except SentinelError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
