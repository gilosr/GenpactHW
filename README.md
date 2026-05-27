# University Database QA Agent

A natural language question-answering system over a university database, built with LangGraph. Ask questions in plain English and get accurate answers powered by SQL and an LLM.

## Architecture

The system has three layers: a **9-node LangGraph pipeline** that converts natural language to SQL and back to an answer; an **LRU query cache** at the entry point for standalone questions; and **centralized prompt management** that feeds all LLM nodes via domain templates with optional LangSmith Hub fallback.

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD
    User([User]) --> CM[ConversationManager]
    CM --> CacheCheck{QueryCache}
    CacheCheck -- hit --> CachedAnswer([Cached Answer])
    CacheCheck -- miss --> START

    subgraph LangGraphPipeline [LangGraph Pipeline]
        START((__start__)) --> check_relevance
        check_relevance -- not_relevant --> polite_decline --> END((__end__))
        check_relevance -- relevant --> fetch_schema
        fetch_schema --> generate_sql
        generate_sql --> validate_sql
        validate_sql -- destructive_or_empty --> error_response --> END
        validate_sql -- safe --> execute_sql
        execute_sql -- success --> format_answer --> END
        execute_sql -- retry_budget --> regenerate_sql --> generate_sql
        execute_sql -- no_retries --> error_response
    end

    subgraph PromptManagement [Prompt Management]
        PM[PromptManager]
        Domain[prompts/domains/university.py]
        Hub[LangSmith Hub optional]
        Domain --> PM
        Hub -.-> PM
    end

    PM -.-> check_relevance
    PM -.-> polite_decline
    PM -.-> generate_sql
    PM -.-> regenerate_sql
    PM -.-> format_answer

    classDef default fill:#f2f0ff,line-height:1.2
    classDef first fill-opacity:0
    classDef last fill:#bfb6fc
```

## Quick Start

```bash
# 1. Clone and set up
git clone https://github.com/gilosr/GenpactHW.git
cd GenpactHW
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Configure
cp .env.example .env
# Edit .env: add OPENAI_API_KEY (or ANTHROPIC_API_KEY) and LANGSMITH_API_KEY

# 3. Seed the database
python -m db.seed

# 4. Run a query
python -c "
from agent.conversation_manager import ConversationManager
from agent.cache import QueryCache
cm = ConversationManager(cache=QueryCache())
session = cm.create_session()
result = cm.ask('How many students are there?', session)
print(result['answer'])
"
```

## Web UI and API

Start the trace UI (requires seeded DB + API keys in `.env`):

```bash
uvicorn api.main:app --reload --port 8000
# Open http://localhost:8000
```

Three UI tabs:

- **Dashboard** — live Q&A with step-by-step trace timeline
- **Evaluation** — CSV upload, column mapping, LLM-as-judge runs
- **Trace History** — browse/search persisted past runs

| Method | Path | Purpose |
| ------ | ---- | ------- |
| GET | `/` | Web UI |
| GET | `/api/health` | Health + DB/LangSmith status |
| POST | `/api/ask` | Ask a question (optional `thread_id`, `bypass_cache`) |
| GET | `/api/schema/summary` | Table row counts |
| GET | `/api/traces/examples` | Seeded example traces |
| GET | `/api/history` | Paginated trace history (search/filter) |
| GET | `/api/history/{id}` | Single trace detail |
| POST | `/api/eval/upload` | Upload evaluation CSV |
| POST | `/api/eval/run` | Start eval run |
| GET | `/api/eval/status/{id}` | Poll run progress |
| GET | `/api/eval/results/{id}` | Full results + statistics |
| GET | `/api/eval/runs` | List past eval runs |

## Evaluation

Run regression checks against the golden dataset via the Evaluation tab or API:

1. Upload [docs/golden_dataset.csv](docs/golden_dataset.csv) (or `POST /api/eval/upload`)
2. Map the input column and expected-output columns — SQL columns use execution accuracy; NL columns use the LLM judge
3. Each row is evaluated via `ConversationManager` + `EvaluationEngine` ([evaluation/evaluator.py](evaluation/evaluator.py))
4. Scoring combines a 5-level LLM judge rubric with deterministic SQL result comparison ([evaluation/execution_accuracy.py](evaluation/execution_accuracy.py))
5. Results are persisted to `evaluation_runs/` (gitignored)

Optional config overrides in [config.py](config.py): `EVAL__JUDGE_MODEL`, `EVAL__RESULTS_DIR`.

## Running Tests

```bash
# All unit tests (fast, no API keys needed) — 333 tests
pytest --ignore=tests/evals

