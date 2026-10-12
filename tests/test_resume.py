"""A paused run resumes from its checkpoint in a fresh graph (a new process, in real life)."""

import os
from contextlib import contextmanager, nullcontext

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agent.config import postgres_uri
from tests.conftest import ORDERS_PLAN, FakeLLM, ScriptedDbt, build_input, config, dbt_pass, pending_review

SQL = "models/staging/stg_orders.sql"
INPUT = build_input("orders.csv", "orders, loaded daily, one row per order")


def run_then_resume(make_graph, settings, saver_factory, thread_id):
    # First "process": run until the human review interrupt, then throw the graph away.
    with saver_factory() as saver:
        graph, _ = make_graph(FakeLLM(ModelPlan=[ORDERS_PLAN]), ScriptedDbt(dbt_pass()), checkpointer=saver)
        graph.invoke(INPUT, config(thread_id))
        assert pending_review(graph, thread_id)["status"] == "passed"
    assert not (settings.project_dir / SQL).exists()

    # Second "process": brand-new graph and LLM that must not be called again.
    with saver_factory() as saver:
        graph, _ = make_graph(FakeLLM(), ScriptedDbt(), checkpointer=saver)
        snapshot = graph.get_state(config(thread_id))
        assert snapshot.next == ("human_review",)
        graph.invoke(Command(resume="approve"), config(thread_id))
        assert graph.get_state(config(thread_id)).values["status"] == "applied"
    assert (settings.project_dir / SQL).exists()


def test_resume_from_memory_checkpoint(make_graph, settings):
    saver = InMemorySaver()
    run_then_resume(make_graph, settings, lambda: nullcontext(saver), "resume-mem")


def _postgres_available() -> bool:
    try:
        import psycopg

        with psycopg.connect(postgres_uri(), connect_timeout=2):
            return True
    except Exception:
        return False


@pytest.mark.skipif(not _postgres_available(), reason="Postgres not reachable (run `make up`)")
def test_resume_from_postgres_checkpoint(make_graph, settings):
    from langgraph.checkpoint.postgres import PostgresSaver

    @contextmanager
    def saver_factory():
        # A new connection per phase, like a restarted process.
        with PostgresSaver.from_conn_string(postgres_uri()) as saver:
            saver.setup()
            yield saver

    run_then_resume(make_graph, settings, saver_factory, f"resume-pg-{os.getpid()}-{id(settings)}")
