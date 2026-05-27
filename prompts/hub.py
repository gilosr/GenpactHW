from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from langchain_core.messages import BaseMessage
from langchain_core.prompts import ChatPromptTemplate

logger = logging.getLogger(__name__)

_HUB_METADATA_KEYS = ("lc_hub_owner", "lc_hub_repo", "lc_hub_commit_hash")


def hub_prompt_name(prefix: str, domain: str, kind: str, dialect: str) -> str:
    """Build a LangSmith-compatible prompt name (at most one '/' allowed).

    Uses hyphens to encode prefix/domain/kind/dialect hierarchy, e.g.
    ``genpact-university-qa-university-sql-generation-sqlite``.
    """
    return f"{prefix}-{domain}-{kind}-{dialect}"


@dataclass
class PromptBundle:
    messages: list[BaseMessage]
    trace_metadata: dict[str, str] = field(default_factory=dict)


class HubPromptLoader:
    """Lazy LangSmith Prompt Hub loader with process-local pull cache."""

    def __init__(
        self,
        *,
        hub_enabled: bool,
        hub_prefix: str,
        hub_tag: str,
        domain: str,
    ) -> None:
        self._hub_enabled = hub_enabled
        self._hub_prefix = hub_prefix
        self._hub_tag = hub_tag
        self._domain = domain
        self._client: Any | None = None

    @property
    def enabled(self) -> bool:
        return self._hub_enabled and bool(os.getenv("LANGSMITH_API_KEY"))

    def prompt_name(self, kind: str, dialect: str) -> str:
        return hub_prompt_name(self._hub_prefix, self._domain, kind, dialect)

    def pull(self, kind: str, dialect: str) -> ChatPromptTemplate | None:
        if not self.enabled:
            return None

        identifier = f"{self.prompt_name(kind, dialect)}:{self._hub_tag}"
        try:
            template = self._get_client().pull_prompt(identifier)
        except Exception as exc:
            logger.warning("Hub pull failed for %s: %s", identifier, exc)
            return None

        if not isinstance(template, ChatPromptTemplate):
            logger.warning("Hub pull returned non-ChatPromptTemplate for %s", identifier)
            return None
        return template

    def extract_trace_metadata(self, template: ChatPromptTemplate) -> dict[str, str]:
        metadata = getattr(template, "metadata", None) or {}
        return {
            key: str(metadata[key])
            for key in _HUB_METADATA_KEYS
            if key in metadata and metadata[key] is not None
        }

    def _get_client(self) -> Any:
        if self._client is None:
            from langsmith import Client

            self._client = Client()
        return self._client


def build_from_hub_or_local(
    *,
    loader: HubPromptLoader | None,
    kind: str,
    dialect: str,
    format_kwargs: dict[str, str],
    local_messages: list[BaseMessage],
) -> PromptBundle:
    if loader is not None and loader.enabled:
        template = loader.pull(kind, dialect)
        if template is not None:
            try:
                messages = template.format_messages(**format_kwargs)
                return PromptBundle(
                    messages=messages,
                    trace_metadata=loader.extract_trace_metadata(template),
                )
            except Exception as exc:
                logger.warning(
                    "Hub template format failed for %s/%s: %s",
                    kind,
                    dialect,
                    exc,
                )

    return PromptBundle(messages=local_messages, trace_metadata={})


@lru_cache(maxsize=1)
def get_hub_prompt_loader(
    hub_enabled: bool,
    hub_prefix: str,
    hub_tag: str,
    domain: str,
) -> HubPromptLoader:
    return HubPromptLoader(
        hub_enabled=hub_enabled,
        hub_prefix=hub_prefix,
        hub_tag=hub_tag,
        domain=domain,
    )
