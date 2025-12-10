# Implementation Plan: Production-Grade Agent Server with Pydantic AI

**Branch**: `005-i-want-to` | **Date**: 2025-01-08 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/005-i-want-to/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command. See `.specify/templates/commands/plan.md` for the execution workflow.

## Summary

Build a production-grade agent server using FastAPI and Pydantic AI that enables execution of AI agents with tool calls (search/ingest/RAG), dynamic agent/workflow/scorer management stored in PostgreSQL, OAuth2 token forwarding to Busibox services, cron scheduling for long-running tasks, and comprehensive testing (unit/integration/e2e) with OpenTelemetry observability. The system must support tiered execution limits (Simple: 30s/512MB, Complex: 5min/2GB, Batch: 30min/4GB) and provide SSE streaming for real-time run updates.

## Technical Context

**Language/Version**: Python 3.11+  
**Primary Dependencies**: FastAPI 0.115+, Pydantic AI 0.0.20+, SQLAlchemy 2.0+ (async), asyncpg, APScheduler 3.10+, python-jose (JWT), httpx, OpenTelemetry SDK, sse-starlette  
**Storage**: PostgreSQL 15+ (agent definitions, runs, tokens, workflows, scorers)  
**Testing**: pytest 8.3+ with pytest-asyncio, httpx AsyncClient for API tests, coverage reporting  
**Target Platform**: Linux server (LXC container agent-lxc on Proxmox), deployed via Ansible, systemd service management  
**Project Type**: Single backend API service  
**Performance Goals**: <5s response for simple agent queries, 100 concurrent executions, 95% success rate, <1s token exchange  
**Constraints**: Tiered execution limits (Simple: 30s/512MB, Complex: 5min/2GB, Batch: 30min/4GB), must forward auth to Busibox services (search/ingest/RAG)  
**Scale/Scope**: 10-100 users, 50+ agent definitions, 1000s of runs per day, 10+ concurrent scheduled jobs

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### I. Infrastructure as Code ✅ PASS
- Agent server code lives in `/srv/agent` (version controlled)
- Deployment managed via Ansible role `app_deployer` with local source sync
- Configuration externalized in `.env` files and Ansible group_vars
- No manual SSH fixes—changes go through code and Ansible

### II. Service Isolation & Role-Based Security ✅ PASS
- Agent server runs in dedicated `agent-lxc` container (10.96.200.202)
- Enforces JWT validation via Busibox JWKS
- Forwards scoped downstream tokens to search/ingest/RAG services
- PostgreSQL RLS enforced at database layer (inherited from Busibox)

### III. Observability & Debuggability ✅ PASS
- `/health` endpoint required (FR-037)
- Structured logging with OpenTelemetry tracing (FR-036, FR-038)
- All agent executions, tool calls, and errors logged (FR-036)
- Run records persist input/output/events/timestamps (FR-002)

### IV. Extensibility & Modularity ✅ PASS
- Dynamic agent/tool/workflow definitions stored in DB (FR-007-FR-013, FR-024-FR-028)
- Tool registry pattern allows adding new tools without code changes
- Pydantic AI framework enables model provider flexibility
- Ansible role is idempotent for repeatable deployments

### V. Test-Driven Infrastructure ✅ PASS
- Comprehensive test requirements (FR-033-FR-035): unit, integration, e2e
- 90%+ test coverage mandated (SC-005)
- Health checks validated post-deployment (Makefile `test-agent` target)
- Database schema has DDL with clear structure

### VI. Documentation as Contract ✅ PASS
- README.md in `/srv/agent` documents setup and architecture
- API endpoints will have OpenAPI schema (Phase 1 contracts)
- Quickstart.md will provide working commands (Phase 1)
- Changes to agent/tool/workflow schemas documented in data-model.md

### VII. Simplicity & Pragmatism ✅ PASS
- Uses standard stack: FastAPI, PostgreSQL, Redis (via APScheduler)
- No custom service discovery—uses static IPs from Busibox
- No custom message queue—APScheduler for cron, async execution for runs
- Complexity justified: Dynamic definitions enable operational flexibility without redeployment

