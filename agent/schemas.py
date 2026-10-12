"""Structured outputs the LLM must return."""

from typing import Literal

from pydantic import BaseModel, Field

TestName = Literal["unique", "not_null", "accepted_values", "relationships"]


class ColumnPlan(BaseModel):
    source_column: str = Field(description="Column name exactly as it appears in the CSV header.")
    name: str = Field(description="snake_case name in the staging model.")
    data_type: str = Field(description="DuckDB type to cast to, e.g. integer, bigint, varchar, date, timestamp, decimal(12,2), boolean.")
    description: str = Field(description="One-sentence column description for the docs.")
    tests: list[TestName] = Field(default_factory=list)
    accepted_values: list[str] = Field(default_factory=list, description="Only when tests include accepted_values.")
    relationship_model: str | None = Field(default=None, description="Known dim_* model, only when tests include relationships.")
    relationship_field: str | None = Field(default=None, description="Column in relationship_model.")


class ModelPlan(BaseModel):
    model_description: str = Field(description="What one row of the model represents and where it comes from.")
    primary_key: str = Field(description="snake_case name of the primary key column.")
    columns: list[ColumnPlan]


class FixDecision(BaseModel):
    kind: Literal["code_fix", "data_issue"] = Field(
        description="code_fix when our generated code is wrong; data_issue when a test correctly caught bad source data."
    )
    file: str | None = Field(default=None, description="For code_fix: path of the ONE file to replace, exactly as listed.")
    content: str | None = Field(default=None, description="For code_fix: the complete new content of that file.")
    explanation: str = Field(description="One line: what failed and what you changed (or which data is bad).")


class FileEdit(BaseModel):
    path: str
    content: str = Field(description="Complete new file content.")


class Revision(BaseModel):
    edits: list[FileEdit] = Field(description="Only the files that change.")
    summary: str = Field(description="One line describing what changed.")
