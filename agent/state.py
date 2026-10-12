from typing import Any, Literal, TypedDict

Status = Literal[
    "running",       # profiling / generating / fixing
    "passed",        # dbt build is green, waiting for review
    "needs_help",    # hit the iteration cap without a green build
    "data_issue",    # a test fails because the source data is bad
    "revising",      # reviewer sent feedback, another round is starting
    "approved",
    "rejected",
    "applied",
]


class PipelineState(TypedDict, total=False):
    source_description: str
    file_path: str
    source_name: str
    sandbox_dir: str
    profile: dict[str, Any]
    plan: dict[str, Any]
    # Relative path inside the dbt project -> file content.
    files: dict[str, str]
    dbt_output: dict[str, Any]
    # One entry per failed build or reviewer revision, oldest first.
    error_history: list[dict[str, Any]]
    iteration: int
    status: Status
    data_issues: list[str]
    feedback: str | None
    review: dict[str, Any]
    applied_files: list[str]
