# Bridge Service

A multi-channel communication bridge that connects various messaging platforms to the Busibox Agent API for AI-powered interactions.

**Status**: Under Development

## Overview

This service provides a unified interface for AI interactions across multiple communication channels:
- **Signal**: Secure messaging (currently implemented)
- **Email**: SMTP/IMAP integration (planned)
- **WhatsApp**: WhatsApp Business API (planned)
- **Webhooks**: Generic webhook endpoints (planned)

## Architecture

```
External Channel → Channel Adapter → Bridge Service → Agent API
    ↑                                       ↓
    └───────────────────────────────────────┘
```

### Current Implementation (Signal)

```
Signal App → signal-cli-rest-api → Bridge Service → Agent API
    ↑                                   ↓
    └───────────────────────────────────┘
```

## Quick Start

### Prerequisites

1. signal-cli-rest-api running with a registered phone number (for Signal channel)
2. Agent API accessible
3. Service account credentials for Agent API

### Environment Variables

```bash
# Application
APP_NAME=bridge
ENVIRONMENT=production
LOG_LEVEL=INFO

# Signal channel configuration
SIGNAL_CLI_URL=http://localhost:8080
SIGNAL_PHONE_NUMBER=+12025551234

# Agent API configuration
AGENT_API_URL=http://agent-lxc:8000
AUTH_TOKEN_URL=http://authz-lxc:8010/oauth/token

# Zero Trust Authentication - Delegation Token
# Pre-issued delegation token for the signal-bot service account.
# Create via: POST /oauth/delegation/create with a valid admin session token
DELEGATION_TOKEN=eyJhbGciOiJSUzI1NiIs...

# Bot behavior (Signal-specific)
ENABLE_WEB_SEARCH=true
ENABLE_DOC_SEARCH=false
DEFAULT_MODEL=auto

# Rate limiting
RATE_LIMIT_MESSAGES=30
RATE_LIMIT_WINDOW=60

# Polling
POLL_INTERVAL=1.0

# Allowed phone numbers (comma-separated, empty = all)
ALLOWED_PHONE_NUMBERS=
```

### Running Locally

```bash
# Install dependencies
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Set environment variables
export SIGNAL_CLI_URL=http://localhost:8080
export AGENT_API_URL=http://localhost:8000
# ... other vars ...

# Run the service
python -m app.main
```

### Deployment

The service is deployed via Ansible as an optional, non-blocking component:

```bash
# Deploy bridge service (won't fail if host doesn't exist)
make bridge

# Or with specific inventory
make bridge INV=inventory/staging
```

## Signal Setup

### 1. Register Phone Number

After deployment, SSH to the bridge container and run:

```bash
/usr/local/bin/register-signal register +12025551234
```

Follow the SMS verification prompts.

### 2. Verify Registration

```bash
/usr/local/bin/register-signal status
```

### 3. Test Integration

```bash
# From Ansible directory
make test-bridge
```

## Adding New Channels

To add a new communication channel:

1. **Create Adapter**: Add a new adapter in `app/adapters/` (e.g., `email_adapter.py`, `whatsapp_adapter.py`)
2. **Implement Interface**: Follow the pattern from `signal_client.py`:
   - Poll for incoming messages
   - Format messages for Agent API
   - Send responses back through the channel
3. **Configuration**: Add channel-specific env vars to Ansible templates
4. **Register**: Update `app/main.py` to initialize and run the new adapter

## API Integration (Zero Trust)

The bridge service uses **delegation tokens** for Zero Trust authentication:

### 1. Create Delegation Token for Service Account

First, create a "signal-bot" user in AuthZ and issue a delegation token:

```bash
# Using an admin session token
curl -X POST http://authz-lxc:8010/oauth/delegation/create \
  -H "Authorization: Bearer ${ADMIN_SESSION_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "signal-bot-service",
    "scopes": ["agent.execute", "chat.write", "chat.read"],
    "expires_in_seconds": 94608000
  }'
```

This returns a long-lived delegation token for the service account.

### 2. Token Exchange for Service-Scoped Tokens

At runtime, the bridge service exchanges its delegation token for agent-api-scoped tokens:

```python
# Exchange delegation token for agent-api token
POST http://authz-lxc:8010/oauth/token
{
  "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
  "subject_token": "<delegation_token>",
  "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
  "audience": "agent-api",
  "scope": "agent.execute chat.write chat.read"
}

# Call Agent API with exchanged token
POST http://agent-lxc:8000/agents/{agent_id}/chat
Authorization: Bearer {agent_api_token}
{
  "message": "User message",
  "conversation_id": "optional-existing-id"
}
```

**Note**: No `client_id`/`client_secret` is used - the delegation token carries the service account's identity.

## Monitoring

### Check Service Status

```bash
systemctl status bridge
journalctl -u bridge -f
```

### Check Signal CLI

```bash
docker logs signal-cli-rest-api
curl http://localhost:8080/v1/about
```

### Status Script

```bash
/usr/local/bin/bridge-status
```

## Security

- Runs as dedicated `bridge` user
- Environment variables protected (mode 0600)
- OAuth2 authentication for API access
- Rate limiting per user
- Optional phone number whitelist

## Troubleshooting

### Service won't start

```bash
systemctl status bridge
journalctl -u bridge -n 50
```

### Signal messages not received

1. Check signal-cli-rest-api: `docker logs signal-cli-rest-api`
2. Verify registration: `/usr/local/bin/register-signal status`
3. Check bridge logs: `journalctl -u bridge -f`

### Authentication failures

1. Verify AuthZ service is running: `curl http://authz-lxc:8010/health/live`
2. Check delegation token is valid: Decode the JWT and verify it hasn't expired
3. Verify the service user exists and has appropriate roles in AuthZ
4. Check token exchange logs in AuthZ for detailed errors

### Rate limiting

Default: 30 messages per 60 seconds per user. Adjust in Ansible defaults:
- `signal_bot_rate_limit_messages`
- `signal_bot_rate_limit_window`

## Development

### Local Testing

```bash
# Start signal-cli-rest-api locally
docker run -d --name signal-cli-rest-api \
  -p 8080:8080 \
  -v ~/signal-cli-data:/home/.local/share/signal-cli \
  bbernhard/signal-cli-rest-api:latest

# Run bridge service
python -m app.main
```

### Code Structure

```
app/
├── __init__.py
├── main.py              # Entry point, polling loop
├── config.py            # Configuration management
├── signal_client.py     # Signal channel adapter
├── agent_client.py      # Agent API client
└── adapters/            # Future: email, whatsapp, webhook adapters
```

## Future Enhancements

- [ ] Email channel (SMTP/IMAP)
- [ ] WhatsApp Business API integration
- [ ] Generic webhook endpoints
- [ ] Message queue for async processing
- [ ] Conversation persistence
- [ ] Multi-agent routing based on channel/user
- [ ] Rich media support (images, files)
- [ ] Typing indicators
- [ ] Read receipts
