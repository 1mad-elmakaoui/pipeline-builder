"""End-to-end on orders.csv with the real Claude model. Skipped without ANTHROPIC_API_KEY."""

import os

import pytest
from dotenv import load_dotenv
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agent.config import Settings
from agent.graph import build_graph
from agent.nodes import Deps
from tests.conftest import build_input, config, pending_review

load_dotenv()

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY not set"),
]


def test_orders_end_to_end_passes_dbt_build(settings: Settings):
    graph = build_graph(Deps(settings=settings), InMemorySaver())
    graph.invoke(build_input("orders.csv", "orders, loaded daily, one row per order"), config("e2e-orders"))

    review = pending_review(graph, "e2e-orders")
    assert review["status"] == "passed", review
    assert any(r["status"] == "pass" for r in review["test_results"])

    graph.invoke(Command(resume="approve"), config("e2e-orders"))
    assert (settings.project_dir / "models/staging/stg_orders.sql").exists()
