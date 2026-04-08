# Gmail Watcher Skill

## Overview
Monitors Gmail for unread/important emails and creates action files in Needs_Action folder.

## Triggers
- Unread emails marked as important by Gmail
- Emails with keywords: 'urgent', 'invoice', 'payment', 'asap'
- New emails from VIP contacts

## Input
- Gmail API (external)

## Output
- `AI_Employee_Vault/Needs_Action/EMAIL_*.md` files

## Usage
```bash
python scripte/gmail_watcher.py --vault PATH --credentials CREDENTIALS.json
```

## Configuration
See `settings.json` for check intervals and email limits.

## Dependencies
- google-api-python-client
- google-auth-httplib2
- google-auth-oauthlib

## Setup
1. Enable Gmail API in Google Cloud Console
2. Create OAuth 2.0 credentials (Desktop app)
3. Download credentials JSON
4. Set `GMAIL_CREDENTIALS_PATH` in `.env`
