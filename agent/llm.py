from typing import TypeVar

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from agent.config import MODEL_NAME

T = TypeVar("T", bound=BaseModel)


def default_llm(model_name: str = MODEL_NAME) -> BaseChatModel:
    from langchain_anthropic import ChatAnthropic

    # Reads ANTHROPIC_API_KEY from the environment.
    return ChatAnthropic(model=model_name, temperature=0, max_tokens=8192, max_retries=3)


def ask(llm: BaseChatModel, schema: type[T], system: str, user: str) -> T:
    result = llm.with_structured_output(schema).invoke([SystemMessage(system), HumanMessage(user)])
    return result if isinstance(result, schema) else schema.model_validate(result)
