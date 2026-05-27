# Production Considerations

> **Document purpose:** This document explains what would be required to take the Genpact University QA Agent from its current demo state to a production-grade system. It is written at the depth expected for a technical interview — every recommendation is justified, concrete, and traceable to the existing codebase.

---

## Demo vs. Production — Summary Table

| Area | Current (Demo) | Production |
|------|---------------|------------|
| **Database** | SQLite single-file (`university.db`) | PostgreSQL + SQLAlchemy connection pooling |
| **Reliability** | `MemorySaver` (in-memory), 3 retries | `PostgresSaver` durable checkpoints, circuit breaker, dead letter queue |
| **Scalability** | Single process, unlimited LLM calls | Connection pooling, horizontal scaling, LLM rate limiting, async invocation |
| **Monitoring** | LangSmith traces + `steps` audit trail | Prometheus metrics, structured JSON logs, Grafana dashboards, alerting |
| **Security** | Regex DML/DDL block in `_BLOCKED_PATTERN` | Read-only DB user, input sanitization, API auth, secrets manager |
| **Deployment** | `python main.py` locally | Docker, FastAPI endpoint, health checks, CI/CD, blue-green deploy |
| **Cost** | Unlimited LLM calls per question | Token budgets, `QueryCache`, model tiering, schema introspection caching |
| **Memory & Cache** | In-memory `MemorySaver` + LRU `QueryCache` | `PostgresSaver`, Redis distributed cache, semantic similarity cache |
| **Testing** | pytest + in-memory SQLite + mocked LLMs | Integration suite, canary queries, LLM regression tests, load testing |

---

## 1. Database

### Current State

The demo runs on SQLite (`university.db`) via a singleton `Engine` created in `db/connection.py`. SQLite is a single file — it has no built-in connection pooling, does not support concurrent writes, and cannot run on a separate server.

### Production Upgrade: PostgreSQL

PostgreSQL is the natural upgrade target for production because it supports:
- True concurrent reads and writes
- Built-in connection pooling via pgBouncer
- Advanced query planning
- Row-level security (relevant for the security section)

**The key architectural insight:** Because the agent never imports `db/connection.py` directly, and because `DatabaseManager` accepts any SQLAlchemy-compatible connection string, swapping the database requires **zero agent code changes**. It is a one-line config change:

```bash
# .env (demo)
DATABASE_URL=sqlite:///university.db

# .env (production)
DATABASE_URL=postgresql+psycopg2://qa_user:secret@db-host:5432/university
```

### Connection Pooling

SQLAlchemy's `create_engine` accepts pool configuration parameters that become critical under concurrent load:

```python
from sqlalchemy import create_engine

engine = create_engine(
    "postgresql+psycopg2://qa_user:secret@db-host:5432/university",
    pool_size=10,          # Number of persistent connections in the pool
    max_overflow=20,       # Extra connections allowed beyond pool_size
    pool_timeout=30,       # Seconds to wait for a connection before raising
    pool_recycle=1800,     # Recycle connections older than 30 min (avoids stale connections)
    pool_pre_ping=True,    # Test connection health before use (handles DB restarts)
)
```

`pool_size=10` means up to 10 connections are kept alive and reused. `max_overflow=20` allows bursting to 30 total connections under peak load. `pool_timeout=30` ensures a queued request fails fast rather than hanging indefinitely.

The current `get_engine()` in `db/connection.py` does not pass any pool arguments (it defaults to `pool_size=5`). For production, these values would be tuned based on observed concurrency and the DB server's `max_connections` setting.

### Schema Migration: Alembic, Not Raw DDL

The demo runs `db/schema.sql` directly via `executescript()` in `init_db()`. This works for the initial schema but is fragile for updates — if you add a column or index, `executescript()` re-runs and hits errors on existing tables (even with `IF NOT EXISTS`, you cannot `ALTER TABLE` idempotently with raw DDL).

In production, schema changes go through **Alembic**:

```bash
pip install alembic
alembic init alembic
# Edit alembic/env.py to point at your DATABASE_URL
alembic revision --autogenerate -m "add_index_on_enrollments_status"
alembic upgrade head
```

Alembic maintains a `alembic_version` table in the database that tracks which migrations have been applied. It generates `upgrade()` and `downgrade()` functions for every schema change, enabling safe forward and backward migration with zero data loss.

---

## 2. Reliability

### Current State

Session checkpoints use `MemorySaver` (line 31 in `agent/graph.py`), which stores all graph state in a Python dictionary in the process's heap. When the process restarts, all conversation history is lost.

The retry cycle (up to 3 attempts) handles SQL execution failures but has no protection against LLM API outages — if the API is down, every request fails immediately.

### Durable Session Checkpoints: PostgresSaver

Replacing `MemorySaver` with `PostgresSaver` makes conversation history survive process restarts:

```python
# agent/graph.py (production)
from langgraph.checkpoint.postgres import PostgresSaver

DB_URI = os.getenv("DATABASE_URL")  # postgresql+psycopg2://...

with PostgresSaver.from_conn_string(DB_URI) as checkpointer:
    app = create_graph().compile(checkpointer=checkpointer)
```

`PostgresSaver` writes checkpoint data (the full `AgentState` dict) to a `checkpoints` table after every node execution. If the process crashes mid-conversation, the next request with the same `thread_id` resumes exactly from where it left off.

