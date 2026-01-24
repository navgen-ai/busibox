"""
Deployment Service Authentication

Validates admin tokens by calling authz service.
"""

import httpx
import logging
from fastapi import HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from .config import config

logger = logging.getLogger(__name__)

security = HTTPBearer()


async def verify_admin_token(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> dict:
    """
    Verify that the request has a valid admin token.
    
    Calls authz service to validate token and check admin role.
    Returns the validated token payload.
    Raises HTTPException if invalid or not admin.
    """
    token = credentials.credentials
    
    try:
        async with httpx.AsyncClient() as client:
            # Call authz internal endpoint to validate token
            response = await client.get(
                f"{config.authz_url}/api/v1/auth/me",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0
            )
            
            if response.status_code == 401:
                raise HTTPException(status_code=401, detail="Invalid or expired token")
            
            if response.status_code != 200:
                logger.error(f"Auth validation failed: {response.status_code}")
                raise HTTPException(status_code=401, detail="Token validation failed")
            
            user_data = response.json()
            user_id = user_data.get('id') or user_data.get('sub')
            
            if not user_id:
                raise HTTPException(status_code=401, detail="Invalid token: missing user ID")
            
            # Check admin role via authz
            roles_response = await client.get(
                f"{config.authz_url}/api/v1/users/{user_id}/roles",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0
            )
            
            if roles_response.status_code != 200:
                logger.warning(f"Failed to get user roles: {roles_response.status_code}")
                raise HTTPException(status_code=403, detail="Failed to verify admin role")
            
            roles = roles_response.json()
            is_admin = any(
                r.get('name') == 'Admin' or r.get('role', {}).get('name') == 'Admin'
                for r in roles
            )
            
            if not is_admin:
                logger.warning(f"Non-admin user {user_id} attempted deployment operation")
                raise HTTPException(
                    status_code=403,
                    detail="Admin role required for deployment operations"
                )
            
            logger.info(f"Admin user {user_id} authenticated for deployment")
            return {"user_id": user_id, "roles": roles, **user_data}
            
    except httpx.RequestError as e:
        logger.error(f"Failed to connect to authz service: {e}")
        raise HTTPException(
            status_code=503,
            detail="Authentication service unavailable"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Token validation error: {e}")
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")
