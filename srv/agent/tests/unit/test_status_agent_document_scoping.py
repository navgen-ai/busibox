"""Regression tests for status-agent document scoping."""

from types import SimpleNamespace

import pytest

from app.agents.base_agent import AgentContext
from app.agents.status_agent import StatusAssistantAgent, StatusUpdateAgent


@pytest.mark.asyncio
async def test_status_assistant_uses_busibox_projects_docs_only() -> None:
    agent = StatusAssistantAgent()
    context = AgentContext(recent_messages=[{"role": "user", "content": "list all projects"}])
    result = SimpleNamespace(documents=[
        {"id": "appbuilder-projects-id", "name": "busibox-appbuilder-projects", "sourceApp": "busibox-appbuilder"},
        {"id": "status-projects-id", "name": "busibox-projects-projects", "sourceApp": "busibox-projects"},
        {"id": "status-tasks-id", "name": "busibox-projects-tasks", "sourceApp": "busibox-projects"},
    ])

    steps = await agent._handle_list_docs_result(result, context)

    assert agent._doc_ids.get("projects") == "status-projects-id"
    assert agent._doc_ids.get("tasks") == "status-tasks-id"
    assert steps
    assert steps[0].tool == "query_data"
    assert steps[0].args.get("document_id") == "status-projects-id"


@pytest.mark.asyncio
async def test_status_assistant_bootstraps_when_only_foreign_docs_exist() -> None:
    agent = StatusAssistantAgent()
    context = AgentContext(recent_messages=[{"role": "user", "content": "create projects"}])
    result = SimpleNamespace(documents=[
        {"id": "appbuilder-projects-id", "name": "busibox-appbuilder-projects", "sourceApp": "busibox-appbuilder"},
        {"id": "appbuilder-tasks-id", "name": "busibox-appbuilder-tasks", "sourceApp": "busibox-appbuilder"},
    ])

    steps = await agent._handle_list_docs_result(result, context)

    assert len(steps) == 3
    assert all(step.tool == "create_data_document" for step in steps)


def test_status_update_uses_busibox_projects_docs_only() -> None:
    agent = StatusUpdateAgent()
    result = SimpleNamespace(documents=[
        {"id": "appbuilder-projects-id", "name": "busibox-appbuilder-projects", "sourceApp": "busibox-appbuilder"},
        {"id": "status-projects-id", "name": "busibox-projects-projects", "sourceApp": "busibox-projects"},
        {"id": "status-tasks-id", "name": "busibox-projects-tasks", "sourceApp": "busibox-projects"},
    ])

    steps = agent._handle_list_docs(result)

    assert agent._doc_ids.get("projects") == "status-projects-id"
    assert agent._doc_ids.get("tasks") == "status-tasks-id"
    assert steps
    assert steps[0].tool == "query_data"
    assert steps[0].args.get("document_id") == "status-projects-id"


def test_status_assistant_task_insert_uses_single_project_fallback() -> None:
    agent = StatusAssistantAgent()
    agent._doc_ids = {"projects": "projects-doc-id", "tasks": "tasks-doc-id"}
    agent._query_results = {"projects": {"records": [{"id": "project-row-id"}], "total": 1}}
    agent._extracted_data = {
        "projects": [{"name": "Peter 90 day plan"}],
        "_pending_tasks": [{"title": "Onboarding", "status": "todo", "priority": "high"}],
    }

    step = SimpleNamespace(args={"document_id": "projects-doc-id"})
    result = SimpleNamespace(record_ids=[])
    chained = agent._handle_insert_result(step, result, AgentContext())

    assert chained
    assert chained[0].tool == "insert_records"
    records = chained[0].args.get("records", [])
    assert len(records) == 1
    assert records[0]["projectId"] == "project-row-id"
    assert records[0]["title"] == "Onboarding"


def test_status_assistant_task_insert_skips_unmappable_tasks() -> None:
    agent = StatusAssistantAgent()
    agent._doc_ids = {"projects": "projects-doc-id", "tasks": "tasks-doc-id"}
    agent._query_results = {"projects": {"records": [], "total": 0}}
    agent._extracted_data = {
        "projects": [{"name": "Project A"}, {"name": "Project B"}],
        "_pending_tasks": [
            {"project_name": "Project A", "title": "Task 1", "status": "todo"},
            {"project_name": "Unknown Project", "title": "Task 2", "status": "todo"},
        ],
    }

    step = SimpleNamespace(args={"document_id": "projects-doc-id"})
    result = SimpleNamespace(record_ids=["id-a", "id-b"])
    chained = agent._handle_insert_result(step, result, AgentContext())

    assert chained
    records = chained[0].args.get("records", [])
    assert len(records) == 1
    assert records[0]["title"] == "Task 1"
    assert records[0]["projectId"] == "id-a"
