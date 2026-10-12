"""Helpers for the dbt project on disk: file layout, sandbox copies, diffs."""

import difflib
import re
import shutil
from pathlib import Path
from typing import Any

import yaml

SANDBOX_IGNORE = shutil.ignore_patterns("target", "logs", "dbt_packages", "*.duckdb", "*.duckdb.wal", ".user.yml")


def to_snake(name: str) -> str:
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name.strip())
    name = re.sub(r"[^0-9a-zA-Z]+", "_", name)
    return name.strip("_").lower()


def model_name(source_name: str) -> str:
    return f"stg_{source_name}"


def file_paths(source_name: str) -> dict[str, str]:
    """Relative paths (inside the dbt project) of the three generated files."""
    base = "models/staging"
    return {
        "sql": f"{base}/stg_{source_name}.sql",
        "models": f"{base}/_{source_name}__models.yml",
        "sources": f"{base}/_{source_name}__sources.yml",
    }


def ensure_sandbox(project_dir: Path, sandbox_dir: Path) -> None:
    if not sandbox_dir.exists():
        sandbox_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(project_dir, sandbox_dir, ignore=SANDBOX_IGNORE)


def write_files(root: Path, files: dict[str, str]) -> list[Path]:
    written = []
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        written.append(path)
    return written


def unified_diff(project_dir: Path, files: dict[str, str]) -> str:
    chunks = []
    for rel, new in sorted(files.items()):
        old_path = project_dir / rel
        old = old_path.read_text() if old_path.exists() else ""
        if old == new:
            continue
        chunks.extend(
            difflib.unified_diff(
                old.splitlines(keepends=True),
                new.splitlines(keepends=True),
                fromfile=f"a/{rel}" if old_path.exists() else "/dev/null",
                tofile=f"b/{rel}",
            )
        )
    return "".join(chunks)


def known_dimensions(project_dir: Path) -> list[dict[str, Any]]:
    """Models named dim_* declared in the project's YAML, with their columns."""
    dims = []
    for path in sorted((project_dir / "models").rglob("*.yml")):
        doc = yaml.safe_load(path.read_text()) or {}
        for model in doc.get("models") or []:
            if model.get("name", "").startswith("dim_"):
                dims.append(
                    {
                        "model": model["name"],
                        "description": model.get("description", ""),
                        "columns": [c["name"] for c in model.get("columns") or []],
                    }
                )
    return dims


def column_tests(models_yml: str) -> dict[tuple[str, str], Any]:
    """Map (column, test_name) -> test entry for every column test in a models YAML."""
    try:
        doc = yaml.safe_load(models_yml) or {}
    except yaml.YAMLError:
        return {}
    tests: dict[tuple[str, str], Any] = {}
    for model in doc.get("models") or []:
        for column in model.get("columns") or []:
            for entry in (column.get("data_tests") or column.get("tests") or []):
                name = entry if isinstance(entry, str) else next(iter(entry))
                tests[(column.get("name"), name)] = entry
    return tests