For simpler deployments that don't need a full PostgreSQL cluster, `SqliteSaver` works with a dedicated SQLite file (separate from the data DB):

```python
from langgraph.checkpoint.sqlite import SqliteSaver

with SqliteSaver.from_conn_string("checkpoints.db") as checkpointer:
    app = create_graph().compile(checkpointer=checkpointer)
```

`SqliteSaver` is safe for single-process deployments and significantly more durable than `MemorySaver`.

### Circuit Breaker for LLM API Calls

A circuit breaker stops calling a failing service after a threshold of consecutive failures, preventing cascading timeouts across all requests during an LLM outage. The pattern has three states:

- **Closed** (normal): all calls go through
- **Open** (tripped): calls fail immediately without hitting the API
- **Half-open** (recovering): one probe request is allowed to test if the API recovered

```python
import time
from dataclasses import dataclass, field

@dataclass
class CircuitBreaker:
    failure_threshold: int = 5       # Open after 5 consecutive failures
    recovery_timeout: float = 60.0   # Try again after 60 seconds
    _failures: int = field(default=0, repr=False)
    _opened_at: float = field(default=0.0, repr=False)
    _state: str = field(default="closed", repr=False)

    def call(self, fn, *args, **kwargs):
        if self._state == "open":
            if time.time() - self._opened_at > self.recovery_timeout:
                self._state = "half-open"
            else:
                raise RuntimeError("Circuit breaker OPEN — LLM API unavailable")
        try:
            result = fn(*args, **kwargs)
            self._failures = 0
            self._state = "closed"
            return result
        except Exception:
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._state = "open"
                self._opened_at = time.time()
            raise

_llm_breaker = CircuitBreaker()
```

In `agent/nodes.py`, every `llm.invoke()` call would be wrapped in `_llm_breaker.call(llm.invoke, prompt)`. When the circuit opens, `generate_sql` immediately routes to `error_response` with a clear "service temporarily unavailable" message instead of timing out after 30 seconds.

### Dead Letter Queue for Failed Queries

When a question exhausts all 3 retry attempts and reaches `error_response`, the failure is currently logged but the query is lost. In production, failed queries should be persisted to a dead letter queue (DLQ) for later analysis:

```python
# Minimal DLQ: write to a DB table
def send_to_dlq(
    question: str,
    thread_id: str,
    failed_sql: str,
    error: str,
) -> None:
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO failed_queries
                (question, thread_id, failed_sql, error_message, created_at)
            VALUES
                (:question, :thread_id, :failed_sql, :error, NOW())
        """), {
            "question": question, "thread_id": thread_id,
            "failed_sql": failed_sql, "error": error,
        })
```

A background job processes the DLQ periodically: re-attempts after a delay, alerts on patterns (e.g., the same question failing repeatedly suggests a prompt or schema issue), or surfaces failures to an admin dashboard.

### Idempotent Retry Logic

The current retry cycle is already idempotent at the graph level — `regenerate_sql` increments `attempts` but does not duplicate state writes. However, in a distributed system with `PostgresSaver`, a network timeout during `app.invoke()` could cause the client to retry the entire request, leading to duplicate graph executions for the same `thread_id`. To prevent this:

- The `thread_id` + `turn_number` pair should serve as an idempotency key
- `ConversationManager.ask()` should check if the last stored answer for this turn already exists before re-invoking the graph
- `PostgresSaver` stores the complete checkpoint after each node, so a duplicate invocation with the same `thread_id` will resume from the last checkpoint rather than starting over

---

## 3. Scalability

### Current State

The demo runs as a single Python process. `get_engine()` in `db/connection.py` returns a singleton `Engine` shared within that process. There is no rate limiting on LLM calls — a flood of requests would saturate the OpenAI API quota immediately.

### Connection Pooling (Already Covered Under Database)

See §1. Pool configuration is the first scalability lever.

### Horizontal Scaling Behind a Load Balancer

To run multiple instances of the agent service (e.g., behind an nginx or AWS ALB load balancer), every instance must use:

1. A **shared external database** (PostgreSQL), not SQLite (which is per-file)
2. A **shared checkpoint store** (`PostgresSaver`), so a user's conversation can be picked up by any instance
3. A **shared cache** (Redis), so cache hits work across instances (covered in §8)

Each instance is stateless — all state lives in PostgreSQL and Redis. Instances can be started and stopped freely without affecting ongoing conversations.

### LLM Rate Limiting: Token Bucket Algorithm

The **token bucket** algorithm models a bucket that fills with tokens at a fixed rate (e.g., 60,000 tokens per minute — the OpenAI TPM limit) and each LLM call consumes tokens proportional to its prompt size.

```python
import threading
import time

class TokenBucketRateLimiter:
    def __init__(self, tokens_per_minute: int = 60_000):
        self.capacity = tokens_per_minute
        self.tokens = float(tokens_per_minute)
        self.refill_rate = tokens_per_minute / 60.0  # tokens per second
        self.lock = threading.Lock()
        self.last_refill = time.time()

    def consume(self, token_count: int) -> None:
        with self.lock:
            now = time.time()
            elapsed = now - self.last_refill
            self.tokens = min(
                self.capacity,
                self.tokens + elapsed * self.refill_rate
            )
            self.last_refill = now
            if self.tokens < token_count:
                wait = (token_count - self.tokens) / self.refill_rate
                time.sleep(wait)
                self.tokens = 0
            else:
                self.tokens -= token_count

_rate_limiter = TokenBucketRateLimiter(tokens_per_minute=60_000)
```

