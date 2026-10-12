"""Langfuse tracing, enabled when LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are set."""

import os
from typing import Any


def enabled() -> bool:
    return bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"))


def callbacks() -> list[Any]:
    if not enabled():
        return []
    from langfuse.langchain import CallbackHandler

    return [CallbackHandler()]


def flush() -> None:
    if enabled():
        from langfuse import get_client

        get_client().flush()
