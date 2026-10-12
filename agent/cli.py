"""Command line entry point.

    python -m agent build data/orders.csv "orders, loaded daily, one row per order"
    python -m agent resume <thread_id>
    python -m agent graph
"""

import argparse
import os
import re
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from dotenv import load_dotenv
from langgraph.types import Command

from agent import tracing
from agent.config import postgres_uri
from agent.graph import build_graph, mermaid
from agent.nodes import Deps
from agent.project import to_snake

RULE = "=" * 78


@contextmanager
def open_checkpointer() -> Iterator[Any]:
    import psycopg
    from langgraph.checkpoint.postgres import PostgresSaver

    try:
        with PostgresSaver.from_conn_string(postgres_uri()) as saver:
            saver.setup()
            yield saver
    except psycopg.OperationalError as exc:
        raise SystemExit(f"Cannot reach Postgres at {postgres_uri()} ({exc}). Start it with `make up`.") from exc


def run_config(thread_id: str) -> dict:
    return {
        "configurable": {"thread_id": thread_id},
        "callbacks": tracing.callbacks(),
        "metadata": {"langfuse_session_id": thread_id, "langfuse_tags": ["dbt-pipeline-builder"]},
        "recursion_limit": 100,
    }


def _headline(text: str | None, limit: int = 160) -> str:
    """The most useful line of a dbt message: the database error if there is one, else the first line."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    errors = [ln for ln in lines if re.match(r"^[A-Z][A-Za-z ]*Error:", ln)]
    line = (errors or lines or [""])[0]
    return line if len(line) <= limit else line[: limit - 3] + "..."


def describe_update(node: str, update: dict) -> list[str]:
    if node == "profile_source":
        p = update["profile"]
        return [f"profiled {p['file']}: {p['row_count']} rows, {len(p['columns'])} columns, "
                f"candidate primary key: {p['candidate_primary_key'] or 'none'}"]
    if node == "plan_model":
        plan = update["plan"]
        n_tests = sum(len(c["tests"]) for c in plan["columns"])
        return [f"planned {len(plan['columns'])} columns, primary key {plan['primary_key']}, {n_tests} tests"]
    if node == "generate_files":
        lines = [f"wrote {path}" for path in update["files"]]
        history = update.get("error_history") or []
        if history and history[-1].get("kind") == "feedback":
            lines.insert(0, f"revised after feedback: {history[-1]['explanation']}")
        return lines
    if node == "run_dbt":
        out = update["dbt_output"]
        mark = "PASS" if out["success"] else "FAIL"
        lines = [f"[{mark}] iteration {update['iteration']}: {out['command']}  ({out['summary']})"]
        for f in out["failures"]:
            lines.append(f"    {f['status'].upper()} {f.get('name', f['resource_type'])}: {_headline(f.get('message'))}")
        if update.get("status") == "needs_help":
            lines.append("    reached the iteration limit, handing over to a human")
        return lines
    if node == "diagnose_and_fix":
        fix = update["error_history"][-1]["fix"]
        if fix["kind"] == "data_issue":
            return [f"DATA ISSUE: {fix['explanation']}"]
        return [f"fix ({fix['file'] or 'no file changed'}): {fix['explanation']}"]
    if node == "human_review":
        return [f"review decision: {update['review']['action']}"]
    if node == "apply_changes":
        return [f"applied {os.path.relpath(path)}" for path in update["applied_files"]]
    return []


def print_review(review: dict) -> None:
    print(RULE)
    print(f"REVIEW  thread={review['thread_id']}  source={review['source']}  status={review['status'].upper()}")
    print(f"dbt: {review['dbt_summary']}  (after {review['iterations']} build(s))")
    print(RULE)
    if review["data_issues"]:
        print("DATA ISSUES (tests kept; the source data needs attention):")
        for issue in review["data_issues"]:
            print(f"  ! {issue}")
        print()
    if review["fix_history"]:
        print("Fix history:")
        for fix in review["fix_history"]:
            where = f" [{fix['file']}]" if fix.get("file") else ""
            print(f"  - iteration {fix.get('iteration', '-')}: {fix['kind']}{where}: {fix['explanation']}")
        print()
    if review["test_results"]:
        print("Test results:")
        for r in review["test_results"]:
            print(f"  {r['status'].upper():<8} {r['type']:<6} {r['node']}")
        print()
    print("Diff against the dbt project:")
    print(review["diff"] or "  (no changes)")
    print(RULE)


def ask_decision() -> Any:
    while True:
        answer = input("approve / reject / or type feedback for another round: ").strip()
        if answer:
            return answer


def drive(graph, payload: Any, thread_id: str, decision: str | None) -> int:
    config = run_config(thread_id)
    try:
        while True:
            for chunk in graph.stream(payload, config, stream_mode="updates"):
                for node, update in chunk.items():
                    if node == "__interrupt__" or not update:
                        continue
                    for line in describe_update(node, update):
                        print(f"[{node}] {line}", flush=True)

            snapshot = graph.get_state(config)
            if not snapshot.next:
                values = snapshot.values
                print(f"run {thread_id} finished with status {values.get('status', 'unknown').upper()}")
                return 0
            interrupts = [i for task in snapshot.tasks for i in task.interrupts]
            if not interrupts:
                payload = None  # paused between nodes (e.g. after a crash): continue from the checkpoint
                continue

            print_review(interrupts[0].value)
            if decision is not None:
                answer, decision = decision, None
            elif sys.stdin.isatty():
                answer = ask_decision()
            else:
                print(f"No terminal to ask for a review. Resume later with:\n  python -m agent resume {thread_id}")
                return 0
            payload = Command(resume=answer)
    finally:
        tracing.flush()


def cmd_build(args: argparse.Namespace) -> int:
    csv_path = Path(args.csv)
    if not csv_path.is_file():
        raise SystemExit(f"{csv_path} does not exist")
    name = to_snake(args.name or csv_path.stem)
    thread_id = args.thread_id or f"{name}-{uuid.uuid4().hex[:8]}"
    print(f"thread_id: {thread_id}")
    initial = {"file_path": str(csv_path.resolve()), "source_description": args.description, "source_name": name}
    with open_checkpointer() as checkpointer:
        return drive(build_graph(Deps(), checkpointer), initial, thread_id, args.decision)


def cmd_resume(args: argparse.Namespace) -> int:
    with open_checkpointer() as checkpointer:
        graph = build_graph(Deps(), checkpointer)
        snapshot = graph.get_state({"configurable": {"thread_id": args.thread_id}})
        if not snapshot.values:
            raise SystemExit(f"no run found for thread {args.thread_id}")
        if not snapshot.next:
            print(f"run {args.thread_id} already finished with status {snapshot.values.get('status', '').upper()}")
            return 0
        interrupted = any(task.interrupts for task in snapshot.tasks)
        if interrupted:
            # Re-show the pending review; drive() streams nothing new and goes straight to the prompt.
            print_review(next(i for t in snapshot.tasks for i in t.interrupts).value)
            answer = args.decision if args.decision is not None else (ask_decision() if sys.stdin.isatty() else None)
            if answer is None:
                print("No decision given. Pass --decision approve|reject|'<feedback>'.")
                return 1
            return drive(graph, Command(resume=answer), args.thread_id, None)
        return drive(graph, None, args.thread_id, args.decision)


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="python -m agent", description="dbt pipeline builder agent")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="profile a CSV and build a tested staging model for it")
    build.add_argument("csv")
    build.add_argument("description", help='plain-language description, e.g. "orders, loaded daily, one row per order"')
    build.add_argument("--name", help="source name (default: CSV file name)")
    build.add_argument("--thread-id", help="checkpoint thread id (default: <name>-<random>)")
    build.add_argument("--decision", help="answer the review non-interactively: approve, reject, or feedback text")
    build.set_defaults(func=cmd_build)

    resume = sub.add_parser("resume", help="resume a paused run from its Postgres checkpoint")
    resume.add_argument("thread_id")
    resume.add_argument("--decision", help="approve, reject, or feedback text")
    resume.set_defaults(func=cmd_resume)

    graph = sub.add_parser("graph", help="print the graph as Mermaid")
    graph.set_defaults(func=lambda _: print(mermaid()) or 0)

    args = parser.parse_args(argv)
    return args.func(args)