Before each LLM call in `generate_sql` and `format_answer`, estimate the prompt token count (using `tiktoken`) and call `_rate_limiter.consume(estimated_tokens)`. This provides backpressure rather than letting requests fail with a 429 error.

### Schema Introspection Caching

`DatabaseManager.get_schema()` calls `SQLDatabase.get_table_info()`, which issues `PRAGMA table_info` queries on every call. In the demo this is called once per request in `fetch_schema`. Under load with 100 concurrent requests, this generates 100 identical DB queries.

In production, cache the schema with a TTL:

```python
import functools
import time

_schema_cache: dict = {}

def get_schema_cached(manager: DatabaseManager, ttl_seconds: int = 300) -> str:
    now = time.time()
    if "schema" in _schema_cache and now - _schema_cache["ts"] < ttl_seconds:
        return _schema_cache["schema"]
    schema = manager.get_schema()
    _schema_cache["schema"] = schema
    _schema_cache["ts"] = now
    return schema
```

The schema only changes during DDL migrations — a 5-minute TTL means at most 5 minutes of stale schema in the prompt after a migration, which is acceptable.

### Async LangGraph Invocation

`app.invoke()` is synchronous and blocks the Python thread. Under a FastAPI server with multiple concurrent requests, blocking threads exhaust the thread pool. Switch to `app.ainvoke()` for async execution:

```python
# FastAPI endpoint (see §6 for full endpoint)
@app.post("/api/ask")
async def ask(request: AskRequest):
    result = await graph_app.ainvoke(
        {"question": request.question},
        config={"configurable": {"thread_id": request.thread_id}},
    )
    return {"answer": result["answer"], "steps": result["steps"]}
```

`ainvoke()` uses `asyncio` under the hood. Each LangGraph node runs in the async event loop, and I/O-bound operations (LLM calls, DB queries) don't block other requests.

---

## 4. Monitoring and Observability

### Current State

The demo uses two observability mechanisms:
1. **LangSmith** — automatic distributed tracing of all LangGraph nodes and LLM calls, viewable at smith.langchain.com
2. **State audit trail** — `steps: Annotated[list[str], add]` is appended by every node, giving a programmatic execution trace

These are excellent for development and debugging. For production, we need metrics (numbers over time) and alerting (notifications when metrics cross thresholds).

### Prometheus Metrics

Prometheus is the industry-standard metrics system for Python services. Metrics are exposed via an HTTP endpoint (`/metrics`) and scraped by a Prometheus server on a schedule.

```python
from prometheus_client import Counter, Histogram, Gauge, start_http_server

# Counters (monotonically increasing)
REQUEST_COUNT = Counter(
    "qa_requests_total",
    "Total question-answering requests",
    ["outcome"],  # labels: "success", "error", "decline", "cached"
)
RETRY_COUNT = Counter(
    "qa_sql_retries_total",
    "Total SQL generation retries",
)
CACHE_HITS = Counter("qa_cache_hits_total", "Total QueryCache hits")
CACHE_MISSES = Counter("qa_cache_misses_total", "Total QueryCache misses")

# Histograms (distribution of values, e.g. latency)
REQUEST_LATENCY = Histogram(
    "qa_request_duration_seconds",
    "End-to-end request latency",
    buckets=[0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0],
)
LLM_LATENCY = Histogram(
    "qa_llm_call_duration_seconds",
    "Individual LLM call latency",
    ["node"],  # labels: "check_relevance", "generate_sql", "format_answer"
)

# Gauges (current value)
ACTIVE_SESSIONS = Gauge("qa_active_sessions", "Number of active conversation sessions")

# Expose metrics endpoint alongside the API
start_http_server(9090)
```

**Usage in node functions:**

```python
import time

def generate_sql(state: AgentState) -> dict:
    start = time.time()
    # ... SQL generation logic ...
    LLM_LATENCY.labels(node="generate_sql").observe(time.time() - start)
    return updated_state
```

**Key metrics to track:**
- `qa_requests_total{outcome="error"}` / `qa_requests_total` → **error rate** (alert if > 5%)
- `qa_request_duration_seconds` p50/p95/p99 → **latency percentiles** (alert if p99 > 15s)
- `qa_cache_hits_total` / (`qa_cache_hits_total` + `qa_cache_misses_total`) → **cache hit rate** (alert if drops below 20%)
- `qa_sql_retries_total` rate → **retry rate** (spike indicates LLM regression)

### Alerting Rules

Prometheus alerting rules evaluate metric expressions and fire alerts to PagerDuty or Slack:

```yaml
# prometheus/alerts.yml
groups:
  - name: qa_agent
    rules:
      - alert: HighErrorRate
        expr: rate(qa_requests_total{outcome="error"}[5m]) /
              rate(qa_requests_total[5m]) > 0.05
        for: 2m
        labels:
          severity: warning
        annotations:
          summary: "QA agent error rate > 5% for 2 minutes"

      - alert: HighP99Latency
        expr: histogram_quantile(0.99, qa_request_duration_seconds_bucket) > 15
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "P99 request latency > 15 seconds"

      - alert: LowCacheHitRate
        expr: rate(qa_cache_hits_total[10m]) /
              (rate(qa_cache_hits_total[10m]) + rate(qa_cache_misses_total[10m])) < 0.2
        for: 10m
        labels:
          severity: info
        annotations:
          summary: "Cache hit rate dropped below 20%"
```

