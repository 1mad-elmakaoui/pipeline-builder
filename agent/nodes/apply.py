"""apply_changes: copy the approved files into the real dbt project."""

from agent.nodes import Deps
from agent.project import write_files
from agent.state import PipelineState


def apply_changes(state: PipelineState, deps: Deps) -> dict:
    written = write_files(deps.settings.project_dir, state["files"])
    return {"status": "applied", "applied_files": [str(p) for p in written]}
