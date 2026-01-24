# Busibox Deployment Service

A standalone service for deploying apps, provisioning databases, and configuring nginx routing.

## Overview

The deployment service handles:
- **App Deployment** via Ansible playbooks
- **Database Provisioning** via SSH to pg-lxc
- **Nginx Configuration** via SSH to nginx container
- **Real-time Logs** via WebSocket

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Authz Container                          │
│                                                             │
│  ┌─────────────────┐          ┌──────────────────────┐     │
│  │   Authz API     │          │  Deployment Service  │     │
│  │   Port 8010     │◄────────►│     Port 8011        │     │
│  │                 │  Token    │                      │     │
│  │  - Auth         │  Valid.   │  - Deploy Apps       │     │
│  │  - Users        │          │  - Provision DBs     │     │
│  │  - Roles        │          │  - Configure Nginx   │     │
│  └─────────────────┘          └──────────────────────┘     │
│                                        │                    │
└────────────────────────────────────────┼────────────────────┘
                                         │
           ┌────────────────────────────┬┴────────────────┐
           │                            │                  │
           ▼                            ▼                  ▼
    ┌─────────────┐            ┌───────────────┐   ┌─────────────┐
    │   pg-lxc    │            │   apps-lxc    │   │   nginx     │
    │  PostgreSQL │            │   Apps Host   │   │   Routing   │
    └─────────────┘            └───────────────┘   └─────────────┘
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/deployment/deploy` | POST | Deploy an app |
| `/api/v1/deployment/deploy/{id}/status` | GET | Get deployment status |
| `/api/v1/deployment/deploy/{id}/logs` | WS | Stream deployment logs |
| `/api/v1/deployment/deployments` | GET | List recent deployments |
| `/api/v1/deployment/health` | GET | Service health check |
| `/health/live` | GET | Liveness probe |
| `/health/ready` | GET | Readiness probe |

## Authentication

All endpoints (except health checks) require admin authentication:

```bash
curl -X POST http://localhost:8011/api/v1/deployment/deploy \
  -H "Authorization: Bearer <admin-jwt-token>" \
  -H "Content-Type: application/json" \
  -d '{"manifest": {...}, "config": {...}}'
```

The service validates tokens by calling the authz service at `/api/v1/auth/me`.

## Configuration

Environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `DEPLOY_PORT` | 8011 | Service port |
| `DEBUG` | false | Enable debug logging |
| `AUTHZ_URL` | http://localhost:8010 | Authz service URL |
| `ANSIBLE_DIR` | /root/busibox/provision/ansible | Ansible directory |
| `POSTGRES_HOST` | 10.96.200.202 | PostgreSQL host |
| `POSTGRES_PORT` | 5432 | PostgreSQL port |
| `POSTGRES_ADMIN_USER` | postgres | Admin user |
| `POSTGRES_ADMIN_PASSWORD` | (required) | Admin password |
| `APPS_CONTAINER_IP` | 10.96.200.201 | Production apps IP |
| `APPS_CONTAINER_IP_STAGING` | 10.96.201.201 | Staging apps IP |
| `SSH_KEY_PATH` | /root/.ssh/id_rsa | SSH private key |
| `NGINX_HOST` | 10.96.200.200 | Nginx container IP |
| `NGINX_CONFIG_DIR` | /etc/nginx/sites-available/apps | Nginx config path |
| `NGINX_ENABLED_DIR` | /etc/nginx/sites-enabled | Nginx enabled path |
| `RATE_LIMIT_MINUTES` | 5 | Rate limit per app |

## Running Locally

```bash
cd srv/deploy

# Install dependencies
pip install -r requirements.txt

# Set required environment variables
export AUTHZ_URL=http://localhost:8010
export POSTGRES_ADMIN_PASSWORD=your-password

# Run the service
python -m uvicorn src.main:app --reload --port 8011
```

## Docker

```bash
docker build -t busibox-deploy .
docker run -p 8011:8011 \
  -e AUTHZ_URL=http://authz:8010 \
  -e POSTGRES_ADMIN_PASSWORD=secret \
  busibox-deploy
```

## Deployment Request

```json
{
  "manifest": {
    "name": "My App",
    "id": "my-app",
    "version": "1.0.0",
    "description": "My awesome app",
    "icon": "Calculator",
    "defaultPath": "/myapp",
    "defaultPort": 3010,
    "healthEndpoint": "/api/health",
    "buildCommand": "npm run build",
    "startCommand": "npm start",
    "appMode": "prisma",
    "database": {
      "required": true,
      "preferredName": "myapp",
      "schemaManagement": "prisma"
    },
    "requiredEnvVars": ["LITELLM_API_KEY"]
  },
  "config": {
    "githubRepoOwner": "owner",
    "githubRepoName": "repo",
    "githubBranch": "main",
    "githubToken": "ghp_xxx",
    "environment": "production",
    "secrets": {
      "LITELLM_API_KEY": "sk-xxx"
    }
  }
}
```

## Deployment Flow

1. **Validate** - Check admin token, rate limit
2. **Provision Database** - Create PostgreSQL database if required
3. **Deploy App** - Run Ansible app_deployer playbook
4. **Configure Nginx** - Write config, validate, reload
5. **Complete** - Return success with app URL

## WebSocket Logs

Connect to `/api/v1/deployment/deploy/{id}/logs?token=<jwt>`:

```javascript
const ws = new WebSocket('ws://localhost:8011/api/v1/deployment/deploy/abc/logs?token=xxx');

ws.onmessage = (event) => {
  const status = JSON.parse(event.data);
  console.log(`${status.progress}% - ${status.currentStep}`);
  console.log('Logs:', status.logs);
};
```

## Rate Limiting

- 1 deployment per app per 5 minutes (configurable)
- Prevents deployment spam
- Returns 429 if exceeded

## Files

```
srv/deploy/
├── Dockerfile
├── requirements.txt
├── pytest.ini
├── README.md
├── src/
│   ├── __init__.py
│   ├── main.py          # FastAPI app
│   ├── config.py        # Configuration
│   ├── auth.py          # Token validation via authz
│   ├── models.py        # Pydantic models
│   ├── routes.py        # API endpoints
│   ├── database.py      # PostgreSQL provisioning
│   ├── ansible_executor.py  # Ansible execution
│   └── nginx_config.py  # Nginx configuration
└── tests/
    └── (tests go here)
```

## Integration with AI Portal

```typescript
import { deployApp, connectDeploymentLogs } from '@/lib/deployment-service-client';

const result = await deployApp(manifest, config);
const cleanup = connectDeploymentLogs(result.deploymentId, onStatus, onError);
```

## Troubleshooting

### Token Validation Failed
- Check authz service is running at `AUTHZ_URL`
- Verify token is valid admin token

### Database Provisioning Failed
- Check `POSTGRES_ADMIN_PASSWORD` is set
- Verify SSH access to pg-lxc
- Check PostgreSQL is running

### Ansible Failed
- Check ansible-playbook is installed
- Verify `ANSIBLE_DIR` path is correct
- Check inventory files exist

### Nginx Config Failed
- Verify SSH access to nginx host
- Check config directories exist
- Review nginx error logs