### LangSmith Evaluation Datasets

Beyond real-time tracing, LangSmith supports **evaluation datasets** — collections of (input, expected output) pairs that can be run against the live system to detect regressions:

```python
from langsmith import Client

client = Client()

# Create a dataset of known-good question/answer pairs
dataset = client.create_dataset("university-qa-regression")
client.create_examples(
    inputs=[
        {"question": "How many students are there?"},
        {"question": "What is the average grade in CS101?"},
    ],
    outputs=[
        {"answer_contains": "20"},
        {"answer_contains": "80.5"},
    ],
    dataset_id=dataset.id,
)
```

Run the evaluation suite in CI before deploying a new model version. If the pass rate drops below a threshold, block the deployment.

### Structured Logging

Replace `print()` and unstructured log messages with JSON-structured logs containing a request ID for correlation across services:

```python
import json
import logging
import uuid

class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps({
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", None),
            "node": getattr(record, "node", None),
        })

logger = logging.getLogger("qa_agent")
logger.addHandler(logging.StreamHandler())
logger.handlers[0].setFormatter(JSONFormatter())
```

Every request generates a `request_id = str(uuid.uuid4())` that is threaded through all log messages, LangSmith metadata, and error reports. When a user reports an issue, you can search logs by `request_id` and reconstruct the full execution trace across every service.

---

## 5. Security

### Current State

The demo has two layers of SQL injection defense:
1. **`validate_sql` node** (`agent/nodes.py`) — regex blocks destructive keywords before execution
2. **`DatabaseManager._BLOCKED_PATTERN`** (`db/database.py` line 43) — second regex check inside `execute_query()`

Both use the same `re.compile(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|REPLACE|MERGE)\b", re.IGNORECASE)` pattern.

### Read-Only Database User — The Strongest Defense

Regex blocking is defense-in-depth but not foolproof. Creative SQL injection can bypass keyword-based filters (e.g., comments, Unicode substitution, nested subqueries that obscure keywords). The **strongest SQL injection defense** is a read-only database user at the infrastructure level:

```sql
-- Run once as the DBA user on PostgreSQL
CREATE USER qa_agent WITH PASSWORD 'strong-random-password';
GRANT CONNECT ON DATABASE university TO qa_agent;
GRANT USAGE ON SCHEMA public TO qa_agent;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO qa_agent;

-- Ensure future tables are also readable (important for migrations)
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON TABLES TO qa_agent;
```

When the agent connects as `qa_agent`, the database engine enforces read-only access at the permission level. Even if an adversary bypasses all application-level checks and injects `DROP TABLE students`, PostgreSQL will reject it with `ERROR: permission denied for table students`. The regex checks become a secondary layer, not the primary defense.

### Input Sanitization

Before the question reaches the LLM, sanitize it at the API boundary:

```python
import re

MAX_QUESTION_LENGTH = 500

def sanitize_question(question: str) -> str:
    # Strip null bytes (crash some parsers)
    question = question.replace("\x00", "")
    # Normalize whitespace
    question = " ".join(question.split())
    # Enforce length limit
    if len(question) > MAX_QUESTION_LENGTH:
        raise ValueError(f"Question exceeds maximum length of {MAX_QUESTION_LENGTH} characters")
    return question
```

**Why length limits matter for LLMs:** A user who sends a 100,000-character question is either attempting a prompt injection attack (embedding hidden instructions) or will exhaust the token budget. Reject at the edge before any LLM call is made.

### API Authentication

The demo runs as a CLI script with no authentication. The production API endpoint requires authentication to prevent unauthorized access and to enable per-user rate limiting:

**API key authentication** (simpler, suitable for machine-to-machine):

```python
from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

api_key_header = APIKeyHeader(name="X-API-Key")

def verify_api_key(api_key: str = Security(api_key_header)) -> str:
    if api_key not in VALID_API_KEYS:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return api_key
```

**JWT authentication** (suitable for end-user web applications):

```python
from jose import JWTError, jwt

def verify_jwt(token: str = Depends(oauth2_scheme)) -> dict:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        return payload
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
```

### Rate Limiting Per User/IP

Even with authentication, a single user can send thousands of requests per second. Rate limit at the application layer using the sliding window algorithm:

```python
from collections import defaultdict
import time

class SlidingWindowRateLimiter:
    def __init__(self, limit: int = 60, window_seconds: int = 60):
        self.limit = limit
        self.window = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)

    def allow(self, key: str) -> bool:
        now = time.time()
        window_start = now - self.window
        self._requests[key] = [
            t for t in self._requests[key] if t > window_start
        ]
        if len(self._requests[key]) >= self.limit:
            return False
        self._requests[key].append(now)
        return True

_rate_limiter = SlidingWindowRateLimiter(limit=60, window_seconds=60)

# In the FastAPI endpoint:
if not _rate_limiter.allow(user_id):
    raise HTTPException(status_code=429, detail="Rate limit exceeded")
```

### Never Log Raw SQL with User Data

Agent nodes should never log the full SQL query with embedded user data. In production:

```python
# Bad — logs user question and generated SQL in plain text
logger.debug(f"Executing SQL: {sql}")

# Good — log a hash for correlation without exposing data
import hashlib
sql_hash = hashlib.sha256(sql.encode()).hexdigest()[:8]
logger.debug(f"Executing SQL (hash={sql_hash})", extra={"request_id": request_id})
```

