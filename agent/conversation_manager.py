"""
agent/conversation_manager.py
──────────────────────────────
Session-aware invocation wrapper for the university QA agent.

Manages conversation context across multiple turns within a session.
Each session gets a unique thread_id. Recent Q&A history is injected
into the question so the LLM can resolve follow-up references.

Design decisions:
- Question augmentation (not graph modification): keeps the graph
  as a stateless pipeline, testable and explainable. Nodes don't need
  to know about multi-turn — they see a richer question string.
- Sliding window of last N turns (default 5): bounded token usage,
  covers 95% of follow-up scenarios without token-limit risk.
- Cache only for standalone questions: follow-up questions depend on
  conversation context, so caching on raw text would serve wrong answers.
- MemorySaver (in-memory): demo-appropriate. Production upgrade:
  SqliteSaver or PostgresSaver in agent/graph.py.

Production upgrade path (document, don't implement here):
- Token-budget truncation via langchain_core.messages.trim_messages
- Summarization node that compresses old history into a paragraph
- Persist _history alongside checkpoints for cross-process continuity
"""

from __future__ import annotations

import uuid
from typing import Optional

from agent.cache import QueryCache
from config import settings


class ConversationManager:
    """Session-aware wrapper that adds multi-turn context to the QA agent.

    Each session is identified by a thread_id (UUID). The manager keeps
    a sliding window of recent Q&A pairs and prepends them to new questions
    before invoking the graph, enabling follow-up reference resolution.

    Usage:
        from agent.conversation_manager import ConversationManager
        from agent.cache import QueryCache

        cm = ConversationManager(cache=QueryCache())
        session_id = cm.create_session()
        result = cm.ask("How many students are there?", session_id)
        print(result["answer"])
    """

    def __init__(
        self,
        app=None,
        cache: Optional[QueryCache] = None,
        max_history: int | None = None,
    ) -> None:
        """Initialise the manager.

        Args:
            app: Compiled LangGraph application. Defaults to importing
                 the module-level ``app`` from agent.graph at first use
                 (lazy import avoids circular dependency at module load).
            cache: Optional QueryCache instance for repeated identical
                   standalone questions. Pass None to disable caching.
            max_history: Maximum recent Q&A pairs in context window.
                         Defaults to config value.
        """
        self._app = app
        self._cache = cache
        self._max_history = max_history if max_history is not None else settings.conversation.max_history
        # thread_id -> list of (question, answer) pairs
        self._history: dict[str, list[tuple[str, str]]] = {}

    def _get_app(self):
        """Lazy-load the compiled graph to avoid import-time side effects."""
        if self._app is None:
            from agent.graph import app  # noqa: PLC0415
            self._app = app
        return self._app

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_session(self) -> str:
        """Create a new conversation session.

        Returns:
            A UUID4 thread_id string that identifies this session.
            Pass this to every subsequent ask() call.
        """
        thread_id = str(uuid.uuid4())
        self._history[thread_id] = []
        return thread_id

    def ask(
        self,
        question: str,
        thread_id: str,
        bypass_cache: bool = False,
    ) -> dict:
        """Ask a question within a conversation session.

        Flow:
          1. Check cache (standalone questions only — no history)
          2. Augment question with recent conversation context
          3. Invoke the graph
          4. Store Q&A pair in history (original question, not augmented)
          5. Cache standalone results

        Args:
            question: The user's question (may reference prior turns).
            thread_id: Session identifier from create_session().
            bypass_cache: If True, skip cache lookup and always invoke
                          the graph (useful for forcing fresh results).

        Returns:
            dict with keys: answer, steps, sql_query, thread_id, turn, cached
        """
        history = self._history.get(thread_id, [])
        has_history = len(history) > 0

        # 1. Cache check — only for standalone questions (no conversation context).
        #    Follow-up questions depend on history so caching on raw text would
        #    serve context-free (wrong) answers from other conversations.
        if self._cache and not bypass_cache and not has_history:
            cached = self._cache.get(question)
            if cached:
                # Store in history so future follow-ups have context
                self._history.setdefault(thread_id, []).append(
                    (question, cached["answer"])
                )
                return {
                    **cached,
                    "thread_id": thread_id,
                    "cached": True,
                    "turn": len(self._history[thread_id]),
                    "steps": ["cache_hit: returned cached answer"],
                }

        # 2. Augment question with conversation context (sliding window)
        if has_history:
            augmented = self._build_contextual_question(
                question, history[-self._max_history:]
            )
        else:
            augmented = question

        # 3. Invoke the graph
        config = {
            "configurable": {"thread_id": thread_id},
            "tags": ["university-qa"],
            "metadata": {"question": question, "turn": len(history) + 1},
        }
        result = self._get_app().invoke({"question": augmented}, config=config)

        # 4. Store Q&A pair — ORIGINAL question (not augmented) so future
        #    context doesn't nest recursively.
        answer = result.get("answer", "")
        self._history.setdefault(thread_id, []).append((question, answer))

        # 5. Cache standalone results (no conversation context at time of ask).
        #    Never cache error responses — transient failures must not be
        #    served to future identical questions.
        steps = result.get("steps", [])
        has_error = any("error_response" in s for s in steps)
        if self._cache and not has_history and answer and not has_error:
            self._cache.put(
                question,
                result.get("sql_query", ""),
                result.get("query_result", []),
                answer,
            )

        return {
            "answer": answer,
            "steps": result.get("steps", []),
            "sql_query": result.get("sql_query", ""),
            "thread_id": thread_id,
            "turn": len(self._history[thread_id]),
            "cached": False,
        }

    def reset_session(self, thread_id: str) -> None:
        """Clear history and remove the session.

        Useful for explicitly starting a fresh conversation on the same
        thread_id, or for cleanup after a session ends.

        Args:
            thread_id: Session identifier to reset.
        """
        self._history.pop(thread_id, None)

    def get_session_info(self, thread_id: str) -> dict:
        """Return metadata about a session.

        Args:
            thread_id: Session identifier to inspect.

        Returns:
            dict with keys:
              - "thread_id" (str): The session identifier
              - "turn_count" (int): Number of Q&A pairs so far
              - "exists" (bool): Whether the session is initialised
        """
        exists = thread_id in self._history
        turn_count = len(self._history.get(thread_id, []))
        return {
            "thread_id": thread_id,
            "turn_count": turn_count,
            "exists": exists,
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_contextual_question(
        self,
        question: str,
        recent_history: list[tuple[str, str]],
    ) -> str:
        """Prepend recent conversation context to the question.

        Allows the LLM to resolve pronouns and references like
        'that course', 'Prof. Chen', 'how many of them' from prior turns.

        The preamble is clearly labelled "for context only" so the LLM
        treats it as background, not as a new question to answer.

        Args:
            question: The current user question.
            recent_history: List of (question, answer) tuples from
                            recent turns (already sliced to max_history).

        Returns:
            Augmented question string with conversation context prepended.
        """
        lines = []
        for q, a in recent_history:
            lines.append(f"User asked: {q}")
            lines.append(f"Answer: {a}")
        context = "\n".join(lines)
        return (
            f"Previous conversation (for context only):\n"
            f"{context}\n\n"
            f"Current question: {question}"
        )
