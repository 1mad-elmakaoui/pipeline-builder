from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from langchain_core.language_models import BaseChatModel

from agent.config import Settings
from agent.dbt_runner import run_build
from agent.llm import default_llm

DbtRunner = Callable[[Path, str, Path], dict[str, Any]]


@dataclass
class Deps:
    """Everything the nodes need besides the state. Tests swap in fakes."""

    settings: Settings = field(default_factory=Settings)
    llm_factory: Callable[[], BaseChatModel] | None = None
    dbt_runner: DbtRunner = run_build
    _llm: BaseChatModel | None = field(default=None, init=False, repr=False)

    @property
    def llm(self) -> BaseChatModel:
        if self._llm is None:
            factory = self.llm_factory or (lambda: default_llm(self.settings.model_name))
            self._llm = factory()
        return self._llm
