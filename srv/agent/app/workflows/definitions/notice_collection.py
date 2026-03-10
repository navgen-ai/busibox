"""
Notice Collection Workflow Definition.

A multi-step workflow that:
1. Loads active sites from data-api
2. For each site, loads its scraping configuration
3. Uses playwright_browser to fetch the page
4. Uses the notice-collector agent to parse and store notices
5. Summarizes the collection run

Configuration options (passed via input_data):
- site_ids: Optional list of specific site IDs to collect from (default: all active)
- document_id_sites: UUID of the sites data document
- document_id_configs: UUID of the site-configs data document
- document_id_notices: UUID of the notices data document
- document_id_runs: UUID of the collection-runs data document
"""

from typing import Any, Dict, List, Optional


NOTICE_COLLECTION_WORKFLOW_DEFINITION: Dict[str, Any] = {
    "name": "notice-collection-workflow",
    "description": "Collect public notices from configured sites using scraping strategies",
    "steps": [
        {
            "id": "load_sites",
            "type": "tool",
            "tool": "query_data",
            "tool_args": {
                "document_id": "$.input.document_id_sites",
                "where": {"status": {"$ne": "disabled"}},
                "limit": 500,
            },
            "description": "Load active sites from data-api",
        },
        {
            "id": "collect_loop",
            "type": "loop",
            "loop_config": {
                "items_path": "$.load_sites.records",
                "item_variable": "current_site",
                "max_iterations": 50,
                "steps": [
                    {
                        "id": "load_config",
                        "type": "tool",
                        "tool": "query_data",
                        "tool_args": {
                            "document_id": "$.input.document_id_configs",
                            "where": {"site_id": "$.current_site.id"},
                            "limit": 1,
                        },
                        "description": "Load scraping config for current site",
                    },
                    {
                        "id": "check_has_config",
                        "type": "condition",
                        "condition": {
                            "field": "$.load_config.total",
                            "operator": "gt",
                            "value": 0,
                            "then_step": "scrape_site",
                            "else_step": None,
                        },
                        "description": "Skip sites without scraping config",
                    },
                    {
                        "id": "scrape_site",
                        "type": "tool",
                        "tool": "playwright_browser",
                        "tool_args": {
                            "url": "$.current_site.url",
                            "extract_links": True,
                            "max_content_length": 20000,
                        },
                        "description": "Fetch page content with Playwright",
                    },
                    {
                        "id": "extract_notices",
                        "type": "agent",
                        "agent": "notice-collector",
                        "agent_prompt": """Extract notices from this page content using the scraping configuration.

Site: $.current_site.entity ($.current_site.url)
Category: $.current_site.category

Scraping Config:
$.load_config.records

Page Content:
$.scrape_site.content

Page Links:
$.scrape_site.links

Store new notices in document: $.input.document_id_notices
Use source_site_id: $.current_site.id

Deduplicate against existing notices by permit_number.""",
                        "description": "Parse and store notices from scraped content",
                    },
                ],
            },
            "description": "Collect notices from each active site",
        },
        {
            "id": "summarize",
            "type": "agent",
            "agent": "notice-collector",
            "agent_prompt": """Summarize the collection run results.

Sites processed: $.collect_loop
Input configuration: $.input

Provide a brief summary of:
- How many sites were processed
- How many new notices were found
- Any errors or issues encountered""",
            "description": "Generate collection run summary",
        },
    ],
    "guardrails": {
        "request_limit": 200,
        "tool_calls_limit": 500,
        "total_tokens_limit": 200000,
        "max_cost_dollars": 10.0,
        "timeout_seconds": 600,
    },
    "trigger": {
        "type": "manual",
        "allowed_types": ["manual", "api", "scheduled"],
    },
}


def create_notice_collection_workflow(
    site_ids: Optional[List[str]] = None,
    custom_guardrails: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Create a notice collection workflow definition with custom configuration.

    Args:
        site_ids: Optional list of specific site IDs to collect from
        custom_guardrails: Override default guardrails

    Returns:
        Workflow definition dict
    """
    import copy
    workflow = copy.deepcopy(NOTICE_COLLECTION_WORKFLOW_DEFINITION)

    if site_ids:
        # Modify the load_sites step to filter by specific IDs
        for step in workflow["steps"]:
            if step["id"] == "load_sites":
                step["tool_args"]["where"]["id"] = {"$in": site_ids}

    if custom_guardrails:
        workflow["guardrails"].update(custom_guardrails)

    return workflow


def get_default_input_data(
    document_id_sites: str,
    document_id_configs: str,
    document_id_notices: str,
    document_id_runs: str,
    site_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Get default input_data for executing the notice collection workflow.

    Args:
        document_id_sites: UUID of the sites data document
        document_id_configs: UUID of the site-configs data document
        document_id_notices: UUID of the notices data document
        document_id_runs: UUID of the collection-runs data document
        site_ids: Optional list of specific site IDs

    Returns:
        Input data dict for workflow execution
    """
    return {
        "document_id_sites": document_id_sites,
        "document_id_configs": document_id_configs,
        "document_id_notices": document_id_notices,
        "document_id_runs": document_id_runs,
        "site_ids": site_ids,
    }
