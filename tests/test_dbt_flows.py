"""Real dbt + DuckDB runs with a scripted LLM: the clean source passes, the messy one self-corrects."""

import subprocess

from langgraph.types import Command

from agent.dbt_runner import _dbt_executable
from tests.conftest import (
    CUSTOMERS_PLAN, DATA_DIR, ORDERS_PLAN, FakeLLM, build_input, config, current_file, pending_review,
)

CUSTOMERS_SQL = "models/staging/stg_customers.sql"
FIXED_DATE = """coalesce(try_strptime("SignupDate", '%Y-%m-%d'), try_strptime("SignupDate", '%m/%d/%Y'))::date"""


def test_orders_passes_first_time_and_applies(make_graph, settings):
    graph, _ = make_graph(FakeLLM(ModelPlan=[ORDERS_PLAN]))
    graph.invoke(build_input("orders.csv", "orders, loaded daily, one row per order"), config("orders"))

    review = pending_review(graph, "orders")
    assert review["status"] == "passed", review
    assert review["iterations"] == 1
    tests = {r["node"] for r in review["test_results"] if r["type"] == "test"}
    assert "relationships_stg_orders_customer_id__customer_id__ref_dim_customers_" in tests
    assert all(r["status"] in ("success", "pass") for r in review["test_results"])

    graph.invoke(Command(resume="approve"), config("orders"))
    # The applied files work in the real project too.
    proc = subprocess.run(
        [_dbt_executable(), "build", "--project-dir", ".", "--profiles-dir", ".", "--no-use-colors"],
        cwd=settings.project_dir, capture_output=True, text=True, env={"PIPELINE_DATA_DIR": str(DATA_DIR),
                                                                       "PATH": "/usr/bin:/bin"},
    )
    assert proc.returncode == 0, proc.stdout[-3000:]


def test_customers_self_corrects_mixed_dates(make_graph):
    def fix(messages):
        prompt = messages[-1].content
        assert 'Conversion Error: invalid date field format: "05/14/2024"' in prompt
        sql = current_file(messages, CUSTOMERS_SQL).replace('cast("SignupDate" as date)', FIXED_DATE)
        return {"kind": "code_fix", "file": CUSTOMERS_SQL, "content": sql,
                "explanation": "SignupDate mixes YYYY-MM-DD and MM/DD/YYYY; parse both explicitly"}

    llm = FakeLLM(ModelPlan=[CUSTOMERS_PLAN], FixDecision=fix)
    graph, _ = make_graph(llm)
    graph.invoke(build_input("customers.csv", "customers from the CRM, one row per customer"), config("cust"))

    review = pending_review(graph, "cust")
    assert review["status"] == "passed", review
    assert review["iterations"] == 2
    assert llm.count("FixDecision") == 1
    assert review["fix_history"][0]["file"] == CUSTOMERS_SQL
    assert '"Customer ID"' in graph.get_state(config("cust")).values["files"][CUSTOMERS_SQL]
