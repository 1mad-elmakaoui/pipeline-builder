import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

MODEL_NAME = "claude-sonnet-4-6"
MAX_ITERATIONS = 5


@dataclass(frozen=True)
class Settings:
    project_dir: Path = field(
        default_factory=lambda: Path(os.getenv("DBT_PROJECT_DIR", REPO_ROOT / "dbt_project")).resolve()
    )
    sandbox_root: Path = field(
        default_factory=lambda: Path(os.getenv("SANDBOX_DIR", REPO_ROOT / ".sandbox")).resolve()
    )
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("DATA_DIR", REPO_ROOT / "data")).resolve())
    max_iterations: int = MAX_ITERATIONS
    model_name: str = MODEL_NAME


def postgres_uri() -> str:
    return os.getenv("POSTGRES_URI", "postgresql://agent:agent@localhost:5432/agent")
