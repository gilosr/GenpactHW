#!/usr/bin/env python3
"""
Publish all agent prompts to LangSmith Prompt Hub.

One-time setup:
  1. Set LANGSMITH_API_KEY in .env
  2. Run this script for each domain/dialect you support
  3. Set PROMPT__HUB_ENABLED=true to pull prompts at runtime

Example:
  python scripts/push_prompts_to_hub.py \\
      --domain university \\
      --dialects sqlite,postgresql \\
      --tag production
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
from langchain_core.prompts import ChatPromptTemplate

from config import settings
from prompts.domains import get_domain_data
from prompts.hub import hub_prompt_name
from prompts.manager import PromptManager, get_dialect_rules

load_dotenv()

_RELEVANCE_HUMAN = "<user_question>\n{question}\n</user_question>\n\nClassification:"
_SQL_GENERATION_HUMAN = (
    "DATABASE SCHEMA:\n{schema}\n\n"
    "<user_question>\n{question}\n</user_question>\n\nSQLQuery:"
)
_SQL_REGENERATION_HUMAN = (
    "DATABASE SCHEMA:\n{schema}\n\n"
    "PREVIOUS ATTEMPTS:\n{previous_attempts}\n\n"
    "Original question:\n<user_question>\n{question}\n</user_question>\n\n"
    "Corrected SQLQuery:"
)
_ANSWER_HUMAN = (
    "SQL Query Used:\n{sql_query}\n\n"
    "Query Results:\n{results}\n"
    "Number of rows returned: {row_count}\n\n"
    "<user_question>\n{question}\n</user_question>\n\nAnswer:"
)
_DECLINE_HUMAN = "<user_question>\n{question}\n</user_question>\n\nResponse:"


def _build_dialect_templates(
    domain: str,
    dialect: str,
) -> tuple[ChatPromptTemplate, ChatPromptTemplate]:
    pm = PromptManager(get_domain_data(domain), get_dialect_rules(dialect), hub_loader=None)
    generation = ChatPromptTemplate.from_messages([
        ("system", pm._render_sql_generation_system()),
        ("human", _SQL_GENERATION_HUMAN),
    ])
    regeneration = ChatPromptTemplate.from_messages([
        ("system", pm._render_sql_regeneration_system()),
        ("human", _SQL_REGENERATION_HUMAN),
    ])
    return generation, regeneration


def _build_domain_templates(domain: str) -> dict[str, ChatPromptTemplate]:
    """Domain-only prompts (dialect-independent). Uses sqlite rules for rendering."""
    pm = PromptManager(get_domain_data(domain), get_dialect_rules("sqlite"), hub_loader=None)
    return {
        "relevance": ChatPromptTemplate.from_messages([
            ("system", pm._render_relevance_system()),
            ("human", _RELEVANCE_HUMAN),
        ]),
        "answer-formatting": ChatPromptTemplate.from_messages([
            ("system", pm._render_answer_formatting_system()),
            ("human", _ANSWER_HUMAN),
        ]),
        "polite-decline": ChatPromptTemplate.from_messages([
            ("system", pm._render_polite_decline_system()),
            ("human", _DECLINE_HUMAN),
        ]),
    }


def _ensure_commit_tag(client, prompt_id: str, tag: str) -> None:
    """Apply commit tag to the latest commit (e.g. when content is unchanged)."""
    from langsmith import utils as ls_utils

    owner, prompt_name, _ = ls_utils.parse_prompt_identifier(prompt_id)
    prompt_owner_and_name = f"{owner}/{prompt_name}"
    commits = list(client.list_prompt_commits(prompt_id, limit=1))
    if not commits:
        return
    commit = commits[0]
    try:
        client._create_commit_tags(prompt_owner_and_name, str(commit.id), tag)
        print(f"  Tagged latest commit ({commit.commit_hash[:8]}) -> {tag}")
    except Exception as exc:
        if "already" not in str(exc).lower() and "conflict" not in str(exc).lower():
            print(f"  Warning: could not apply tag {tag!r}: {exc}", file=sys.stderr)


def _push_prompt(client, prompt_id: str, template: ChatPromptTemplate, tag: str) -> None:
    from langsmith.utils import LangSmithConflictError

    try:
        commit_url = client.push_prompt(
            prompt_id,
            object=template,
            commit_tags=[tag],
        )
        print(f"Pushed {prompt_id} -> {commit_url}")
    except LangSmithConflictError as exc:
        if "Nothing to commit" not in str(exc):
            raise
        _ensure_commit_tag(client, prompt_id, tag)
        url = client._get_prompt_url(prompt_id)
        print(f"Unchanged {prompt_id} (already at latest commit) -> {url}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Push all agent prompts to LangSmith Prompt Hub.",
    )
    parser.add_argument(
        "--domain",
        default=settings.prompt.domain,
        help="Domain name (default: config prompt.domain)",
    )
    parser.add_argument(
        "--dialects",
        default="sqlite,postgresql",
        help="Comma-separated dialect list for SQL prompts (default: sqlite,postgresql)",
    )
    parser.add_argument(
        "--tag",
        default=settings.prompt.hub_tag,
        help="Commit tag to apply (default: config prompt.hub_tag)",
    )
    parser.add_argument(
        "--prefix",
        default=settings.prompt.hub_prefix,
        help="Hub name prefix (default: config prompt.hub_prefix)",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    if not os.getenv("LANGSMITH_API_KEY"):
        print("ERROR: LANGSMITH_API_KEY is not set.", file=sys.stderr)
        return 1

    from langsmith import Client

    client = Client()
    dialects = [d.strip() for d in args.dialects.split(",") if d.strip()]
    if not dialects:
        print("ERROR: No dialects specified.", file=sys.stderr)
        return 1

    domain_templates = _build_domain_templates(args.domain)
    for kind, template in domain_templates.items():
        prompt_id = hub_prompt_name(args.prefix, args.domain, kind)
        _push_prompt(client, prompt_id, template, args.tag)

    for dialect in dialects:
        generation, regeneration = _build_dialect_templates(args.domain, dialect)
        for kind, template in (
            ("sql-generation", generation),
            ("sql-regeneration", regeneration),
        ):
            prompt_id = hub_prompt_name(args.prefix, args.domain, kind, dialect)
            _push_prompt(client, prompt_id, template, args.tag)

    print(
        "\nDone. Enable runtime Hub pulls with PROMPT__HUB_ENABLED=true "
        f"and PROMPT__HUB_TAG={args.tag}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
