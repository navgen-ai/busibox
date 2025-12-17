"""
Enhanced workflow execution engine with full support for:
- Condition steps with branching
- Human-in-loop approval steps
- Parallel step execution
- Loop/iteration steps
- Usage tracking and guardrails
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.agents.core import BusiboxDeps
from app.clients.busibox import BusiboxClient
from app.models.domain import (
    WorkflowDefinition,
    WorkflowExecution,
    StepExecution,
    AgentDefinition,
)
from app.schemas.auth import Principal
from app.services.agent_registry import agent_registry
from app.services.token_service import get_or_exchange_token
from app.workflows.engine import (
    WorkflowExecutionError,
    GuardrailsExceededError,
    UsageLimits,
    _resolve_value,
    _resolve_args,
    _evaluate_condition,
    _estimate_cost,
)

logger = logging.getLogger(__name__)


async def execute_step(
    session: AsyncSession,
    execution: WorkflowExecution,
    step: Dict[str, Any],
    context: Dict[str, Any],
    usage_limits: UsageLimits,
    busibox_client: BusiboxClient,
    principal: Principal,
) -> Any:
    """
    Execute a single workflow step.
    
    Args:
        session: Database session
        execution: WorkflowExecution record
        step: Step definition
        context: Execution context
        usage_limits: Usage tracking and limits
        busibox_client: Busibox API client
        principal: User principal
        
    Returns:
        Step output
        
    Raises:
        WorkflowExecutionError: If step execution fails
    """
    step_id = step.get("id", "unknown")
    step_type = step.get("type")
    
    # Create step execution record
    step_exec = StepExecution(
        execution_id=execution.id,
        step_id=step_id,
        status="running",
        started_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    session.add(step_exec)
    await session.commit()
    
    try:
        # Check guardrails before starting
        usage_limits.check_before_step(step_type)
        
        result = None
        
        if step_type == "tool":
            result = await _execute_tool_step(step, context, busibox_client, usage_limits)
        
        elif step_type == "agent":
            result = await _execute_agent_step(
                session, step, context, busibox_client, principal, usage_limits
            )
        
        elif step_type == "condition":
            result = await _execute_condition_step(step, context)
        
        elif step_type == "human":
            result = await _execute_human_step(session, execution, step, context)
        
        elif step_type == "parallel":
            result = await _execute_parallel_step(
                session, execution, step, context, usage_limits, busibox_client, principal
            )
        
        elif step_type == "loop":
            result = await _execute_loop_step(
                session, execution, step, context, usage_limits, busibox_client, principal
            )
        
        else:
            raise WorkflowExecutionError(f"Unknown step type: {step_type}")
        
        # Update step execution
        step_exec.status = "completed"
        step_exec.output_data = result if isinstance(result, dict) else {"result": result}
        step_exec.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        step_exec.duration_seconds = (
            step_exec.completed_at - step_exec.started_at
        ).total_seconds()
        
        # Copy usage from limits
        usage = usage_limits.get_usage_dict()
        step_exec.usage_requests = usage["requests"]
        step_exec.usage_input_tokens = usage["input_tokens"]
        step_exec.usage_output_tokens = usage["output_tokens"]
        step_exec.usage_tool_calls = usage["tool_calls"]
        step_exec.estimated_cost_dollars = usage["estimated_cost_dollars"]
        
        await session.commit()
        
        logger.info(f"Step {step_id} completed successfully")
        return result
        
    except Exception as e:
        # Update step execution with error
        step_exec.status = "failed"
        step_exec.error = str(e)
        step_exec.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        step_exec.duration_seconds = (
            step_exec.completed_at - step_exec.started_at
        ).total_seconds()
        await session.commit()
        
        logger.error(f"Step {step_id} failed: {str(e)}", exc_info=True)
        raise


async def _execute_tool_step(
    step: Dict[str, Any],
    context: Dict[str, Any],
    busibox_client: BusiboxClient,
    usage_limits: UsageLimits,
) -> Any:
    """Execute a tool call step."""
    tool_name = step.get("tool")
    args = step.get("tool_args", {})
    resolved_args = _resolve_args(args, context)
    
    logger.info(f"Executing tool {tool_name} with args: {resolved_args}")
    
    # Call tool via Busibox client
    if tool_name == "search":
        result = await busibox_client.search(**resolved_args)
    elif tool_name == "ingest":
        result = await busibox_client.ingest_document(**resolved_args)
    elif tool_name == "rag":
        result = await busibox_client.rag_query(**resolved_args)
    else:
        raise WorkflowExecutionError(f"Unknown tool: {tool_name}")
    
    # Update usage
    usage_limits.update(tool_calls=1)
    
    return result


async def _execute_agent_step(
    session: AsyncSession,
    step: Dict[str, Any],
    context: Dict[str, Any],
    busibox_client: BusiboxClient,
    principal: Principal,
    usage_limits: UsageLimits,
) -> Any:
    """Execute an agent step."""
    agent_id = step.get("agent_id")
    agent_name = step.get("agent")  # Backward compatibility
    agent_prompt = step.get("agent_prompt", "")
    resolved_prompt = _resolve_value(agent_prompt, context)
    
    logger.info(f"Executing agent {agent_id or agent_name}")
    
    # Find agent by ID or name
    if agent_id:
        agent_def = await session.get(AgentDefinition, agent_id)
    else:
        stmt = select(AgentDefinition).where(
            AgentDefinition.name == agent_name,
            AgentDefinition.is_active.is_(True),
        )
        result = await session.execute(stmt)
        agent_def = result.scalars().first()
    
    if not agent_def:
        raise WorkflowExecutionError(f"Agent {agent_id or agent_name} not found")
    
    # Get agent from registry
    agent = agent_registry.get(agent_def.id)
    
    # Execute agent
    deps = BusiboxDeps(principal=principal, busibox_client=busibox_client)
    agent_result = await agent.run(str(resolved_prompt), deps=deps)
    
    # Extract output
    if hasattr(agent_result, "data") and hasattr(agent_result.data, "model_dump"):
        output = agent_result.data.model_dump()
    else:
        output = {"result": str(agent_result)}
    
    # Estimate usage (simplified - in reality would come from agent result)
    # For now, estimate based on typical usage
    usage_limits.update(
        requests=1,
        input_tokens=len(str(resolved_prompt)) // 4,  # Rough estimate
        output_tokens=len(str(output)) // 4,
        estimated_cost=_estimate_cost(agent_def.model, 1000, 500),
    )
    
    return output


async def _execute_condition_step(
    step: Dict[str, Any],
    context: Dict[str, Any],
) -> Dict[str, Any]:
    """Execute a condition step and determine next step."""
    condition = step.get("condition", {})
    
    # Evaluate condition
    passes = _evaluate_condition(condition, context)
    
    # Determine next step based on condition result
    next_step = condition.get("then_step") if passes else condition.get("else_step")
    
    logger.info(f"Condition evaluated to {passes}, next step: {next_step}")
    
    return {
        "condition_result": passes,
        "next_step": next_step,
    }


async def _execute_human_step(
    session: AsyncSession,
    execution: WorkflowExecution,
    step: Dict[str, Any],
    context: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Execute a human-in-loop step.
    
    This pauses workflow execution and waits for human approval.
    The workflow will be resumed via API call.
    """
    human_config = step.get("human_config", {})
    notification = _resolve_value(human_config.get("notification", ""), context)
    
    # Update execution status to awaiting_human
    execution.status = "awaiting_human"
    execution.current_step_id = step.get("id")
    execution.awaiting_approval_data = {
        "notification": notification,
        "options": human_config.get("options", []),
        "timeout_minutes": human_config.get("timeout_minutes"),
        "on_timeout": human_config.get("on_timeout", "continue"),
    }
    await session.commit()
    
    logger.info(f"Workflow paused for human approval: {notification}")
    
    # Return special marker that workflow is paused
    return {
        "status": "awaiting_human",
        "notification": notification,
    }


