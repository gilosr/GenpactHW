from __future__ import annotations

from prompts.domains.base import DomainPromptData

_REGISTRY: dict[str, DomainPromptData] = {}


def register_domain(name: str, data: DomainPromptData) -> None:
    _REGISTRY[name] = data


def get_domain_data(name: str) -> DomainPromptData:
    if not _REGISTRY:
        import prompts.domains.university  # noqa: F401
    if name not in _REGISTRY:
        raise KeyError(
            f"Unknown domain '{name}'. Available: {sorted(_REGISTRY.keys())}"
        )
    return _REGISTRY[name]
