"""generate_files: render the staging model, its YAML and the source definition into the sandbox."""

import json
from pathlib import Path

import yaml

from agent.llm import ask
from agent.nodes import Deps
from agent.project import ensure_sandbox, file_paths, model_name, write_files
from agent.prompts import REVISE_SYSTEM, REVISE_USER, render_files
from agent.schemas import Revision
from agent.state import PipelineState


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


class _IndentedDumper(yaml.SafeDumper):
    """Indent list items under their key, the way dbt projects are usually written."""

    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, False)


def _dump(doc: dict) -> str:
    return yaml.dump(doc, Dumper=_IndentedDumper, sort_keys=False, width=1000, allow_unicode=True)


def render_sql(plan: dict, source_name: str) -> str:
    lines = []
    for col in plan["columns"]:
        src = _quote_ident(col["source_column"])
        expr = src if col["data_type"] == "varchar" else f"cast({src} as {col['data_type']})"
        lines.append(f"        {expr} as {col['name']}")
    select_list = ",\n".join(lines)
    return (
        "with source as (\n\n"
        f"    select * from {{{{ source('raw_{source_name}', '{source_name}') }}}}\n\n"
        "),\n\n"
        "renamed as (\n\n"
        "    select\n"
        f"{select_list}\n"
        "    from source\n\n"
        ")\n\n"
        "select * from renamed\n"
    )


def _test_entry(col: dict):
    entries = []
    for test in col["tests"]:
        if test == "accepted_values":
            args = {"values": col["accepted_values"]}
            if col["data_type"] != "varchar":
                args["quote"] = False
            entries.append({"accepted_values": {"arguments": args}})
        elif test == "relationships":
            entries.append(
                {
                    "relationships": {
                        "arguments": {"to": f"ref('{col['relationship_model']}')", "field": col["relationship_field"]}
                    }
                }
            )
        else:
            entries.append(test)
    return entries


def render_models_yml(plan: dict, source_name: str) -> str:
    columns = []
    for col in plan["columns"]:
        entry = {"name": col["name"], "description": col["description"], "data_type": col["data_type"]}
        tests = _test_entry(col)
        if tests:
            entry["data_tests"] = tests
        columns.append(entry)
    doc = {
        "version": 2,
        "models": [{"name": model_name(source_name), "description": plan["model_description"], "columns": columns}],
    }
    return _dump(doc)


def csv_location(file_path: str, data_dir: Path) -> str:
    """Path expression used in the source. Files in the data dir stay portable via PIPELINE_DATA_DIR."""
    path = Path(file_path).resolve()
    if path.parent == data_dir.resolve():
        return "{{ env_var('PIPELINE_DATA_DIR', '../data') }}/" + path.name
    return str(path)


def render_sources_yml(source_name: str, description: str, file_path: str, data_dir: Path) -> str:
    location = csv_location(file_path, data_dir)
    doc = {
        "version": 2,
        "sources": [
            {
                "name": f"raw_{source_name}",
                "description": f"Raw CSV extract: {description}",
                "tables": [
                    {
                        "name": source_name,
                        "description": description,
                        "meta": {
                            # Read every column as text; the staging model owns the casts.
                            "external_location": f"read_csv('{location}', header = true, all_varchar = true)"
                        },
                    }
                ],
            }
        ],
    }
    return _dump(doc)


def render_all(state: PipelineState, data_dir: Path) -> dict[str, str]:
    name = state["source_name"]
    paths = file_paths(name)
    return {
        paths["sql"]: render_sql(state["plan"], name),
        paths["models"]: render_models_yml(state["plan"], name),
        paths["sources"]: render_sources_yml(name, state["source_description"], state["file_path"], data_dir),
    }


def revise_files(state: PipelineState, deps: Deps) -> tuple[dict[str, str], str]:
    files = dict(state["files"])
    revision = ask(
        deps.llm,
        Revision,
        REVISE_SYSTEM,
        REVISE_USER.format(
            feedback=state["feedback"],
            description=state["source_description"],
            profile=json.dumps(state["profile"], indent=2, default=str),
            files=render_files(files),
        ),
    )
    for edit in revision.edits:
        if edit.path in files:
            files[edit.path] = edit.content.rstrip() + "\n"
    return files, revision.summary


def generate_files(state: PipelineState, deps: Deps) -> dict:
    update: dict = {}
    if state.get("feedback"):
        files, summary = revise_files(state, deps)
        update = {
            "feedback": None,
            "iteration": 0,
            "data_issues": [],
            "status": "running",
            "error_history": state.get("error_history", [])
            + [{"kind": "feedback", "feedback": state["feedback"], "explanation": summary}],
        }
    else:
        files = render_all(state, deps.settings.data_dir)
    sandbox = Path(state["sandbox_dir"])
    ensure_sandbox(deps.settings.project_dir, sandbox)
    write_files(sandbox, files)
    return {**update, "files": files}
