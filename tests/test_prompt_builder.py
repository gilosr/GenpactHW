from __future__ import annotations

from unittest.mock import MagicMock

from langchain_core.messages import HumanMessage, SystemMessage


def test_sql_generation_static_prefix_is_byte_stable_across_questions() -> None:
    from prompts.manager import get_prompt_manager

    pm = get_prompt_manager()
    first = pm.build_sql_generation_messages("schema text", "How many students?").messages
    second = pm.build_sql_generation_messages("schema text", "List teachers").messages

    assert isinstance(first[0], SystemMessage)
    assert first[0].content == second[0].content
    assert first[1].content != second[1].content
    assert "schema text" in first[1].content
    assert "How many students?" in first[1].content


def test_answer_formatting_static_prefix_is_byte_stable_across_results() -> None:
    from prompts.manager import get_prompt_manager

    pm = get_prompt_manager()
    first = pm.build_answer_formatting_messages(
        question="How many students?",
        sql_query="SELECT COUNT(*) FROM students;",
        results="student_count: 20",
        row_count=1,
    )
    second = pm.build_answer_formatting_messages(
        question="List teachers",
        sql_query="SELECT first_name FROM teachers;",
        results="| first_name |\n| Ada |",
        row_count=1,
    )

    assert first[0].content == second[0].content
    assert first[1].content != second[1].content


def test_builder_returns_system_then_human_messages() -> None:
    from prompts.manager import get_prompt_manager

    pm = get_prompt_manager()
    messages = pm.build_relevance_check_messages("How many students?")

    assert isinstance(messages[0], SystemMessage)
    assert isinstance(messages[1], HumanMessage)
    assert "<user_question>" in messages[1].content


def test_anthropic_invoke_prompt_adds_ephemeral_cache_control() -> None:
    from agent.llm import invoke_prompt

    llm = MagicMock()
    llm.__class__.__module__ = "langchain_anthropic.chat_models"
    messages = [SystemMessage(content="static"), HumanMessage(content="dynamic")]

    invoke_prompt(llm, messages)

    sent = llm.invoke.call_args.args[0]
    assert sent[0].content == [
        {"type": "text", "text": "static", "cache_control": {"type": "ephemeral"}}
    ]
    assert sent[1].content == "dynamic"


def test_openai_invoke_prompt_leaves_messages_unchanged() -> None:
    from agent.llm import invoke_prompt

    llm = MagicMock()
    llm.__class__.__module__ = "langchain_openai.chat_models"
    messages = [SystemMessage(content="static"), HumanMessage(content="dynamic")]

    invoke_prompt(llm, messages)

    assert llm.invoke.call_args.args[0] == messages
