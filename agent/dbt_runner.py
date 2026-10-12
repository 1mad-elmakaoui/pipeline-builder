"""Run dbt in a sandbox project and turn its output into something an LLM can read."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

BASELINE_MARKER = ".baseline_ok"
LOG_TAIL_CHARS = 6000


def _dbt_executable() -> str:
    local = Path(sys.executable).parent / "dbt"
    return str(local) if local.exists() else (shutil.which("dbt") or "dbt")


def _invoke(args: list[str], project_dir: Path, data_dir: Path) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "PIPELINE_DATA_DIR": str(data_dir),
        "DBT_SEND_ANONYMOUS_USAGE_STATS": "False",
        "DO_NOT_TRACK": "1",
    }
    env.pop("DBT_DUCKDB_PATH", None)  # each sandbox keeps its own warehouse file
    cmd = [_dbt_executable(), *args, "--project-dir", ".", "--profiles-dir", ".", "--no-use-colors"]
    return subprocess.run(cmd, cwd=project_dir, env=env, capture_output=True, text=True, timeout=600)


def _tail(text: str) -> str:
    return text if len(text) <= LOG_TAIL_CHARS else "...\n" + text[-LOG_TAIL_CHARS:]


def _read_results(project_dir: Path) -> list[dict[str, Any]]:
    results_path = project_dir / "target" / "run_results.json"
    manifest_path = project_dir / "target" / "manifest.json"
    if not results_path.exists():
        return []
    nodes = json.loads(manifest_path.read_text()).get("nodes", {}) if manifest_path.exists() else {}
    results = []
    for r in json.loads(results_path.read_text())["results"]:
        node = nodes.get(r["unique_id"], {})
        test_meta = node.get("test_metadata") or {}
        results.append(
            {
                "unique_id": r["unique_id"],
                "name": node.get("name", r["unique_id"].split(".")[-1]),
                "resource_type": node.get("resource_type", r["unique_id"].split(".")[0]),
                "status": r["status"],
                "message": r.get("message"),
                "failures": r.get("failures"),
                "test_name": test_meta.get("name"),
                "column": node.get("column_name"),
                "file": node.get("original_file_path"),
            }
        )
    return results


def run_build(project_dir: Path, model: str, data_dir: Path) -> dict[str, Any]:
    """`dbt build --select <model>` in a sandbox. Builds existing models once first so refs resolve."""
    project_dir = Path(project_dir)
    if not (project_dir / BASELINE_MARKER).exists():
        for args in (["seed"], ["run", "--exclude", model]):
            proc = _invoke(args, project_dir, data_dir)
            if proc.returncode != 0:
                return {
                    "success": False,
                    "command": "dbt " + " ".join(args),
                    "summary": "baseline build of the existing project failed",
                    "results": [],
                    "failures": [{"resource_type": "project", "status": "error", "message": _tail(proc.stdout)}],
                    "log": _tail(proc.stdout + proc.stderr),
                }
        (project_dir / BASELINE_MARKER).write_text("ok\n")

    (project_dir / "target" / "run_results.json").unlink(missing_ok=True)
    args = ["build", "--select", model]
    proc = _invoke(args, project_dir, data_dir)
    log = proc.stdout + proc.stderr
    results = _read_results(project_dir)
    failures = [r for r in results if r["status"] in ("error", "fail")]
    if proc.returncode != 0 and not failures:
        # Parse/compile errors happen before any node runs, so there are no results.
        failures = [{"resource_type": "project", "status": "error", "message": _tail(log)}]
    summary_line = next((line for line in reversed(log.splitlines()) if "PASS=" in line), "")
    return {
        "success": proc.returncode == 0 and not failures,
        "command": "dbt " + " ".join(args),
        "summary": summary_line.split("Done. ")[-1].strip() or ("ok" if proc.returncode == 0 else "failed"),
        "results": results,
        "failures": failures,
        "log": _tail(log),
    }