async def _execute_parallel_step(
    session: AsyncSession,
    execution: WorkflowExecution,
    step: Dict[str, Any],
    context: Dict[str, Any],
    usage_limits: UsageLimits,
    busibox_client: BusiboxClient,
    principal: Principal,
) -> List[Any]:
    """Execute multiple steps in parallel."""
    parallel_steps = step.get("parallel_steps", [])
    
    logger.info(f"Executing {len(parallel_steps)} steps in parallel")
    
    # Execute all steps concurrently
    tasks = [
        execute_step(
            session, execution, sub_step, context, usage_limits, busibox_client, principal
        )
        for sub_step in parallel_steps
    ]
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Check for failures
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error(f"Parallel step {i} failed: {str(result)}")
            raise WorkflowExecutionError(f"Parallel step {i} failed: {str(result)}")
    
    return list(results)


async def _execute_loop_step(
    session: AsyncSession,
    execution: WorkflowExecution,
    step: Dict[str, Any],
    context: Dict[str, Any],
    usage_limits: UsageLimits,
    busibox_client: BusiboxClient,
    principal: Principal,
) -> List[Any]:
    """Execute a loop step, iterating over items."""
    loop_config = step.get("loop_config", {})
    items_path = loop_config.get("items_path")
    item_variable = loop_config.get("item_variable", "item")
    loop_steps = loop_config.get("steps", [])
    
    # Resolve items array
    items = _resolve_value(items_path, context)
    if not isinstance(items, list):
        raise WorkflowExecutionError(f"Loop items_path must resolve to an array, got {type(items)}")
    
    logger.info(f"Executing loop over {len(items)} items")
    
    results = []
    for i, item in enumerate(items):
        # Create loop context with current item
        loop_context = {
            **context,
            item_variable: item,
            "loop_index": i,
            "loop_count": len(items),
        }
        
        # Execute all loop steps for this item
        for loop_step in loop_steps:
            step_result = await execute_step(
                session, execution, loop_step, loop_context, usage_limits, busibox_client, principal
            )
            loop_context[loop_step.get("id")] = step_result
        
        results.append(loop_context)
    
    return results


