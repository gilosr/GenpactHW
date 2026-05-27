"""
agent/llm.py
Configure LLM instances for all graph nodes.

Design decisions:
  - Provider abstraction: single get_llm() factory picks OpenAI or Anthropic
    based on which API key is present in .env
  - Temperature per task: SQL generation needs determinism (temp=0),
    retries need exploration (temp=0.3), answer formatting stays low (temp=0.2)
    to preserve numeric fidelity
  - Model tiering: main model for SQL gen + answer formatting,
    optionally lighter model for relevance check (cost optimization)
  - Lazy imports: only the installed/configured provider is imported, avoiding
    import-time failures when the other provider's package is absent or unconfigured
"""

import os

from dotenv import load_dotenv
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, SystemMessage

from config import _ANTHROPIC_DEFAULT, settings

load_dotenv()


def _detect_provider() -> str:
    """Determine which LLM provider to use.

    Reads settings.llm.provider first; "auto" falls back to API key detection.
    Raises RuntimeError if neither key is configured in auto mode.
    """
    provider = settings.llm.provider
    if provider != "auto":
        return provider
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    raise RuntimeError(
        "No LLM API key found. Set OPENAI_API_KEY or ANTHROPIC_API_KEY in .env"
    )


def get_llm(
    temperature: float = 0.0,
    model: str | None = None,
) -> BaseChatModel:
    """Factory that returns a configured chat model.

    Checks OPENAI_API_KEY first, falls back to ANTHROPIC_API_KEY.
    Raises RuntimeError if neither is configured.

    Args:
        temperature: Sampling temperature (0.0 = deterministic, 1.0 = creative).
        model: Optional model name override. Defaults to gpt-4o (OpenAI) or
               claude-sonnet-4-20250514 (Anthropic) based on detected provider.

    Returns:
        A configured BaseChatModel instance ready for .invoke() calls.
    """
    provider = _detect_provider()
    model_name = model or ("gpt-4o" if provider == "openai" else _ANTHROPIC_DEFAULT)

    if provider == "openai":
        from langchain_openai import ChatOpenAI  # lazy import: only if OpenAI is used

        return ChatOpenAI(
            model=model_name,
            temperature=temperature,
        )
    else:
        from langchain_anthropic import ChatAnthropic  # lazy import: only if Anthropic is used

        return ChatAnthropic(
            model=model_name,
            temperature=temperature,
        )


def get_sql_llm() -> BaseChatModel:
    """LLM for SQL generation — temperature and model from config."""
    cfg = settings.llm.sql_generation
    return get_llm(temperature=cfg.temperature, model=cfg.model)


def get_retry_llm() -> BaseChatModel:
    """LLM for SQL retry — slightly higher temperature to avoid repeating the failed query."""
    cfg = settings.llm.retry
    return get_llm(temperature=cfg.temperature, model=cfg.model)


def get_answer_llm() -> BaseChatModel:
    """LLM for answer formatting — low temperature to preserve numeric fidelity."""
    cfg = settings.llm.answer
    return get_llm(temperature=cfg.temperature, model=cfg.model)


def get_relevance_llm() -> BaseChatModel:
    """LLM for relevance classification — temperature=0 for deterministic binary output."""
    cfg = settings.llm.relevance
    return get_llm(temperature=cfg.temperature, model=cfg.model)


def _uses_anthropic(llm) -> bool:
    """Return True for LangChain Anthropic chat models and their structured wrappers."""
    module = getattr(getattr(llm, "__class__", None), "__module__", "")
    if "anthropic" in module.lower():
        return True
    wrapped = getattr(llm, "bound", None) or getattr(llm, "llm", None)
    wrapped_module = getattr(getattr(wrapped, "__class__", None), "__module__", "")
    return "anthropic" in wrapped_module.lower()


def _with_anthropic_cache_control(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Mark the stable system prefix for Anthropic ephemeral prompt caching."""
    prepared = list(messages)
    if not prepared or not isinstance(prepared[0], SystemMessage):
        return prepared
    content = prepared[0].content
    if isinstance(content, str):
        prepared[0] = SystemMessage(
            content=[
                {
                    "type": "text",
                    "text": content,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        )
    return prepared


def invoke_prompt(
    llm,
    messages: list[BaseMessage],
    *,
    trace_metadata: dict | None = None,
):
    """Invoke an LLM with cache-friendly message lists.

    Anthropic supports explicit ephemeral prompt caching, so the static system
    prefix is tagged. OpenAI benefits from implicit prefix caching when the
    system message bytes are stable, so no marker is added.
    """
    if _uses_anthropic(llm):
        messages = _with_anthropic_cache_control(messages)
    config = {"metadata": trace_metadata} if trace_metadata else None
    return llm.invoke(messages, config=config)
