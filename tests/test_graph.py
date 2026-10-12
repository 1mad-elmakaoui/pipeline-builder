"""The graph compiles and routes correctly (LLM and dbt mocked)."""

from langgraph.types import Command

from agent.graph import route_after_fix, route_after_review, route_after_run
from tests.conftest import (
    ORDERS_PLAN, FakeLLM, ScriptedDbt, build_input, config, current_file, dbt_model_error, dbt_pass, pending_review,
)

INPUT = build_input("orders.csv", "orders, loaded daily, one row per order")
SQL = "models/staging/stg_orders.sql"


def node_sequence(graph, payload, thread_id):
    seen = []
    for chunk in graph.stream(payload, config(thread_id), stream_mode="updates"):
        seen.extend(node for node in chunk if node != "__interrupt__")
    return seen


def test_graph_compiles_with_expected_nodes(make_graph):
    graph, _ = make_graph(FakeLLM())
    nodes = set(graph.get_graph().nodes)
    assert {"profile_source", "plan_model", "generate_files", "run_dbt", "diagnose_and_fix",
            "human_review", "apply_changes"} <= nodes


def test_routers():
    assert route_after_run({"status": "passed"}) == "passed or max iterations"
    assert route_after_run({"status": "needs_help"}) == "passed or max iterations"
    assert route_after_run({"status": "running"}) == "failed"
    assert route_after_fix({"status": "data_issue"}) == "data_issue"
    assert route_after_fix({"status": "running"}) == "fixed"
    assert route_after_review({"status": "approved"}) == "approve"
    assert route_after_review({"status": "revising"}) == "feedback"
    assert route_after_review({"status": "rejected"}) == "reject"


def test_green_build_goes_to_review_then_apply(make_graph, settings):
    graph, _ = make_graph(FakeLLM(ModelPlan=[ORDERS_PLAN]), ScriptedDbt(dbt_pass()))
    seen = node_sequence(graph, INPUT, "t1")
    assert seen == ["profile_source", "plan_model", "generate_files", "run_dbt"]

    review = pending_review(graph, "t1")
    assert review["status"] == "passed"
    assert "+++ b/models/staging/stg_orders.sql" in review["diff"]
    assert not (settings.project_dir / SQL).exists()

    assert node_sequence(graph, Command(resume="approve"), "t1") == ["human_review", "apply_changes"]
    assert (settings.project_dir / SQL).exists()
    assert graph.get_state(config("t1")).values["status"] == "applied"


def test_failure_goes_through_fix_and_back_to_dbt(make_graph):
    def fix(messages):
        sql = current_file(messages, SQL)
        return {"kind": "code_fix", "file": SQL, "content": sql.replace("renamed", "final"),
                "explanation": "renamed the CTE"}

    dbt = ScriptedDbt(dbt_model_error(), dbt_pass())
    graph, _ = make_graph(FakeLLM(ModelPlan=[ORDERS_PLAN], FixDecision=fix), dbt)
    seen = node_sequence(graph, INPUT, "t2")
    assert seen == ["profile_source", "plan_model", "generate_files", "run_dbt", "diagnose_and_fix", "run_dbt"]

    state = graph.get_state(config("t2")).values
    assert state["status"] == "passed"
    assert "final as (" in state["files"][SQL]
    assert state["error_history"][0]["fix"] == {"kind": "code_fix", "file": SQL, "explanation": "renamed the CTE"}
    assert pending_review(graph, "t2")["fix_history"][0]["explanation"] == "renamed the CTE"


def test_reject_ends_without_writing(make_graph, settings):
    graph, _ = make_graph(FakeLLM(ModelPlan=[ORDERS_PLAN]), ScriptedDbt(dbt_pass()))
    node_sequence(graph, INPUT, "t3")
    assert node_sequence(graph, Command(resume={"action": "reject"}), "t3") == ["human_review"]
    snapshot = graph.get_state(config("t3"))
    assert snapshot.next == ()
    assert snapshot.values["status"] == "rejected"
    assert not (settings.project_dir / SQL).exists()


def test_feedback_starts_another_round(make_graph):
    def revise(messages):
        assert "call it order_total" in messages[-1].content
        sql = current_file(messages, SQL).replace("as amount", "as order_total")
        return {"edits": [{"path": SQL, "content": sql}], "summary": "renamed amount to order_total"}

    llm = FakeLLM(ModelPlan=[ORDERS_PLAN], Revision=revise)
    graph, _ = make_graph(llm, ScriptedDbt(dbt_pass()))
    node_sequence(graph, INPUT, "t4")

    seen = node_sequence(graph, Command(resume="call it order_total"), "t4")
    assert seen == ["human_review", "generate_files", "run_dbt"]
    review = pending_review(graph, "t4")
    assert review["status"] == "passed"
    assert "as order_total" in graph.get_state(config("t4")).values["files"][SQL]
    assert review["fix_history"][-1] == {"kind": "feedback", "explanation": "renamed amount to order_total"}