Full SQL queries are available in LangSmith traces, which are behind authentication and can be granted to authorized engineers only.

### Secret Management

The demo uses `.env` files for API keys and connection strings. In production:

1. **Never store secrets in environment files or source code**
2. Use a secrets manager:
   - **AWS Secrets Manager**: `boto3.client('secretsmanager').get_secret_value(SecretId='prod/qa-agent/openai-key')`
   - **HashiCorp Vault**: `vault kv get secret/qa-agent/openai-key`
   - **Kubernetes Secrets**: mounted as environment variables in the pod spec
3. Rotate secrets regularly; the secrets manager handles rotation without service restarts
4. Audit secret access — every read of a secret creates an audit log entry

---

## 6. Deployment

### Current State

The demo is invoked via `python main.py` or `python -m agent.conversation_manager` locally. There is no HTTP endpoint, no containerization, and no automated deployment.

### Docker Container with Multi-Stage Build

A multi-stage Docker build keeps the production image small by excluding development dependencies:

```dockerfile
# Stage 1: build dependencies
FROM python:3.11-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Stage 2: production image
FROM python:3.11-slim AS production
WORKDIR /app

# Copy installed packages from builder (no pip in final image)
COPY --from=builder /root/.local /root/.local

# Copy source code
COPY db/ db/
COPY agent/ agent/
COPY prompts/ prompts/
COPY tracing/ tracing/

# Non-root user for security
RUN useradd --create-home appuser
USER appuser

ENV PATH=/root/.local/bin:$PATH
ENV PYTHONUNBUFFERED=1

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

The multi-stage build separates the `pip install` step (which downloads ~300MB of packages) from the final runtime image. The production image contains only the installed packages and source code — no pip, no build tools.

### FastAPI Endpoint

The primary API surface exposes two endpoints:

```python
# api/main.py
from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
import os

app = FastAPI(title="University QA Agent", version="1.0.0")

class AskRequest(BaseModel):
    question: str
    thread_id: str | None = None  # None → create new session

class AskResponse(BaseModel):
    answer: str
    thread_id: str
    turn: int
    cached: bool
    steps: list[str]

@app.post("/api/ask", response_model=AskResponse)
async def ask(
    request: AskRequest,
    user: dict = Depends(verify_jwt),  # authentication
) -> AskResponse:
    question = sanitize_question(request.question)

    if not _rate_limiter.allow(user["sub"]):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    thread_id = request.thread_id or manager.create_session()
    result = await manager.ask_async(question, thread_id)
    return AskResponse(**result)

@app.get("/health")
async def health() -> dict:
    """Health check for load balancer and monitoring."""
    db_ok = _check_db_connection()
    llm_ok = _check_llm_availability()
    status = "ok" if db_ok and llm_ok else "degraded"
    return {
        "status": status,
        "db": "ok" if db_ok else "error",
        "llm": "ok" if llm_ok else "error",
    }
```

**Why `/health` matters:** Load balancers (AWS ALB, Kubernetes) hit this endpoint every 10 seconds. If the endpoint returns a non-2xx status, the instance is removed from the rotation automatically. The check should verify both DB connectivity and LLM API reachability (by testing a minimal API call or checking the circuit breaker state).

### CI/CD Pipeline

A GitHub Actions workflow runs on every push to `main`:

```yaml
# .github/workflows/deploy.yml
name: Test, Build, Deploy

on:
  push:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install -r requirements.txt
      - run: pytest -v --tb=short
        env:
          DATABASE_URL: "sqlite:///:memory:"
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}

  build-and-push:
    needs: test
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: |
          docker build -t qa-agent:${{ github.sha }} .
          docker push registry/qa-agent:${{ github.sha }}

  deploy:
    needs: build-and-push
    runs-on: ubuntu-latest
    steps:
      - run: |
          kubectl set image deployment/qa-agent \
            qa-agent=registry/qa-agent:${{ github.sha }}
          kubectl rollout status deployment/qa-agent
```

### Blue-Green Deployment for Zero Downtime

**Blue-green deployment** runs two identical production environments (blue = current, green = new version). The load balancer routes 100% of traffic to blue. When deploying:

1. Deploy the new version to green
2. Run smoke tests against green
3. Switch the load balancer to green (atomic, typically < 1 second)
4. Keep blue running for 10 minutes as rollback target
5. If any alert fires post-switch, revert the load balancer to blue

For Kubernetes, a rolling deployment achieves similar safety:

```yaml
# k8s/deployment.yml
spec:
  replicas: 3
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1         # Start 1 new pod before removing old ones
      maxUnavailable: 0   # Never have fewer than 3 healthy pods
```

This ensures at least 3 healthy instances are serving traffic at all times during deployment.

### Environment-Specific Configuration

Use separate configuration profiles for dev, staging, and production:

```
.env.dev      → DATABASE_URL=sqlite:///dev.db, DEBUG=true, LLM_MODEL=gpt-4o-mini
.env.staging  → DATABASE_URL=postgresql://..., DEBUG=false, LLM_MODEL=gpt-4o
.env.prod     → (no file — secrets from Vault/Secrets Manager), LLM_MODEL=gpt-4o
```

---

## 7. Cost Management

### Current State

The demo makes up to 4 LLM calls per question (relevance check, SQL generation, optionally 1-2 retries, answer formatting) with no budget enforcement. Under sustained load, LLM API costs can grow proportionally with request volume.

### Token Budget Per Request

Before sending a prompt to the LLM, estimate its token count and enforce a budget:

```python
import tiktoken

