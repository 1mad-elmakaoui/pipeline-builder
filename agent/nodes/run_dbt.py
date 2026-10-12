"""run_dbt: `dbt build --select stg_<name>` in the sandbox. No LLM."""

from pathlib import Path

from agent.nodes import Deps
from agent.project import ensure_sandbox, model_name, write_files
from agent.state import PipelineState

MESSAGE_CHARS = 600


def run_dbt(state: PipelineState, deps: Deps) -> dict:
    sandbox = Path(state["sandbox_dir"])
    # State is the source of truth: re-materialize so a resumed run on a fresh machine still works.
    ensure_sandbox(deps.settings.project_dir, sandbox)
    write_files(sandbox, state["files"])

    output = deps.dbt_runner(sandbox, model_name(state["source_name"]), deps.settings.data_dir)
    iteration = state.get("iteration", 0) + 1
    update = {"dbt_output": output, "iteration": iteration}
    if output["success"]:
        return {**update, "status": "passed"}

    entry = {
        "kind": "build_failure",
        "iteration": iteration,
        "summary": output["summary"],
        "failures": [
            {
                "node": f.get("name", f.get("resource_type")),
                "status": f["status"],
                "message": (f.get("message") or "")[:MESSAGE_CHARS],
            }
            for f in output["failures"]
        ],
    }
    history = state.get("error_history", []) + [entry]
    status = "needs_help" if iteration >= deps.settings.max_iterations else "running"
    return {**update, "error_history": history, "status": status}
