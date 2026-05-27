from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate

from prompts.domains import get_domain_data
from prompts.hub import HubPromptLoader, PromptBundle, build_from_hub_or_local
from prompts.manager import PromptManager, get_dialect_rules


def _local_sql_generation_messages(pm: PromptManager, schema: str, question: str):
    return [
        SystemMessage(content=pm._sql_gen_system),
        HumanMessage(
            content=(
                f"DATABASE SCHEMA:\n{schema}\n\n"
                f"<user_question>\n{question}\n</user_question>\n\nSQLQuery:"
            )
        ),
    ]


@pytest.fixture()
def sqlite_pm() -> PromptManager:
    return PromptManager(
        get_domain_data("university"),
        get_dialect_rules("sqlite"),
        hub_loader=HubPromptLoader(
            hub_enabled=False,
            hub_prefix="genpact-university-qa",
            hub_tag="production",
            domain="university",
        ),
    )


def test_hub_disabled_matches_local_messages(sqlite_pm: PromptManager) -> None:
    bundle = sqlite_pm.build_sql_generation_messages("schema", "How many students?")
    local = _local_sql_generation_messages(sqlite_pm, "schema", "How many students?")

    assert isinstance(bundle, PromptBundle)
    assert bundle.trace_metadata == {}
    assert bundle.messages[0].content == local[0].content
    assert bundle.messages[1].content == local[1].content


def test_hub_enabled_mock_pull_success(sqlite_pm: PromptManager) -> None:
    loader = HubPromptLoader(
        hub_enabled=True,
        hub_prefix="genpact-university-qa",
        hub_tag="production",
        domain="university",
    )
    template = ChatPromptTemplate.from_messages([
        ("system", sqlite_pm._sql_gen_system),
        (
            "human",
            "DATABASE SCHEMA:\n{schema}\n\n<user_question>\n{question}\n</user_question>\n\nSQLQuery:",
        ),
    ])
    template.metadata = {
        "lc_hub_owner": "org",
        "lc_hub_repo": "genpact-university-qa-university-sql-generation-sqlite",
        "lc_hub_commit_hash": "abc123",
    }

    with patch.dict("os.environ", {"LANGSMITH_API_KEY": "test-key"}):
        with patch.object(loader, "pull", return_value=template):
            sqlite_pm._hub_loader = loader
            bundle = sqlite_pm.build_sql_generation_messages("my-schema", "Count rows")

    assert bundle.trace_metadata["lc_hub_commit_hash"] == "abc123"
    assert "my-schema" in bundle.messages[1].content
    assert "Count rows" in bundle.messages[1].content


def test_hub_enabled_mock_pull_failure_falls_back(sqlite_pm: PromptManager) -> None:
    loader = HubPromptLoader(
        hub_enabled=True,
        hub_prefix="genpact-university-qa",
        hub_tag="production",
        domain="university",
    )

    with patch.dict("os.environ", {"LANGSMITH_API_KEY": "test-key"}):
        with patch.object(loader, "pull", return_value=None):
            sqlite_pm._hub_loader = loader
            bundle = sqlite_pm.build_sql_generation_messages("schema", "question")

    local = _local_sql_generation_messages(sqlite_pm, "schema", "question")
    assert bundle.trace_metadata == {}
    assert bundle.messages[0].content == local[0].content
    assert bundle.messages[1].content == local[1].content


def test_dialect_routing_resolves_different_hub_prompt_names() -> None:
    loader = HubPromptLoader(
        hub_enabled=True,
        hub_prefix="genpact-university-qa",
        hub_tag="production",
        domain="university",
    )

    assert loader.prompt_name("sql-generation", "sqlite") == (
        "genpact-university-qa-university-sql-generation-sqlite"
    )
    assert loader.prompt_name("sql-regeneration", "postgresql") == (
        "genpact-university-qa-university-sql-regeneration-postgresql"
    )
    assert loader.prompt_name("relevance") == (
        "genpact-university-qa-university-relevance"
    )
    assert loader.prompt_name("answer-formatting") == (
        "genpact-university-qa-university-answer-formatting"
    )
    assert loader.prompt_name("polite-decline") == (
        "genpact-university-qa-university-polite-decline"
    )


def test_relevance_hub_disabled_matches_local(sqlite_pm: PromptManager) -> None:
    bundle = sqlite_pm.build_relevance_check_messages("How many students?")
    local = sqlite_pm.build_relevance_check_messages("How many students?").messages

    assert isinstance(bundle, PromptBundle)
    assert bundle.trace_metadata == {}
    assert bundle.messages[0].content == local[0].content


def test_build_from_hub_or_local_uses_format_kwargs() -> None:
    loader = HubPromptLoader(
        hub_enabled=True,
        hub_prefix="genpact-university-qa",
        hub_tag="production",
        domain="university",
    )
    template = ChatPromptTemplate.from_messages([
        ("system", "system text"),
        (
            "human",
            "DATABASE SCHEMA:\n{schema}\n\n<user_question>\n{question}\n</user_question>\n\nSQLQuery:",
        ),
    ])
    template.metadata = {"lc_hub_commit_hash": "deadbeef"}

    local = [SystemMessage(content="local"), HumanMessage(content="local human")]
    with patch.dict("os.environ", {"LANGSMITH_API_KEY": "test-key"}):
        with patch.object(loader, "pull", return_value=template):
            bundle = build_from_hub_or_local(
                loader=loader,
                kind="sql-generation",
                format_kwargs={"schema": "S", "question": "Q"},
                local_messages=local,
                dialect="sqlite",
            )

    assert bundle.trace_metadata["lc_hub_commit_hash"] == "deadbeef"
    assert "S" in bundle.messages[1].content
    assert "Q" in bundle.messages[1].content


def test_invoke_prompt_passes_trace_metadata() -> None:
    from agent.llm import invoke_prompt

    llm = MagicMock()
    llm.__class__.__module__ = "langchain_openai.chat_models"
    messages = [SystemMessage(content="static"), HumanMessage(content="dynamic")]
    metadata = {"lc_hub_commit_hash": "abc123"}

    invoke_prompt(llm, messages, trace_metadata=metadata)

    assert llm.invoke.call_args.kwargs["config"] == {"metadata": metadata}


def test_invoke_prompt_omits_config_when_no_metadata() -> None:
    from agent.llm import invoke_prompt

    llm = MagicMock()
    llm.__class__.__module__ = "langchain_openai.chat_models"
    messages = [SystemMessage(content="static"), HumanMessage(content="dynamic")]

    invoke_prompt(llm, messages)

    assert llm.invoke.call_args.kwargs.get("config") is None