_encoder = tiktoken.encoding_for_model("gpt-4o")

def estimate_tokens(text: str) -> int:
    return len(_encoder.encode(text))

def enforce_token_budget(prompt: str, budget: int = 4096) -> str:
    token_count = estimate_tokens(prompt)
    if token_count <= budget:
        return prompt
    # Truncate schema description if over budget
    # (schema is the largest variable component)
    lines = prompt.split("\n")
    while estimate_tokens("\n".join(lines)) > budget and len(lines) > 10:
        lines.pop(len(lines) // 2)  # Remove middle line (heuristic)
    return "\n".join(lines)
```

The SQL generation prompt contains: system instructions (~300 tokens) + schema (~1,400 tokens) + few-shot examples (~500 tokens) + question (~20 tokens) = ~2,220 tokens per call. The budget enforcer prevents a pathological case where the question contains embedded context that bloats the prompt past the model's context window.

### QueryCache Avoiding Redundant LLM Calls

The existing `QueryCache` in `agent/cache.py` already eliminates redundant LLM calls for repeated identical questions. In production, monitor the cache hit rate via Prometheus:

```python
# In ConversationManager.ask():
if cached:
    CACHE_HITS.inc()
    return cached
CACHE_MISSES.inc()
```

A healthy deployment should see a 30-50% cache hit rate for a university QA system because certain questions ("How many students are enrolled?", "What courses does Prof. Chen teach?") are asked repeatedly across sessions.

**Cost impact:** At approximately $0.01 per request (4 LLM calls × ~2K tokens × $0.0025/1K tokens for GPT-4o), a 40% cache hit rate reduces LLM costs by 40%.

### Model Tiering

Not every node needs the full capability of `gpt-4o`. The relevance check is a binary classification (relevant / not_relevant) on a short input — it can be handled by a cheaper model:

```python
# agent/llm.py (production tiering)
def get_relevance_llm():
    """Use the cheap model for binary classification."""
    return get_llm(temperature=0.0, model="gpt-4o-mini")

def get_sql_llm():
    """Full model for complex SQL generation."""
    return get_llm(temperature=0.0, model="gpt-4o")

def get_answer_llm():
    """Full model for fluent natural language formatting."""
    return get_llm(temperature=0.7, model="gpt-4o")
```

GPT-4o-mini costs approximately 15× less than GPT-4o per token. Since the relevance check is called on every request, this single tiering change reduces total cost by roughly 5-10% with no quality impact (it's a binary label, not a reasoning task).

### Schema Introspection Caching

`get_schema()` is called by `fetch_schema` on every request. The schema is stable (only changes during migrations). Cache it aggressively:

```python
# In agent/nodes.py
_cached_schema: str | None = None
_schema_cached_at: float = 0.0
_SCHEMA_TTL = 300  # 5 minutes

def fetch_schema(state: AgentState) -> dict:
    global _cached_schema, _schema_cached_at
    now = time.time()
    if _cached_schema is None or now - _schema_cached_at > _SCHEMA_TTL:
        _cached_schema = _db.get_schema()
        _schema_cached_at = now
    return {
        "schema_info": _cached_schema,
        "attempts": 0,
        "max_retries": 3,
        "steps": [f"[fetch_schema] loaded schema ({len(_cached_schema)} chars, cached)"],
    }
```

This eliminates ~4 `PRAGMA table_info` DB queries per request, which matters under high load.

### Monitoring Cost Per Request Over Time

Log the estimated token cost per request:

```python
# After LLM call
prompt_tokens = response.usage.prompt_tokens
completion_tokens = response.usage.completion_tokens
cost_usd = (prompt_tokens * 0.0025 + completion_tokens * 0.01) / 1000
logger.info(
    "LLM call completed",
    extra={
        "request_id": request_id,
        "node": node_name,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "estimated_cost_usd": cost_usd,
    },
)
```

Aggregate these in Grafana to track cost per request, cost per day, and cost per question type. Alert if average cost per request exceeds a threshold.

---

## 8. Memory and Caching Upgrades

### Current State

Two memory systems exist in the demo:

1. **`MemorySaver`** (`agent/graph.py` line 115) — LangGraph's built-in in-memory checkpointer. Stores full `AgentState` per `thread_id`. Lost on process restart.
2. **`QueryCache`** (`agent/cache.py`) — In-memory `OrderedDict`-based LRU cache with TTL. Exact-match only. Not shared across processes.

### Session Persistence: PostgresSaver

Covered in §2. The key point to add here is that `PostgresSaver` stores not just the final state but **a checkpoint after every node**. This enables:

- **Resuming mid-conversation** after a crash
- **Audit trails** of every state transition for debugging
- **Multi-instance routing**: any instance can serve any session

### Distributed Cache: Redis

The current `QueryCache` is an in-memory `OrderedDict` local to each process. In a multi-instance deployment, two instances serving the same question will both miss the cache and make duplicate LLM calls.

Replace with Redis for shared state:

```python
import redis
import json
import hashlib

class RedisQueryCache:
    def __init__(self, url: str = "redis://localhost:6379", ttl_seconds: int = 3600):
        self._client = redis.from_url(url)
        self._ttl = ttl_seconds

    def _key(self, question: str) -> str:
        normalized = question.strip().lower()
        return "qa:" + hashlib.sha256(normalized.encode()).hexdigest()

    def get(self, question: str) -> dict | None:
        data = self._client.get(self._key(question))
        if data is None:
            return None
        return json.loads(data)

    def put(self, question: str, sql_query: str, query_result: list, answer: str) -> None:
        if not answer:
            return
        value = json.dumps({
            "sql_query": sql_query,
            "query_result": query_result,
            "answer": answer,
        })
        self._client.setex(self._key(question), self._ttl, value)
```

`RedisQueryCache` has the same interface as `QueryCache`, making it a drop-in replacement in `ConversationManager`. Redis supports atomic operations, TTL, and eviction policies — making it production-safe for concurrent writes from multiple instances.

### Semantic Similarity Cache

The current exact-match cache misses semantically equivalent questions: "How many students?" and "What is the total student count?" are different strings but should return the same cached answer.

A semantic similarity cache embeds each question as a vector and finds cached entries within a cosine similarity threshold:

```python
from sentence_transformers import SentenceTransformer
import numpy as np

class SemanticCache:
    def __init__(self, threshold: float = 0.95, max_size: int = 128):
        self._model = SentenceTransformer("all-MiniLM-L6-v2")  # 22MB, fast
        self._threshold = threshold
        self._embeddings: list[np.ndarray] = []
        self._entries: list[dict] = []

    def get(self, question: str) -> dict | None:
        if not self._embeddings:
            return None
        query_embedding = self._model.encode(question)
        # Cosine similarity = dot product of L2-normalized vectors
        similarities = np.dot(
            np.array(self._embeddings),
            query_embedding / np.linalg.norm(query_embedding)
        )
        best_idx = np.argmax(similarities)
        if similarities[best_idx] >= self._threshold:
            return self._entries[best_idx]
        return None

    def put(self, question: str, sql_query: str, query_result: list, answer: str) -> None:
        if not answer:
            return
        embedding = self._model.encode(question)
        self._embeddings.append(embedding / np.linalg.norm(embedding))
        self._entries.append({
            "sql_query": sql_query,
            "query_result": query_result,
            "answer": answer,
        })
```

The threshold of 0.95 is deliberately high — you want only semantically near-identical questions to share cache entries. Lower thresholds (e.g., 0.80) risk returning incorrect cached answers for questions that are similar but actually ask different things.

For production scale, the embedding vectors would be stored in a vector database like Pinecone, Weaviate, or pgvector (a PostgreSQL extension) rather than an in-memory list. This enables efficient approximate nearest-neighbor search over millions of cached questions.

### Cache Warming

On startup, pre-populate the cache with the most frequently asked questions. This eliminates cold-start latency for common queries:

```python
WARM_UP_QUESTIONS = [
    "How many students are there?",
    "List all teachers and their departments",
    "How many students are enrolled in each course?",
    "What is the average grade in CS101?",
    "Which student has the highest average grade?",
]

async def warm_cache(manager: ConversationManager) -> None:
    logger.info(f"Warming cache with {len(WARM_UP_QUESTIONS)} questions")
    for question in WARM_UP_QUESTIONS:
        thread_id = manager.create_session()
        await manager.ask_async(question, thread_id, bypass_cache=False)
    logger.info("Cache warming complete")

# In FastAPI lifespan handler:
@asynccontextmanager
async def lifespan(app: FastAPI):
    await warm_cache(conversation_manager)
    yield
```

---

## 9. Testing in Production

### Current State

The test suite (`tests/`) uses in-memory SQLite and mocked LLMs:
- `tests/test_database.py` — schema, FK enforcement, seed counts
- `tests/test_sql_generation.py` — tiered SQL generation with mocked LLMs
- `tests/test_agent_e2e.py` — full pipeline with all LLMs mocked

These tests are fast, deterministic, and require no API keys. They catch regressions in the pipeline orchestration but cannot catch LLM quality regressions (a new model version that generates worse SQL).

### Integration Tests Against Staging Database

In the staging environment, run integration tests against a real PostgreSQL database populated with production-like data:

```python
# tests/test_integration.py
import pytest

@pytest.mark.integration
class TestStagingDatabase:
    """Run against staging DB. Requires STAGING_DATABASE_URL env var."""

    def test_schema_matches_production(self, staging_db_manager):
        schema = staging_db_manager.get_schema()
        for table in ["teachers", "students", "courses", "enrollments"]:
            assert table in schema

    def test_student_count_in_expected_range(self, staging_db_manager):
        rows = staging_db_manager.execute_query("SELECT COUNT(*) AS cnt FROM students")
        assert rows[0]["cnt"] >= 20  # At least seed data
```

Run with `pytest -m integration --staging` in the CI pipeline's staging step (after deploy to staging, before promote to production).

### Canary Queries

A canary is a synthetic test request that runs on a schedule against the live production system. It detects correctness regressions that unit tests cannot catch:

```python
# scripts/canary.py — run every 5 minutes via cron or Kubernetes CronJob
import sys
from agent.conversation_manager import ConversationManager
from agent.cache import QueryCache

CANARY_CHECKS = [
    {
        "question": "How many students are there?",
        "assertion": lambda ans: "20" in ans,
        "description": "student count == 20",
    },
    {
        "question": "What courses does Prof. Chen teach?",
        "assertion": lambda ans: "CS101" in ans or "Chen" in ans,
        "description": "Prof. Chen courses visible",
    },
]

def run_canary():
    manager = ConversationManager(cache=QueryCache())
    failures = []
    for check in CANARY_CHECKS:
        thread_id = manager.create_session()
        result = manager.ask(check["question"], thread_id, bypass_cache=True)
        if not check["assertion"](result["answer"]):
            failures.append(f"FAILED: {check['description']}\nAnswer: {result['answer']}")
    if failures:
        print("\n".join(failures), file=sys.stderr)
        sys.exit(1)  # Non-zero exit triggers alert in monitoring

if __name__ == "__main__":
    run_canary()
```

Canary failures alert on PagerDuty. A canary that passes before deployment but fails after is a deployment regression — roll back immediately.

### LLM Regression Tests with LangSmith Evaluation

When upgrading the LLM model version (e.g., `gpt-4o` → `gpt-4.1`), run the LangSmith evaluation dataset to verify that SQL generation quality did not regress:

```python
from langsmith import Client
from langsmith.evaluation import evaluate

client = Client()

def correctness_evaluator(run, example):
    """Check if the answer contains the expected fact."""
    answer = run.outputs.get("answer", "")
    expected = example.outputs.get("answer_contains", "")
    return {"score": 1 if expected in answer else 0, "key": "correctness"}

results = evaluate(
    lambda inputs: manager.ask(inputs["question"], manager.create_session()),
    data="university-qa-regression",  # Dataset name in LangSmith
    evaluators=[correctness_evaluator],
    experiment_prefix="gpt-4.1-upgrade",
)

pass_rate = results.summary_metrics["correctness"]
if pass_rate < 0.90:
    raise RuntimeError(f"LLM regression: pass rate {pass_rate:.1%} < 90% threshold")
```

This runs every question in the evaluation dataset through the full pipeline with the new model and verifies that at least 90% of answers are correct. Block the deployment if the threshold is not met.

### Load Testing

Before launching a production deployment, run load tests to find the throughput limit and verify graceful degradation under overload:

```python
# load_test.py using locust
from locust import HttpUser, task, between

class QAAgentUser(HttpUser):
    wait_time = between(1, 3)  # 1-3 seconds between requests

    QUESTIONS = [
        "How many students are there?",
        "What courses does Prof. Chen teach?",
        "What is the average grade in CS101?",
    ]

    def on_start(self):
        # Create a session
        resp = self.client.post("/api/ask", json={"question": self.QUESTIONS[0]})
        self.thread_id = resp.json()["thread_id"]

    @task
    def ask_question(self):
        import random
        self.client.post("/api/ask", json={
            "question": random.choice(self.QUESTIONS),
            "thread_id": self.thread_id,
        })
```

Run with `locust -f load_test.py --headless -u 50 -r 5 --run-time 5m` (50 users, 5 new users/second, 5 minutes). Monitor:
- **Requests per second**: target > 10 RPS (LLM latency is the bottleneck)
- **P99 latency**: target < 15s
- **Error rate**: target < 1%

If the service degrades under load, the circuit breaker (§2) kicks in and returns fast errors instead of slow timeouts — confirming that the system fails safely.

### Chaos Testing

Chaos testing verifies that the system degrades gracefully when dependencies fail. Simulate:

**LLM API outage:**
```python
# Mock the LLM to raise an exception
with patch("agent.nodes.get_sql_llm") as mock:
    mock.return_value.invoke.side_effect = Exception("Connection refused")
    result = manager.ask("How many students?", thread_id)
assert "temporarily unavailable" in result["answer"].lower()
assert result["answer"] != ""  # Must return an answer, not crash
```

**Database outage:**
```python
# Simulate DB connection failure
with patch.object(DatabaseManager, "execute_query") as mock:
    mock.side_effect = DatabaseError("Connection refused")
    result = manager.ask("How many students?", thread_id)
assert any("error_response" in step for step in result["steps"])
```

Both scenarios should result in a user-facing error message (not a 500 HTTP error or uncaught exception), demonstrating that the error handling strategy from §5.1 (every failure path ends at `error_response → END`) works correctly under real infrastructure failures.

---

## Summary: Interview-Ready Answers

When asked "what would it take to productionize this?" in an interview, walk through this order:

1. **Database first** — change `DATABASE_URL` to PostgreSQL. Zero agent code changes (this is the demo of DB-agnostic design).
2. **Reliability** — swap `MemorySaver` → `PostgresSaver` for durable sessions. Add circuit breaker around LLM calls.
3. **Security** — create a read-only DB user. The regex blocks are defense-in-depth, but infrastructure-level permissions are the real defense.
4. **Deployment** — Dockerize, expose via FastAPI with `/api/ask` and `/health`. Add auth middleware.
5. **Monitoring** — Prometheus metrics + Grafana. Alert on error rate, P99 latency, cache hit rate.
6. **Cost** — the `QueryCache` already exists. Add model tiering (cheaper model for relevance check), schema caching, and token budgets.
7. **Scale** — Redis for distributed cache, PostgresSaver for shared session state across instances. Async `ainvoke()` for concurrent requests.
8. **Testing** — canary queries against production, LangSmith evaluation datasets on model upgrades, load testing before launch.

The core architecture — `DatabaseManager` abstraction, LangGraph pipeline, `QueryCache`, `ConversationManager` — is already designed for production. The demo versions of each component have clear, documented upgrade paths that require changing configuration or swapping implementations, not rewriting the agent logic.
