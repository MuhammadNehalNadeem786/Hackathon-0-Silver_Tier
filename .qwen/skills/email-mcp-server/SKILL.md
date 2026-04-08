# Email MCP Server Skill

## Overview
Model Context Protocol server for sending, drafting, and searching emails via Gmail API.

## Triggers
- Approved email action in `/Approved` folder
- AI decides to send email reply
- Scheduled email campaigns

## Input
- `AI_Employee_Vault/Approved/` - Approved email actions
- Email MCP protocol commands

## Output
- Emails sent via Gmail API
- `AI_Employee_Vault/Done/` - Completed email actions
- `AI_Employee_Vault/Logs/` - Activity logs

## Usage
```bash
python scripte/email_mcp_server.py
```

## Configuration
See `settings.json` for rate limits and dry-run mode.

## MCP Methods
- `send_email(to, subject, body, attachment?)` - Send email
- `draft_email(to, subject, body)` - Create draft only
- `search_emails(query)` - Search inbox
- `reply_email(message_id, body)` - Reply to email

## Dependencies
- google-api-python-client
- google-auth-*

## Setup
1. Use same Gmail credentials as gmail-watcher
2. Enable Gmail API with send permissions
3. Set `DRY_RUN=true` for testing