# Include LLM eval tests (requires API keys) — 398 tests
pytest
```

## Demo Script

Run 20 questions through the agent (relevant + off-topic) to see the full pipeline in action:

```bash
python run_questions.py
```

## Project Structure

```
GenpactHW/
├── db/                         Database layer (no LLM code)
│   ├── schema.sql              SQLite DDL — 4 tables (teachers, students, courses, enrollments)
│   ├── connection.py           SQLAlchemy engine factory + FK enforcement
│   ├── seed.py                 Deterministic seed: 6 teachers, 20 students, 12 courses, 52 enrollments
│   ├── database.py             DatabaseManager — agent's only DB interface
│   └── history.py              Persistent trace history (history.db)
├── agent/                      LangGraph pipeline
│   ├── state.py                AgentState TypedDict (InputState / OutputState)
│   ├── nodes.py                9 node functions + 3 routing functions
│   ├── graph.py                StateGraph assembly, compiled app with MemorySaver
│   ├── llm.py                  LLM provider factory (OpenAI / Anthropic auto-detect)
│   ├── cache.py                LRU query cache with TTL
│   └── conversation_manager.py Multi-turn session management
├── prompts/                    Prompt templates (no execution logic)
│   ├── manager.py              PromptManager — builds all message lists
│   ├── schemas.py              Pydantic structured output models
│   ├── hub.py                  LangSmith Hub integration (optional)
│   └── domains/
│       ├── base.py             Abstract domain interface
│       └── university.py       University-specific prompt templates
├── evaluation/
│   ├── evaluator.py            LLM-as-judge + CSV pipeline
│   └── execution_accuracy.py   Deterministic SQL result comparison
├── api/
│   ├── main.py                 FastAPI app + trace/history routes
│   └── eval_routes.py          Evaluation API (/api/eval/*)
├── web/                        Browser UI
│   ├── index.html
│   ├── app.js                  Dashboard + trace timeline
│   ├── eval.js                 Evaluation dashboard
│   ├── history.js              Trace history browser
│   └── styles.css
├── tracing/
│   └── tracer.py               print_trace(), get_trace_summary(), LangSmith config check
├── scripts/
│   └── push_prompts_to_hub.py  Push prompts to LangSmith Hub
├── tests/                      pytest suites (333 offline)
│   ├── conftest.py             Shared fixtures (in-memory DB, mock LLMs)
│   ├── test_database.py        DB layer — schema, FK, seed counts
│   ├── test_sql_generation.py  SQL generation pipeline
│   ├── test_agent_e2e.py       End-to-end graph tests
│   ├── test_nodes.py           Node function unit tests
│   ├── test_cache.py           LRU cache tests
│   ├── test_conversation_manager.py  Session + follow-up tests
│   ├── test_prompt_manager.py  Prompt builder tests
│   ├── test_prompt_builder.py  Prompt builder edge cases
│   ├── test_state_and_prompts.py     State schema tests
│   ├── test_tracing.py         Tracing utilities
│   ├── test_tracing_ui_api.py  API + tracing integration
│   ├── test_config.py          Config validation
│   ├── test_history.py         Trace history persistence
│   ├── test_eval_routes.py     Evaluation API routes
│   ├── test_evaluator.py       Evaluation engine
│   ├── test_execution_accuracy.py  SQL result comparison
│   └── evals/                  LLM evaluation tests (require API keys)
│       ├── eval_sql_generation.py
│       └── eval_relevance.py
├── docs/
│   └── golden_dataset.csv      30-question evaluation dataset
├── config.py                   Pydantic-settings config (LLM temps, retries, cache TTL, EvalConfig)
├── run_questions.py            Demo script — 20 questions through the agent
├── requirements.txt
└── .env.example
```

## Example Queries

| Complexity | Question | Pattern |
|---|---|---|
| Simple | "How many students are there?" | COUNT |
| Medium | "How many students per course?" | JOIN + GROUP BY |
| Hard | "Average grade per teacher?" | 3-table JOIN + AVG + status filter |
| Very Hard | "Top student per department?" | CTE + RANK() OVER |

## Design Decisions

- **LangGraph pipeline** — 9 nodes with conditional routing, retry cycle (max 3 attempts), and graceful off-topic handling
- **DB-agnostic design** — swap SQLite → PostgreSQL by changing `DATABASE_URL`; agent never imports `db/connection.py` directly
- **Error handling** — destructive SQL blocked before execution; empty results and DB errors trigger retry or controlled error response
- **Memory and caching** — `ConversationManager` injects sliding-window history for follow-ups; `QueryCache` serves exact-match standalone questions (LRU + TTL)
- **Prompt management** — domain templates via `PromptManager` with optional LangSmith Hub pull and local fallback
- **Tracing** — LangSmith integration plus `steps` audit trail in agent state; trace history persisted to `history.db`
- **Evaluation** — LLM-as-judge rubric plus execution accuracy for golden dataset regression
