"""
Deployment Service Routes

API endpoints for app deployment operations.
"""

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
import asyncio
import logging
import uuid
from datetime import datetime
from typing import Dict
from .models import (
    DeployRequest,
    DeploymentResult,
    DeploymentStatus,
    DeploymentConfig,
    BusiboxManifest
)
from .auth import verify_admin_token
from .database import provision_database
from .ansible_executor import AnsibleExecutor, get_container_ip
from .nginx_config import NginxConfigurator
from .config import config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/deployment", tags=["deployment"])

# In-memory deployment status storage
# TODO: Move to Redis or database for persistence
deployment_statuses: Dict[str, DeploymentStatus] = {}

# WebSocket connections for log streaming
active_connections: Dict[str, list[WebSocket]] = {}

# Rate limiting: track last deployment per app
last_deployment_times: Dict[str, datetime] = {}


def check_rate_limit(app_id: str) -> None:
    """Check if app can be deployed (rate limiting)"""
    if app_id in last_deployment_times:
        elapsed = (datetime.utcnow() - last_deployment_times[app_id]).total_seconds()
        limit_seconds = config.rate_limit_per_app_minutes * 60
        if elapsed < limit_seconds:
            remaining = int(limit_seconds - elapsed)
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded. Try again in {remaining} seconds."
            )


@router.post("/deploy", response_model=DeploymentResult)
async def deploy_app(
    request: DeployRequest,
    token_payload: dict = Depends(verify_admin_token)
):
    """
    Deploy an app from manifest.
    
    Requires admin authentication.
    """
    manifest = request.manifest
    deploy_config = request.config
    
    # Check rate limit
    check_rate_limit(manifest.id)
    
    deployment_id = str(uuid.uuid4())
    
    logger.info(f"Starting deployment {deployment_id} for {manifest.name} by user {token_payload.get('user_id')}")
    
    # Update rate limit
    last_deployment_times[manifest.id] = datetime.utcnow()
    
    # Initialize status
    status = DeploymentStatus(
        deploymentId=deployment_id,
        status='pending',
        progress=0,
        currentStep='Initializing deployment',
        startedAt=datetime.utcnow(),
        logs=[]
    )
    deployment_statuses[deployment_id] = status
    active_connections[deployment_id] = []
    
    # Start deployment in background
    asyncio.create_task(execute_deployment(deployment_id, manifest, deploy_config))
    
    return DeploymentResult(
        deploymentId=deployment_id,
        status='pending',
        appUrl=f"https://yourdomain.com{manifest.defaultPath}"
    )


async def execute_deployment(
    deployment_id: str,
    manifest: BusiboxManifest,
    deploy_config: DeploymentConfig
):
    """Execute deployment asynchronously"""
    
    status = deployment_statuses[deployment_id]
    
    try:
        # Step 1: Provision database if required
        if manifest.database and manifest.database.required:
            status.status = 'provisioning_db'
            status.progress = 10
            status.currentStep = 'Provisioning database'
            status.logs.append(f"[{datetime.utcnow().isoformat()}] Provisioning database...")
            await broadcast_status(deployment_id)
            
            db_result = await provision_database(manifest)
            
            if not db_result.success:
                raise Exception(f"Database provisioning failed: {db_result.error}")
            
            status.logs.append(f"[{datetime.utcnow().isoformat()}] Database provisioned: {db_result.databaseName}")
            await broadcast_status(deployment_id)
            
            # Add DATABASE_URL to secrets
            if db_result.databaseUrl:
                deploy_config.secrets['DATABASE_URL'] = db_result.databaseUrl
        
        # Step 2: Deploy via Ansible
        status.status = 'deploying'
        status.progress = 30
        status.currentStep = 'Deploying application'
        status.logs.append(f"[{datetime.utcnow().isoformat()}] Starting Ansible deployment...")
        await broadcast_status(deployment_id)
        
        executor = AnsibleExecutor()
        success, logs = await executor.deploy_app(
            manifest,
            deploy_config,
            deploy_config.secrets.get('DATABASE_URL')
        )
        
        for log in logs:
            status.logs.append(f"[{datetime.utcnow().isoformat()}] {log}")
        await broadcast_status(deployment_id)
        
        if not success:
            raise Exception("Ansible deployment failed")
        
        status.progress = 70
        await broadcast_status(deployment_id)
        
        # Step 3: Configure nginx
        status.status = 'configuring_nginx'
        status.progress = 80
        status.currentStep = 'Configuring nginx'
        status.logs.append(f"[{datetime.utcnow().isoformat()}] Configuring nginx routing...")
        await broadcast_status(deployment_id)
        
        container_ip = await get_container_ip(manifest.id, deploy_config.environment)
        
        configurator = NginxConfigurator()
        nginx_success, nginx_msg = await configurator.configure_app(manifest, container_ip)
        
        status.logs.append(f"[{datetime.utcnow().isoformat()}] {nginx_msg}")
        await broadcast_status(deployment_id)
        
        if not nginx_success:
            raise Exception(f"Nginx configuration failed: {nginx_msg}")
        
        # Step 4: Complete
        status.status = 'completed'
        status.progress = 100
        status.currentStep = 'Deployment completed'
        status.completedAt = datetime.utcnow()
        status.logs.append(f"[{datetime.utcnow().isoformat()}] ✅ {manifest.name} deployed successfully at {manifest.defaultPath}")
        await broadcast_status(deployment_id)
        
        logger.info(f"Deployment {deployment_id} completed successfully")
        
    except Exception as e:
        logger.error(f"Deployment {deployment_id} failed: {e}")
        status.status = 'failed'
        status.currentStep = 'Deployment failed'
        status.error = str(e)
        status.completedAt = datetime.utcnow()
        status.logs.append(f"[{datetime.utcnow().isoformat()}] ❌ Error: {str(e)}")
        await broadcast_status(deployment_id)


