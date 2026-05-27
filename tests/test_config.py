"""
tests/test_config.py
────────────────────
25 unit tests verifying that every parameter flows from config.py
through the relevant module to the actual runtime component.

Groups:
  1. Config loading (5)
  2. LLM config per step (8)
  3. Different models/temps per step (2)
  4. Agent pipeline — max_retries (2)
  5. Cache defaults (2)
  6. Conversation defaults (1)
  7. Database engine (2)
  8. API (3)
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from config import (
    AgentConfig,
    ApiConfig,
    CacheConfig,
    ConversationConfig,
    DatabaseConfig,
    LLMConfig,
    LLMStepConfig,
    Settings,
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_settings(**overrides) -> Settings:
    """Build a Settings with specific field overrides for testing."""
    return Settings(**overrides)


# ===========================================================================
# Group 1 — Config loading
# ===========================================================================


class TestConfigLoading:
    def test_default_settings_load(self):
        s = _make_settings()
        assert s.cache.max_size == 128
        assert s.cache.ttl_seconds == 3600
        assert s.agent.max_retries == 3
        assert s.conversation.max_history == 5
        assert s.llm.provider == "auto"
        assert s.llm.answer.temperature == 0.2
        assert s.api.title == "University QA Trace UI"

    def test_env_override_nested(self):
        with patch.dict(os.environ, {"CACHE__MAX_SIZE": "256"}):
            s = Settings()
            assert s.cache.max_size == 256

    def test_env_override_flat(self):
        # DATABASE_URL (without __) must still reach database.url via the model_validator
        with patch.dict(os.environ, {"DATABASE_URL": "sqlite:///env_flat.db"}):
            s = Settings()
            assert s.database.url == "sqlite:///env_flat.db"

    def test_invalid_type_raises(self):
        with patch.dict(os.environ, {"CACHE__MAX_SIZE": "not_a_number"}):
            with pytest.raises(ValidationError):
                Settings()

    def test_settings_independence(self):
        s1 = _make_settings(cache=CacheConfig(max_size=10))
        s2 = _make_settings(cache=CacheConfig(max_size=99))
        assert s1.cache.max_size == 10
        assert s2.cache.max_size == 99
        # Mutating one does not affect the other
        s1.cache.max_size = 55
        assert s2.cache.max_size == 99


# ===========================================================================
# Group 2 — LLM config per step (8 tests)
# ===========================================================================


class TestLLMConfigPerStep:
    """Verify each factory function passes model and temperature from config."""

    def _call_factory(self, factory_fn, custom_settings):
        """Call factory_fn with patched settings, capturing get_llm args."""
        with patch("agent.llm.settings", custom_settings), \
             patch("agent.llm.get_llm") as mock_get_llm:
            factory_fn()
            return mock_get_llm.call_args

    def test_sql_llm_uses_config_model(self):
        from agent.llm import get_sql_llm
        custom = _make_settings(llm=LLMConfig(sql_generation=LLMStepConfig(model="gpt-3.5-turbo", temperature=0.0)))
        call = self._call_factory(get_sql_llm, custom)
        assert call.kwargs["model"] == "gpt-3.5-turbo"

    def test_sql_llm_uses_config_temp(self):
        from agent.llm import get_sql_llm
        custom = _make_settings(llm=LLMConfig(sql_generation=LLMStepConfig(model="gpt-4o", temperature=0.1)))
        call = self._call_factory(get_sql_llm, custom)
        assert call.kwargs["temperature"] == 0.1

    def test_retry_llm_uses_config_model(self):
        from agent.llm import get_retry_llm
        custom = _make_settings(llm=LLMConfig(retry=LLMStepConfig(model="gpt-4o-mini", temperature=0.3)))
        call = self._call_factory(get_retry_llm, custom)
        assert call.kwargs["model"] == "gpt-4o-mini"

    def test_retry_llm_uses_config_temp(self):
        from agent.llm import get_retry_llm
        custom = _make_settings(llm=LLMConfig(retry=LLMStepConfig(model="gpt-4o", temperature=0.5)))
        call = self._call_factory(get_retry_llm, custom)
        assert call.kwargs["temperature"] == 0.5

    def test_answer_llm_uses_config_model(self):
        from agent.llm import get_answer_llm
        custom = _make_settings(llm=LLMConfig(answer=LLMStepConfig(model="gpt-4-turbo", temperature=0.7)))
        call = self._call_factory(get_answer_llm, custom)
        assert call.kwargs["model"] == "gpt-4-turbo"

    def test_answer_llm_uses_config_temp(self):
        from agent.llm import get_answer_llm
        custom = _make_settings(llm=LLMConfig(answer=LLMStepConfig(model="gpt-4o", temperature=0.9)))
        call = self._call_factory(get_answer_llm, custom)
        assert call.kwargs["temperature"] == 0.9

    def test_relevance_llm_uses_config_model(self):
        from agent.llm import get_relevance_llm
        custom = _make_settings(llm=LLMConfig(relevance=LLMStepConfig(model="gpt-3.5-turbo", temperature=0.0)))
        call = self._call_factory(get_relevance_llm, custom)
        assert call.kwargs["model"] == "gpt-3.5-turbo"

    def test_relevance_llm_uses_config_temp(self):
        from agent.llm import get_relevance_llm
        custom = _make_settings(llm=LLMConfig(relevance=LLMStepConfig(model="gpt-4o", temperature=0.2)))
        call = self._call_factory(get_relevance_llm, custom)
        assert call.kwargs["temperature"] == 0.2


# ===========================================================================
# Group 3 — Different models and temperatures per step
# ===========================================================================


class TestDifferentModelsPerStep:
    def test_different_models_per_step(self):
        from agent.llm import get_answer_llm, get_relevance_llm, get_retry_llm, get_sql_llm

        custom = _make_settings(llm=LLMConfig(
            sql_generation=LLMStepConfig(model="model-sql", temperature=0.0),
            retry=LLMStepConfig(model="model-retry", temperature=0.3),
            answer=LLMStepConfig(model="model-answer", temperature=0.2),
            relevance=LLMStepConfig(model="model-relevance", temperature=0.0),
        ))
        captured = {}
        def record_get_llm(temperature, model):
            captured[model] = temperature
            return MagicMock()

        with patch("agent.llm.settings", custom), \
             patch("agent.llm.get_llm", side_effect=record_get_llm):
            get_sql_llm()
            get_retry_llm()
            get_answer_llm()
            get_relevance_llm()

        assert set(captured.keys()) == {"model-sql", "model-retry", "model-answer", "model-relevance"}

    def test_different_temps_per_step(self):
        from agent.llm import get_answer_llm, get_relevance_llm, get_retry_llm, get_sql_llm

        custom = _make_settings(llm=LLMConfig(
            sql_generation=LLMStepConfig(model="gpt-4o", temperature=0.0),
            retry=LLMStepConfig(model="gpt-4o", temperature=0.3),
            answer=LLMStepConfig(model="gpt-4o", temperature=0.2),
            relevance=LLMStepConfig(model="gpt-4o", temperature=0.0),
        ))
        temps = []

        def record_get_llm(temperature, model):
            temps.append(temperature)
            return MagicMock()

        with patch("agent.llm.settings", custom), \
             patch("agent.llm.get_llm", side_effect=record_get_llm):
            get_sql_llm()
            get_retry_llm()
            get_answer_llm()
            get_relevance_llm()

        assert temps == [0.0, 0.3, 0.2, 0.0]


# ===========================================================================
# Group 4 — Agent pipeline: max_retries
# ===========================================================================


class TestAgentMaxRetries:
    def test_max_retries_flows_to_fetch_schema(self, db_for_nodes):
        from agent.nodes import fetch_schema
        custom = _make_settings(agent=AgentConfig(max_retries=3))
        with patch("agent.nodes.settings", custom):
            result = fetch_schema({})
        assert result["max_retries"] == 3

    def test_max_retries_override(self, db_for_nodes):
        from agent.nodes import fetch_schema
        custom = _make_settings(agent=AgentConfig(max_retries=7))
        with patch("agent.nodes.settings", custom):
            result = fetch_schema({})
        assert result["max_retries"] == 7


# ===========================================================================
# Group 5 — Cache defaults
# ===========================================================================


class TestCacheDefaults:
    def test_cache_uses_config_max_size(self):
        from agent.cache import QueryCache
        custom = _make_settings(cache=CacheConfig(max_size=64, ttl_seconds=3600))
        with patch("agent.cache.settings", custom):
            cache = QueryCache()
        assert cache._max_size == 64

    def test_cache_uses_config_ttl(self):
        from agent.cache import QueryCache
        custom = _make_settings(cache=CacheConfig(max_size=128, ttl_seconds=7200))
        with patch("agent.cache.settings", custom):
            cache = QueryCache()
        assert cache._ttl_seconds == 7200


# ===========================================================================
# Group 6 — Conversation defaults
# ===========================================================================


class TestConversationDefaults:
    def test_conversation_uses_config_max_history(self):
        from agent.conversation_manager import ConversationManager
        custom = _make_settings(conversation=ConversationConfig(max_history=10))
        with patch("agent.conversation_manager.settings", custom):
            cm = ConversationManager()
        assert cm._max_history == 10


# ===========================================================================
# Group 7 — Database engine
# ===========================================================================


class TestDatabaseConfig:
    def setup_method(self):
        import db.connection as dc
        self._original_engine = dc._engine
        dc._engine = None

    def teardown_method(self):
        import db.connection as dc
        if dc._engine is not None:
            dc._engine.dispose()
        dc._engine = self._original_engine

    def test_database_url_from_config(self):
        import db.connection as dc
        custom = _make_settings(database=DatabaseConfig(url="sqlite:///:memory:", echo=False))
        with patch("db.connection.settings", custom):
            engine = dc.get_engine()
        assert str(engine.url) == "sqlite:///:memory:"

    def test_database_echo_from_config(self):
        import db.connection as dc
        custom = _make_settings(database=DatabaseConfig(url="sqlite:///:memory:", echo=True))
        with patch("db.connection.settings", custom):
            engine = dc.get_engine()
        assert engine.echo is True


# ===========================================================================
# Group 8 — API
# ===========================================================================


class TestAPIConfig:
    def test_api_title_from_config(self):
        from api.main import app
        from config import settings as real_settings
        assert app.title == real_settings.api.title

    def test_api_version_from_config(self):
        from api.main import app
        from config import settings as real_settings
        assert app.version == real_settings.api.version

    def test_api_question_max_length(self):
        from api.main import AskRequest
        custom = _make_settings(api=ApiConfig(question_max_length=10))
        with patch("api.main.settings", custom):
            with pytest.raises(ValidationError, match="exceeds maximum length"):
                AskRequest(question="a" * 11)
