#!/usr/bin/env python3
"""
Publish SQL generation and regeneration prompts to LangSmith Prompt Hub.

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


def _build_templates(
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


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Push SQL prompts to LangSmith Prompt Hub.",
    )
    parser.add_argument(
        "--domain",
        default=settings.prompt.domain,
        help="Domain name (default: config prompt.domain)",
    )
    parser.add_argument(
        "--dialects",
        default="sqlite,postgresql",
        help="Comma-separated dialect list (default: sqlite,postgresql)",
    )
    parser.add_argument(
        "--tag",
        default=settings.prompt.hub_tag,
        help="Commit tag to apply (default: config prompt.hub_tag)",
    )
    parser.add_argument(
        "--prefix",
        default=settings.prompt.hub_prefix,
        help="Hub repo prefix (default: config prompt.hub_prefix)",
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

    for dialect in dialects:
        generation, regeneration = _build_templates(args.domain, dialect)
        for kind, template in (
            ("sql-generation", generation),
            ("sql-regeneration", regeneration),
        ):
            prompt_id = hub_prompt_name(args.prefix, args.domain, kind, dialect)
            commit_url = client.push_prompt(
                prompt_id,
                object=template,
                tags=[args.tag],
            )
            print(f"Pushed {prompt_id} -> {commit_url}")

    print(
        "\nDone. Enable runtime Hub pulls with PROMPT__HUB_ENABLED=true "
        f"and PROMPT__HUB_TAG={args.tag}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
