from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FewShotExample:
    question: str
    sql: dict[str, str]


@dataclass(frozen=True)
class CorrectionExample:
    failed_sql: str
    error: str
    corrected_sql: dict[str, str]


@dataclass(frozen=True)
class ClassificationExample:
    question: str
    classification: str


@dataclass(frozen=True)
class DialectRules:
    name: str
    concat_syntax: str
    concat_example: str
    extra_rules: tuple[str, ...] = ()


@dataclass(frozen=True)
class DomainPromptData:
    domain_name: str
    domain_description: str

    data_description: tuple[str, ...]
    classification_examples: tuple[ClassificationExample, ...]

    business_rules: tuple[str, ...]
    relationship_guide: str
    few_shot_examples: tuple[FewShotExample, ...]

    correction_examples: tuple[CorrectionExample, ...]

    answerable_topics: tuple[str, ...]
    fallback_decline: str
    cannot_answer_message: str
