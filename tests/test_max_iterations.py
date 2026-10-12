"""The fix loop gives up after MAX_ITERATIONS builds and hands over to a human."""

from agent.config import MAX_ITERATIONS
from tests.conftest import ORDERS_PLAN, FakeLLM, ScriptedDbt, build_input, config, current_file, dbt_model_error, pending_review

SQL = "models/staging/stg_orders.sql"


def test_loop_stops_at_max_iterations(make_graph):
    attempts = []

    def useless_fix(messages):
        attempts.append(1)
        sql = current_file(messages, SQL) + f"-- attempt {len(attempts)}\n"
        return {"kind": "code_fix", "file": SQL, "content": sql, "explanation": f"attempt {len(attempts)}"}

    dbt = ScriptedDbt(dbt_model_error("Binder Error: something the fixer cannot fix"))
    llm = FakeLLM(ModelPlan=[ORDERS_PLAN], FixDecision=useless_fix)
    graph, _ = make_graph(llm, dbt)
    graph.invoke(build_input("orders.csv", "orders"), config("max"))

    state = graph.get_state(config("max")).values
    assert dbt.calls == MAX_ITERATIONS == 5
    assert llm.count("FixDecision") == MAX_ITERATIONS - 1
    assert state["iteration"] == MAX_ITERATIONS
    assert state["status"] == "needs_help"

    review = pending_review(graph, "max")
    assert review["status"] == "needs_help"
    assert len(review["fix_history"]) == MAX_ITERATIONS - 1


def test_max_iterations_is_configurable(make_graph):
    dbt = ScriptedDbt(dbt_model_error())
    llm = FakeLLM(ModelPlan=[ORDERS_PLAN], FixDecision=lambda m: {"kind": "code_fix", "file": SQL,
                                                                    "content": current_file(m, SQL), "explanation": "x"})
    graph, _ = make_graph(llm, dbt, max_iterations=2)
    graph.invoke(build_input("orders.csv", "orders"), config("max2"))
    assert dbt.calls == 2
    assert pending_review(graph, "max2")["status"] == "needs_help"