async def broadcast_status(deployment_id: str):
    """Broadcast status update to all connected WebSocket clients"""
    if deployment_id not in active_connections:
        return
    
    status = deployment_statuses.get(deployment_id)
    if not status:
        return
    
    # Serialize status
    status_dict = status.model_dump()
    status_dict['startedAt'] = status.startedAt.isoformat()
    if status.completedAt:
        status_dict['completedAt'] = status.completedAt.isoformat()
    
    # Remove disconnected clients and broadcast
    to_remove = []
    for i, websocket in enumerate(active_connections[deployment_id]):
        try:
            await websocket.send_json(status_dict)
        except Exception as e:
            logger.warning(f"Failed to send to websocket: {e}")
            to_remove.append(i)
    
    # Remove failed connections
    for i in reversed(to_remove):
        active_connections[deployment_id].pop(i)


@router.get("/deploy/{deployment_id}/status", response_model=DeploymentStatus)
async def get_deployment_status(
    deployment_id: str,
    token_payload: dict = Depends(verify_admin_token)
):
    """Get deployment status"""
    status = deployment_statuses.get(deployment_id)
    
    if not status:
        raise HTTPException(status_code=404, detail="Deployment not found")
    
    return status


@router.websocket("/deploy/{deployment_id}/logs")
async def deployment_logs_websocket(
    websocket: WebSocket,
    deployment_id: str
):
    """
    WebSocket endpoint for streaming deployment logs.
    
    Authentication can be done via query parameter: ?token=<jwt>
    """
    await websocket.accept()
    
    # Get token from query parameter
    token = websocket.query_params.get('token')
    if not token:
        await websocket.send_json({"error": "Authentication required. Pass token as query parameter."})
        await websocket.close(code=4001)
        return
    
    # Validate token via authz service
    try:
        import httpx
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{config.authz_url}/api/v1/auth/me",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0
            )
            if response.status_code != 200:
                await websocket.send_json({"error": "Invalid token"})
                await websocket.close(code=4001)
                return
    except Exception as e:
        logger.error(f"WebSocket auth failed: {e}")
        await websocket.send_json({"error": "Authentication failed"})
        await websocket.close(code=4001)
        return
    
    if deployment_id not in deployment_statuses:
        await websocket.send_json({"error": "Deployment not found"})
        await websocket.close(code=4004)
        return
    
    # Add to active connections
    if deployment_id not in active_connections:
        active_connections[deployment_id] = []
    active_connections[deployment_id].append(websocket)
    
    logger.info(f"WebSocket connected for deployment {deployment_id}")
    
    try:
        # Send current status immediately
        status = deployment_statuses[deployment_id]
        status_dict = status.model_dump()
        status_dict['startedAt'] = status.startedAt.isoformat()
        if status.completedAt:
            status_dict['completedAt'] = status.completedAt.isoformat()
        await websocket.send_json(status_dict)
        
        # Keep connection alive until deployment completes or client disconnects
        while True:
            try:
                # Wait for messages from client (ping/pong or close)
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                if data == 'ping':
                    await websocket.send_text('pong')
            except asyncio.TimeoutError:
                # Send heartbeat
                try:
                    await websocket.send_json({"heartbeat": True})
                except:
                    break
            
            # Check if deployment is complete
            status = deployment_statuses.get(deployment_id)
            if status and status.status in ['completed', 'failed']:
                status_dict = status.model_dump()
                status_dict['startedAt'] = status.startedAt.isoformat()
                if status.completedAt:
                    status_dict['completedAt'] = status.completedAt.isoformat()
                await websocket.send_json({"final": True, "status": status_dict})
                break
            
    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for deployment {deployment_id}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        # Remove from active connections
        if deployment_id in active_connections:
            try:
                active_connections[deployment_id].remove(websocket)
            except ValueError:
                pass


@router.get("/health")
async def deployment_service_health():
    """Health check endpoint"""
    return {
        "service": "deployment",
        "status": "healthy",
        "port": config.port,
        "activeDeployments": len([
            s for s in deployment_statuses.values() 
            if s.status not in ['completed', 'failed']
        ])
    }


@router.get("/deployments")
async def list_deployments(
    token_payload: dict = Depends(verify_admin_token)
):
    """List recent deployments"""
    deployments = []
    for status in deployment_statuses.values():
        status_dict = status.model_dump()
        status_dict['startedAt'] = status.startedAt.isoformat()
        if status.completedAt:
            status_dict['completedAt'] = status.completedAt.isoformat()
        deployments.append(status_dict)
    
    # Sort by startedAt descending
    deployments.sort(key=lambda x: x['startedAt'], reverse=True)
    return deployments[:50]  # Last 50 deployments