async def execute_enhanced_workflow(
    session: AsyncSession,
    principal: Principal,
    workflow_id: uuid.UUID,
    input_data: Dict[str, Any],
    scopes: List[str],
    purpose: str,
    override_guardrails: Optional[Dict[str, Any]] = None,
) -> WorkflowExecution:
    """
    Execute a workflow with full support for all step types.
    
    Args:
        session: Database session
        principal: User principal
        workflow_id: Workflow definition UUID
        input_data: Initial workflow input
        scopes: Required scopes
        purpose: Purpose for token exchange
        override_guardrails: Optional guardrails to override workflow defaults
        
    Returns:
        WorkflowExecution record
        
    Raises:
        WorkflowExecutionError: If workflow execution fails
    """
    # Load workflow definition
    workflow = await session.get(WorkflowDefinition, workflow_id)
    if not workflow:
        raise WorkflowExecutionError(f"Workflow {workflow_id} not found")
    
    if not workflow.is_active:
        raise WorkflowExecutionError(f"Workflow {workflow.name} is not active")
    
    # Create execution record
    execution = WorkflowExecution(
        workflow_id=workflow_id,
        status="running",
        trigger_source="manual",
        input_data=input_data,
        started_at=datetime.now(timezone.utc).replace(tzinfo=None),
        created_by=principal.sub,
    )
    session.add(execution)
    await session.commit()
    await session.refresh(execution)
    
    logger.info(f"Starting workflow {workflow.name} (ID: {workflow_id}), execution {execution.id}")
    
    # Initialize guardrails
    guardrails = override_guardrails or workflow.guardrails
    usage_limits = UsageLimits(guardrails)
    
    # Initialize execution context
    context = {
        "input": input_data,
        "workflow": {"id": str(workflow_id), "name": workflow.name},
    }
    
    try:
        # Get or exchange token
        token_response = await get_or_exchange_token(
            session=session,
            principal=principal,
            scopes=scopes,
            purpose=purpose,
        )
        
        # Create Busibox client
        busibox_client = BusiboxClient(
            search_url="http://search-api:8003",
            ingest_url="http://ingest-api:8002",
            bearer_token=token_response.access_token,
        )
        
        # Execute steps with branching support
        current_step_idx = 0
        steps_by_id = {step["id"]: step for step in workflow.steps}
        
        while current_step_idx < len(workflow.steps):
            step = workflow.steps[current_step_idx]
            step_id = step.get("id")
            
            # Update current step
            execution.current_step_id = step_id
            await session.commit()
            
            # Execute step
            result = await execute_step(
                session, execution, step, context, usage_limits, busibox_client, principal
            )
            
            # Store result in context
            context[step_id] = result
            execution.step_outputs[step_id] = result
            
            # Check if workflow is paused for human approval
            if isinstance(result, dict) and result.get("status") == "awaiting_human":
                logger.info(f"Workflow paused at step {step_id} for human approval")
                await session.commit()
                return execution
            
            # Handle branching for condition steps
            if step.get("type") == "condition" and isinstance(result, dict):
                next_step_id = result.get("next_step")
                if next_step_id and next_step_id in steps_by_id:
                    # Jump to specified step
                    current_step_idx = workflow.steps.index(steps_by_id[next_step_id])
                    continue
            
            # Move to next step
            current_step_idx += 1
        
        # Workflow completed successfully
        execution.status = "completed"
        execution.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        execution.duration_seconds = (
            execution.completed_at - execution.started_at
        ).total_seconds()
        
        # Update usage metrics
        usage = usage_limits.get_usage_dict()
        execution.usage_requests = usage["requests"]
        execution.usage_input_tokens = usage["input_tokens"]
        execution.usage_output_tokens = usage["output_tokens"]
        execution.usage_tool_calls = usage["tool_calls"]
        execution.estimated_cost_dollars = usage["estimated_cost_dollars"]
        
        logger.info(f"Workflow {workflow.name} completed successfully, execution {execution.id}")
        
    except GuardrailsExceededError as e:
        execution.status = "failed"
        execution.error = f"Guardrails exceeded: {str(e)}"
        execution.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        execution.duration_seconds = (
            execution.completed_at - execution.started_at
        ).total_seconds()
        logger.error(f"Workflow {workflow.name} failed: guardrails exceeded - {str(e)}")
        
    except WorkflowExecutionError as e:
        execution.status = "failed"
        execution.error = str(e)
        execution.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        execution.duration_seconds = (
            execution.completed_at - execution.started_at
        ).total_seconds()
        logger.error(f"Workflow {workflow.name} failed: {str(e)}")
        
    except Exception as e:
        execution.status = "failed"
        execution.error = f"Unexpected error: {str(e)}"
        execution.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        execution.duration_seconds = (
            execution.completed_at - execution.started_at
        ).total_seconds()
        logger.error(f"Workflow {workflow.name} failed unexpectedly: {str(e)}", exc_info=True)
    
    # Persist final state
    await session.commit()
    await session.refresh(execution)
    
    return execution
