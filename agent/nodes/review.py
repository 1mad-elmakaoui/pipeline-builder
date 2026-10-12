"""human_review: pause the graph and wait for approve / reject / feedback."""

from typing import Any

from langgraph.config import get_config
from langgraph.types import interrupt

from agent.nodes import Deps
from agent.project import unified_diff
from agent.state import PipelineState


def review_payload(state: PipelineState, deps: Deps) -> dict[str, Any]:
    output = state.get("dbt_output") or {}
    return {
        "thread_id": get_config()["configurable"]["thread_id"],
        "source": state["source_name"],
        "status": state["status"],
        "iterations": state.get("iteration", 0),
        "dbt_summary": output.get("summary"),
        "test_results": [
            {"node": r["name"], "type": r["resource_type"], "status": r["status"], "message": r.get("message")}
            for r in output.get("results", [])
        ],
        "fix_history": [
            {"iteration": e.get("iteration"), **e["fix"]} if "fix" in e else {k: e[k] for k in ("kind", "explanation") if k in e}
            for e in state.get("error_history", [])
            if "fix" in e or e.get("kind") == "feedback"
        ],
        "data_issues": state.get("data_issues", []),
        "diff": unified_diff(deps.settings.project_dir, state["files"]),
    }


def parse_decision(answer: Any) -> dict[str, str]:
    """Accept {"action": ..., "feedback": ...} or a bare string ("approve", "reject", anything else = feedback)."""
    if isinstance(answer, dict):
        action = str(answer.get("action", "")).lower()
        feedback = str(answer.get("feedback") or "").strip()
    else:
        text = str(answer).strip()
        action, feedback = text.lower(), text
    if action in ("approve", "approved", "a", "yes", "y"):
        return {"action": "approve"}
    if action in ("reject", "rejected", "r", "no", "n"):
        return {"action": "reject"}
    if not feedback:
        raise ValueError(f"feedback is empty; got {answer!r}")
    return {"action": "feedback", "feedback": feedback}


def human_review(state: PipelineState, deps: Deps) -> dict:
    decision = parse_decision(interrupt(review_payload(state, deps)))
    if decision["action"] == "approve":
        return {"review": decision, "status": "approved"}
    if decision["action"] == "reject":
        return {"review": decision, "status": "rejected"}
    return {"review": decision, "status": "revising", "feedback": decision["feedback"]}