**Gate Status**: ✅ ALL GATES PASS - Proceed to Phase 0 research

## Project Structure

### Documentation (this feature)

```
specs/[###-feature]/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (/speckit.plan command)
├── data-model.md        # Phase 1 output (/speckit.plan command)
├── quickstart.md        # Phase 1 output (/speckit.plan command)
├── contracts/           # Phase 1 output (/speckit.plan command)
└── tasks.md             # Phase 2 output (/speckit.tasks command - NOT created by /speckit.plan)
```

### Source Code (repository root)

```
srv/agent/                      # Agent server root (deployed to agent-lxc)
├── app/                        # FastAPI application
│   ├── __init__.py
│   ├── main.py                 # FastAPI app, startup, middleware
│   ├── config/
│   │   ├── __init__.py
│   │   └── settings.py         # Pydantic Settings (env-based config)
│   ├── db/
│   │   ├── __init__.py
│   │   ├── session.py          # SQLAlchemy async session
│   │   └── schema.sql          # Initial DDL for tables
│   ├── models/
│   │   ├── __init__.py
│   │   ├── base.py             # SQLAlchemy Base
│   │   └── domain.py           # Agent/Tool/Workflow/Run/Token models
│   ├── schemas/
│   │   ├── __init__.py
│   │   ├── auth.py             # Principal, TokenExchange schemas
│   │   ├── definitions.py      # Agent/Tool/Workflow CRUD schemas
│   │   └── run.py              # Run request/response schemas
│   ├── auth/
│   │   ├── __init__.py
│   │   ├── tokens.py           # JWT validation, token exchange
│   │   └── dependencies.py     # FastAPI auth dependencies
│   ├── clients/
│   │   ├── __init__.py
│   │   └── busibox.py          # HTTP client for search/ingest/RAG APIs
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── core.py             # Core agents (chat, RAG) with Pydantic AI
│   │   └── dynamic_loader.py   # Load agents from DB
│   ├── workflows/
│   │   ├── __init__.py
│   │   └── baseline.py         # Baseline workflows (ingest-and-enrich)
│   ├── services/
│   │   ├── __init__.py
│   │   ├── agent_registry.py   # In-memory agent registry
│   │   ├── run_service.py      # Execute runs with token forwarding
│   │   ├── scheduler.py        # APScheduler for cron jobs
│   │   └── token_service.py    # Token caching and exchange
│   ├── api/
│   │   ├── __init__.py
│   │   ├── health.py           # Health check endpoint
│   │   ├── auth.py             # Token exchange endpoint
│   │   ├── agents.py           # Agent CRUD and list
│   │   ├── runs.py             # Run execution and retrieval
│   │   └── streams.py          # SSE streaming for runs
│   └── utils/
│       ├── __init__.py
│       └── logging.py          # Structured logging setup
├── tests/
│   ├── __init__.py
│   ├── conftest.py             # Pytest fixtures (DB, auth mocks)
│   ├── test_health.py          # Smoke test
│   ├── unit/
│   │   ├── test_auth.py        # JWT validation, token exchange
│   │   ├── test_agents.py      # Agent CRUD logic
│   │   ├── test_loader.py      # Dynamic loader
│   │   └── test_run_service.py # Run execution logic
│   ├── integration/
│   │   ├── test_api_agents.py  # Agent API endpoints
│   │   ├── test_api_runs.py    # Run API endpoints
│   │   └── test_api_streams.py # SSE streaming
│   └── e2e/
│       ├── test_agent_execution.py  # Full agent run with tools
│       └── test_scheduled_runs.py   # Cron scheduling
├── pyproject.toml              # Dependencies, pytest config
└── README.md                   # Setup, architecture, usage
```

**Structure Decision**: Single backend API service deployed to `/srv/agent` on agent-lxc container. FastAPI application with clear separation: models (DB), schemas (API), services (business logic), agents (Pydantic AI), api (routes). Tests organized by type (unit/integration/e2e) with comprehensive coverage.

## Complexity Tracking

*No constitution violations - this section is not applicable.*
