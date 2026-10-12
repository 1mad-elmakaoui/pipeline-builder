"""profile_source: describe the CSV with DuckDB. No LLM."""

from pathlib import Path
from typing import Any

import duckdb
from langgraph.config import get_config

from agent.nodes import Deps
from agent.project import to_snake
from agent.state import PipelineState

SAMPLE_ROWS = 5
MAX_TOP_VALUES = 10


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def profile_csv(path: Path) -> dict[str, Any]:
    con = duckdb.connect()
    typed = f"read_csv({_quote_literal(str(path))}, header = true)"
    raw = f"read_csv({_quote_literal(str(path))}, header = true, all_varchar = true)"

    row_count = con.execute(f"select count(*) from {typed}").fetchone()[0]
    columns = []
    for name, inferred_type, *_ in con.execute(f"describe select * from {typed}").fetchall():
        col = _quote_ident(name)
        nulls, distinct = con.execute(f"select count(*) - count({col}), count(distinct {col}) from {typed}").fetchone()
        info: dict[str, Any] = {
            "name": name,
            "inferred_type": inferred_type,
            "null_rate": round(nulls / row_count, 4) if row_count else 0.0,
            "distinct_count": distinct,
        }
        if 0 < distinct <= MAX_TOP_VALUES and distinct < row_count:
            info["values"] = [
                v for (v,) in con.execute(f"select distinct {col} from {raw} where {col} is not null order by 1").fetchall()
            ]
        duplicated = row_count - nulls - distinct
        if duplicated > 0 and to_snake(name).endswith("id"):
            info["duplicate_values"] = [
                {"value": v, "count": c}
                for v, c in con.execute(
                    f"select {col}, count(*) from {raw} where {col} is not null group by 1 having count(*) > 1 "
                    f"order by 2 desc, 1 limit 5"
                ).fetchall()
            ]
        columns.append(info)

    unique_cols = [c["name"] for c in columns if c["null_rate"] == 0 and c["distinct_count"] == row_count]
    id_like = [c for c in unique_cols if to_snake(c).endswith("id")]
    candidate_pk = (id_like or unique_cols or [None])[0]

    cursor = con.execute(f"select * from {raw} limit {SAMPLE_ROWS}")
    headers = [d[0] for d in cursor.description]
    samples = [dict(zip(headers, row)) for row in cursor.fetchall()]

    return {
        "file": path.name,
        "row_count": row_count,
        "columns": columns,
        "candidate_primary_key": candidate_pk,
        "note": None if candidate_pk else "No single column is unique and non-null in this file.",
        "sample_rows": samples,
    }


def profile_source(state: PipelineState, deps: Deps) -> dict:
    path = Path(state["file_path"]).resolve()
    thread_id = get_config()["configurable"]["thread_id"]
    return {
        "file_path": str(path),
        "source_name": state.get("source_name") or to_snake(path.stem),
        "sandbox_dir": str(deps.settings.sandbox_root / thread_id / "dbt_project"),
        "profile": profile_csv(path),
        "files": {},
        "error_history": [],
        "data_issues": [],
        "iteration": 0,
        "status": "running",
        "feedback": None,
    }
