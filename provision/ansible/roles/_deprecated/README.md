# Deprecated Roles

This directory contains Ansible roles that are no longer actively maintained or have been replaced by newer implementations.

## Deprecated Roles

### deploywatch

**Deprecated as of:** January 2026
**Replaced by:** Deploy API (`srv/deploy/`)

The `deploywatch` role was originally designed to monitor GitHub releases and automatically deploy updates. This functionality has been consolidated into the Deploy API service, which provides:

- Manual and automated deployments via REST API
- Real-time deployment logs via WebSocket/SSE
- Version checking and update notifications
- Integration with AI Portal admin UI

**Migration Guide:**

1. Remove any `deploywatch` role references from playbooks
2. Use AI Portal Admin UI for app deployments
3. For programmatic deployments, use the Deploy API endpoints:
   - `POST /api/v1/deployment/deploy` - Deploy an app
   - `POST /api/v1/deployment/version-check` - Check for updates
   - `GET /api/v1/deployment/deploy/{id}/status` - Get deployment status

## Do Not Use

These roles are kept for historical reference only. Do not use them in new deployments.
