"""The LangGraph wiring: profile -> plan -> generate -> run dbt <-> fix -> human review -> apply."""

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agent.nodes import Deps
from agent.nodes.apply import apply_changes
from agent.nodes.fix import diagnose_and_fix
from agent.nodes.generate import generate_files
from agent.nodes.plan import plan_model
from agent.nodes.profile import profile_source
from agent.nodes.review import human_review
from agent.nodes.run_dbt import run_dbt
from agent.state import PipelineState


# Routers return edge labels; the path maps below turn them into node names (and Mermaid labels).


def route_after_run(state: PipelineState) -> str:
    # A green build and hitting the iteration cap (status needs_help) both go to a human.
    return "passed or max iterations" if state["status"] in ("passed", "needs_help") else "failed"


def route_after_fix(state: PipelineState) -> str:
    return "data_issue" if state["status"] == "data_issue" else "fixed"


def route_after_review(state: PipelineState) -> str:
    return {"approved": "approve", "revising": "feedback"}.get(state["status"], "reject")


def build_graph(deps: Deps | None = None, checkpointer: BaseCheckpointSaver | None = None) -> CompiledStateGraph:
    deps = deps or Deps()

    def bind(fn):
        def node(state: PipelineState) -> dict:
            return fn(state, deps)

        node.__name__ = fn.__name__
        return node

    builder = StateGraph(PipelineState)
    for fn in (profile_source, plan_model, generate_files, run_dbt, diagnose_and_fix, human_review, apply_changes):
        builder.add_node(fn.__name__, bind(fn))

    builder.add_edge(START, "profile_source")
    builder.add_edge("profile_source", "plan_model")
    builder.add_edge("plan_model", "generate_files")
    builder.add_edge("generate_files", "run_dbt")
    builder.add_conditional_edges(
        "run_dbt",
        route_after_run,
        {"passed or max iterations": "human_review", "failed": "diagnose_and_fix"},
    )
    builder.add_conditional_edges(
        "diagnose_and_fix", route_after_fix, {"fixed": "run_dbt", "data_issue": "human_review"}
    )
    builder.add_conditional_edges(
        "human_review",
        route_after_review,
        {"approve": "apply_changes", "feedback": "generate_files", "reject": END},
    )
    builder.add_edge("apply_changes", END)
    return builder.compile(checkpointer=checkpointer)


def mermaid() -> str:
    return build_graph().get_graph().draw_mermaid()
