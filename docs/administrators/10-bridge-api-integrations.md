---
title: "Bridge API Integrations"
category: "administrator"
order: 10
description: "Configure Signal, Telegram, Discord, WhatsApp, and inbound email integrations for the Bridge service"
published: true
---

# Bridge API Integrations

Use this guide when configuring Bridge channel integrations from **Busibox Portal -> Admin -> Settings -> Bridge**.

The Bridge service can route messages between users and Busibox agents over:

- Signal
- Telegram
- Discord
- WhatsApp Cloud API
- Inbound email (IMAP polling)

## Before You Start

- Confirm `bridge` is deployed and healthy.
- Confirm `agent` and `authz` are reachable from the Bridge container.
- Use service-specific production tokens for production and separate tokens for staging.

## Signal

### What you need

- Registered Signal phone number for the bot
- Allowlist of sender phone numbers (optional)

### Where to get it

- Signal phone number is your registered bot identity in Signal CLI.
- Allowed phone numbers are user/ops phone numbers in E.164 format.

### Fields

- `signalEnabled`
- `signalPhoneNumber`
- `allowedPhoneNumbers` (comma-separated)

## Telegram Bot API

### What you need

- Telegram bot token
- Allowed chat IDs (optional)

### How to create

1. Open Telegram and message `@BotFather`.
2. Run `/newbot` and complete bot creation.
3. Copy the bot token.
4. Optional: collect chat IDs you want to allow.

### Fields

- `telegramEnabled`
- `telegramBotToken`
- `telegramPollInterval`
- `telegramPollTimeout`
- `telegramAllowedChatIds`

### Connectivity test

The **Test** button validates your token using Telegram `getMe`.

## Discord Bot API

### What you need

- Discord bot token
- Channel IDs to poll

### How to create

1. Open the Discord Developer Portal.
2. Create/select your application.
3. Add a Bot user.
4. Copy the bot token.
5. Enable developer mode in Discord client and copy target channel IDs.

### Fields

- `discordEnabled`
- `discordBotToken`
- `discordPollInterval`
- `discordChannelIds`

### Connectivity test

The **Test** button validates the token using Discord `GET /users/@me`.

## WhatsApp Cloud API

### What you need

- Meta app and WhatsApp Business setup
- Access token
- Verify token (for webhook verification)
- Phone Number ID
- Graph API version

### How to create

1. Create/select an app in Meta for Developers.
2. Enable WhatsApp product for the app.
3. Configure a phone number in WhatsApp Manager.
4. Generate/copy access token.
5. Set a verify token for webhook challenge flow.
6. Copy the phone number ID.

### Fields

- `whatsappEnabled`
- `whatsappVerifyToken`
- `whatsappAccessToken`
- `whatsappPhoneNumberId`
- `whatsappApiVersion`
- `whatsappAllowedPhoneNumbers`

### Connectivity test

The **Test** button validates access against Graph API for the configured phone number ID.

## Inbound Email (IMAP)

### What you need

- IMAP host/port
- Mailbox user/password
- Folder name (`INBOX` by default)
- Optional sender allowlist

### Fields

- `emailInboundEnabled`
- `imapHost`
- `imapPort`
- `imapUser`
- `imapPassword`
- `imapUseSsl`
- `imapFolder`
- `emailInboundPollInterval`
- `emailAllowedSenders`

## Cross-Channel Identity Binding

Use `channelUserBindings` JSON to map multiple channel identities to one stable user key.

Example:

```json
{
  "signal:+15551234567": "user-123",
  "telegram:123456789": "user-123"
}
```

This enables shared conversation continuity and memory across channels.

## Apply and Validate

1. Save Bridge settings in the Portal.
2. The portal triggers Bridge config apply/restart via Deploy API.
3. Run connectivity tests for each configured API.
4. Confirm Bridge health and channel flags in the status panel.

## Troubleshooting

- **Bridge test fails with 502**: verify token/key is valid and outbound internet access exists.
- **Health reachable but channel disabled**: check `...Enabled` boolean and restart Bridge.
- **No inbound IMAP messages**: verify mailbox permissions, folder name, and polling interval.
- **WhatsApp verify fails**: confirm exact verify token match and webhook mode is `subscribe`.
