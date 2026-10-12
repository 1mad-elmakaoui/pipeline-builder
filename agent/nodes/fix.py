"""diagnose_and_fix: the LLM reads the error and edits one file, or flags a data issue."""

import json
import re

from agent.llm import ask
from agent.nodes import Deps
from agent.project import column_tests, model_name
from agent.prompts import FIX_SYSTEM, FIX_USER, render_files
from agent.schemas import FixDecision
from agent.state import PipelineState

WEAKENING_KEYS = {"severity", "where", "error_if", "warn_if", "limit", "enabled"}
ROW_FILTERS = re.compile(r"\b(distinct|row_number|qualify|group\s+by|where|limit)\b", re.IGNORECASE)


def data_test_failures(dbt_output: dict) -> list[dict]:
    """Tests that ran and found offending rows (status "fail"), as opposed to tests that errored."""
    return [f for f in dbt_output.get("failures", []) if f.get("resource_type") == "test" and f["status"] == "fail"]


def _mentions_weakening(entry) -> str | None:
    if not isinstance(entry, dict):
        return None
    stack = [entry]
    while stack:
        node = stack.pop()
        for key, value in node.items():
            if key in WEAKENING_KEYS:
                return key
            if isinstance(value, dict):
                stack.append(value)
    return None


def guard_against_hiding(files: dict[str, str], path: str, new_content: str, dbt_output: dict) -> str | None:
    """Return why an edit is refused if it makes a failing data test pass by hiding the bad data."""
    failing = data_test_failures(dbt_output)
    if not failing:
        return None
    old_content = files[path]
    if path.endswith(".yml"):
        old_tests, new_tests = column_tests(old_content), column_tests(new_content)
        for f in failing:
            key = (f.get("column"), f.get("test_name"))
            if key not in old_tests:
                continue
            if key not in new_tests:
                return f"refused edit that removes failing test {f['test_name']} on {f['column']}"
            weakened = _mentions_weakening(new_tests[key])
            if weakened and weakened != _mentions_weakening(old_tests[key]):
                return f"refused edit that weakens failing test {f['test_name']} on {f['column']} with '{weakened}'"
    if path.endswith(".sql"):
        before = {m.lower() for m in ROW_FILTERS.findall(old_content)}
        added = {m.lower() for m in ROW_FILTERS.findall(new_content)} - before
        if added:
            return f"refused SQL edit that filters or deduplicates rows ({', '.join(sorted(added))}) to pass a failing data test"
    return None


def _resolve_path(files: dict[str, str], path: str | None) -> str | None:
    if not path:
        return None
    path = path.strip().lstrip("./")
    if path in files:
        return path
    matches = [p for p in files if p.endswith("/" + path.split("/")[-1])]
    return matches[0] if len(matches) == 1 else None


def _describe_data_issue(explanation: str, dbt_output: dict) -> list[str]:
    issues = [explanation]
    for f in data_test_failures(dbt_output):
        issues.append(f"{f['name']}: {f.get('message') or 'failed'}")
    return issues


def diagnose_and_fix(state: PipelineState, deps: Deps) -> dict:
    output = state["dbt_output"]
    files = dict(state["files"])
    decision = ask(
        deps.llm,
        FixDecision,
        FIX_SYSTEM,
        FIX_USER.format(
            description=state["source_description"],
            model=model_name(state["source_name"]),
            command=output["command"],
            summary=output["summary"],
            failures=json.dumps(output["failures"], indent=2, default=str),
            log=output["log"],
            history=json.dumps(state.get("error_history", [])[:-1], indent=2, default=str),
            profile=json.dumps(state["profile"], indent=2, default=str),
            files=render_files(files),
        ),
    )

    kind, explanation, edited = decision.kind, decision.explanation.strip(), None
    if kind == "code_fix":
        path = _resolve_path(files, decision.file)
        if path is None or not decision.content:
            explanation = f"no change: fix named an unknown file ({decision.file!r}). {explanation}"
        elif refusal := guard_against_hiding(files, path, decision.content, output):
            kind, explanation = "data_issue", f"{refusal}; flagging as a data issue instead. LLM said: {explanation}"
        else:
            files[path] = decision.content.rstrip() + "\n"
            edited = path

    history = list(state["error_history"])
    history[-1] = {**history[-1], "fix": {"kind": kind, "file": edited, "explanation": explanation}}
    update = {"error_history": history, "files": files}
    if kind == "data_issue":
        update |= {"status": "data_issue", "data_issues": _describe_data_issue(explanation, output)}
    return update
