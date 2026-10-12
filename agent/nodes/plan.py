"""plan_model: the LLM proposes names, types, the primary key and tests."""

import json

from agent.llm import ask
from agent.nodes import Deps
from agent.project import known_dimensions, to_snake
from agent.prompts import PLAN_SYSTEM, PLAN_USER
from agent.schemas import ModelPlan
from agent.state import PipelineState


def normalize_plan(plan: ModelPlan, profile: dict, dimensions: list[dict]) -> dict:
    """Keep the LLM honest: real source columns only, snake_case, sane tests, a tested primary key."""
    source_columns = {c["name"] for c in profile["columns"]}
    dims = {d["model"]: set(d["columns"]) for d in dimensions}
    pk = to_snake(plan.primary_key)
    columns = []
    seen = set()
    for col in plan.columns:
        name = to_snake(col.name)
        if col.source_column not in source_columns or name in seen:
            continue
        seen.add(name)
        tests = list(dict.fromkeys(col.tests))
        if name == pk:
            tests = ["unique", "not_null"] + [t for t in tests if t not in ("unique", "not_null")]
        if "accepted_values" in tests and not col.accepted_values:
            tests.remove("accepted_values")
        rel_ok = col.relationship_model in dims and col.relationship_field in dims.get(col.relationship_model, set())
        if "relationships" in tests and not rel_ok:
            tests.remove("relationships")
        columns.append(
            {
                "source_column": col.source_column,
                "name": name,
                "data_type": col.data_type.strip().lower(),
                "description": col.description.strip(),
                "tests": tests,
                "accepted_values": col.accepted_values if "accepted_values" in tests else [],
                "relationship_model": col.relationship_model if "relationships" in tests else None,
                "relationship_field": col.relationship_field if "relationships" in tests else None,
            }
        )
    return {"model_description": plan.model_description.strip(), "primary_key": pk, "columns": columns}


def plan_model(state: PipelineState, deps: Deps) -> dict:
    dimensions = known_dimensions(deps.settings.project_dir)
    plan = ask(
        deps.llm,
        ModelPlan,
        PLAN_SYSTEM,
        PLAN_USER.format(
            description=state["source_description"],
            source_name=state["source_name"],
            profile=json.dumps(state["profile"], indent=2, default=str),
            dimensions=json.dumps(dimensions, indent=2),
        ),
    )
    return {"plan": normalize_plan(plan, state["profile"], dimensions)}
