import re
import shutil
from pathlib import Path
from typing import Any, Callable

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agent.config import REPO_ROOT, Settings
from agent.graph import build_graph
from agent.nodes import Deps
from agent.project import SANDBOX_IGNORE

DATA_DIR = REPO_ROOT / "data"


class FakeLLM:
    """Stands in for ChatAnthropic. Handlers are keyed by the structured-output schema name.

    A handler is a callable (messages -> dict) or a list of dicts consumed in order.
    """

    def __init__(self, **handlers: Callable[[list], dict] | list[dict]):
        self.handlers = handlers
        self.calls: list[tuple[str, list]] = []

    def with_structured_output(self, schema):
        llm = self

        class Structured:
            def invoke(self, messages, config=None):
                name = schema.__name__
                llm.calls.append((name, messages))
                handler = llm.handlers[name]
                result = handler.pop(0) if isinstance(handler, list) else handler(messages)
                return schema.model_validate(result)

        return Structured()

    def count(self, schema_name: str) -> int:
        return sum(1 for name, _ in self.calls if name == schema_name)


def current_file(messages: list, path: str) -> str:
    """Pull one file's content out of the "Current files" section of a prompt."""
    text = messages[-1].content
    match = re.search(rf"--- {re.escape(path)} ---\n(.*?)(?=\n\n--- |\Z)", text, re.DOTALL)
    assert match, f"{path} not in prompt"
    return match.group(1)


def col(source, name, data_type, tests=(), description=None, **extra) -> dict:
    return {
        "source_column": source,
        "name": name,
        "data_type": data_type,
        "description": description or f"The {name.replace('_', ' ')}.",
        "tests": list(tests),
        **extra,
    }


ORDERS_PLAN = {
    "model_description": "One row per order, loaded daily from orders.csv.",
    "primary_key": "order_id",
    "columns": [
        col("order_id", "order_id", "integer", ["unique", "not_null"]),
        col("customer_id", "customer_id", "integer", ["not_null", "relationships"],
            relationship_model="dim_customers", relationship_field="customer_id"),
        col("order_date", "order_date", "date", ["not_null"]),
        col("status", "status", "varchar", ["accepted_values"],
            accepted_values=["completed", "placed", "returned", "shipped"]),
        col("amount", "amount", "decimal(12,2)", ["not_null"]),
    ],
}

CUSTOMERS_PLAN = {
    "model_description": "One row per customer from the CRM export.",
    "primary_key": "customer_id",
    "columns": [
        col("Customer ID", "customer_id", "integer", ["unique", "not_null"]),
        col("First Name", "first_name", "varchar"),
        col("Last Name", "last_name", "varchar"),
        col("E-mail Address", "email", "varchar", ["not_null"]),
        col("SignupDate", "signup_date", "date", ["not_null"]),
        col("Country", "country_code", "varchar", ["accepted_values"], accepted_values=["DE", "FR", "GB", "MA", "US"]),
        col("Is Active", "is_active", "boolean", ["not_null"]),
    ],
}

PAYMENTS_PLAN = {
    "model_description": "One row per payment.",
    "primary_key": "payment_id",
    "columns": [
        col("payment_id", "payment_id", "integer", ["unique", "not_null"]),
        col("order_id", "order_id", "integer", ["not_null"]),
        col("payment_method", "payment_method", "varchar", ["accepted_values"],
            accepted_values=["bank_transfer", "card", "gift_card", "paypal"]),
        col("amount", "amount", "decimal(12,2)", ["not_null"]),
        col("paid_at", "paid_at", "timestamp", ["not_null"]),
    ],
}


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    project = tmp_path / "dbt_project"
    shutil.copytree(REPO_ROOT / "dbt_project", project, ignore=SANDBOX_IGNORE)
    return Settings(project_dir=project, sandbox_root=tmp_path / "sandbox", data_dir=DATA_DIR)


@pytest.fixture
def make_graph(settings: Settings):
    """Build a graph with fakes; returns (graph, deps). Pass checkpointer= to share one across graphs."""

    def factory(llm: FakeLLM, dbt_runner=None, checkpointer=None, **settings_overrides: Any):
        s = Settings(**{**settings.__dict__, **settings_overrides}) if settings_overrides else settings
        deps = Deps(settings=s, llm_factory=lambda: llm)
        if dbt_runner is not None:
            deps.dbt_runner = dbt_runner
        return build_graph(deps, checkpointer or InMemorySaver()), deps

    return factory


def config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": 100}


def pending_review(graph, thread_id: str) -> dict | None:
    snapshot = graph.get_state(config(thread_id))
    interrupts = [i for task in snapshot.tasks for i in task.interrupts]
    return interrupts[0].value if interrupts else None


def build_input(csv_name: str, description: str) -> dict:
    return {
        "file_path": str(DATA_DIR / csv_name),
        "source_description": description,
        "source_name": Path(csv_name).stem,
    }


class ScriptedDbt:
    """Fake dbt runner: returns canned outputs in order (the last one repeats)."""

    def __init__(self, *outputs: dict):
        self.outputs = list(outputs)
        self.calls = 0

    def __call__(self, project_dir, model, data_dir):
        self.calls += 1
        return self.outputs[min(self.calls, len(self.outputs)) - 1]


def dbt_pass() -> dict:
    result = {"unique_id": "model.pipeline.stg_orders", "name": "stg_orders", "resource_type": "model",
              "status": "success", "message": "OK", "failures": None, "test_name": None, "column": None}
    return {"success": True, "command": "dbt build --select stg_orders", "summary": "PASS=1 WARN=0 ERROR=0",
            "results": [result], "failures": [], "log": "Completed successfully"}


def dbt_model_error(message: str = "Binder Error: column not found") -> dict:
    result = {"unique_id": "model.pipeline.stg_orders", "name": "stg_orders", "resource_type": "model",
              "status": "error", "message": message, "failures": None, "test_name": None, "column": None}
    return {"success": False, "command": "dbt build --select stg_orders", "summary": "PASS=0 WARN=0 ERROR=1",
            "results": [result], "failures": [result], "log": message}
