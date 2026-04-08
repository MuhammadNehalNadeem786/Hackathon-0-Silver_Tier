# WhatsApp Watcher Skill

## Overview
Monitors WhatsApp Web for messages containing important keywords using Playwright browser automation.

## Triggers
- Messages with keywords: 'urgent', 'invoice', 'payment', 'help', 'asap'
- Messages from VIP contacts
- Unread messages in important chats

## Input
- WhatsApp Web (via Playwright)

## Output
- `AI_Employee_Vault/Needs_Action/WHATSAPP_*.md` files

## Usage
```bash
python scripte/whatsapp_watcher.py --vault PATH --session SESSION_PATH
```

## Configuration
See `settings.json` for keywords and check intervals.

## Dependencies
- playwright>=1.40.0

## Setup
1. Install Playwright: `pip install playwright`
2. Install browsers: `playwright install chromium`
3. Set `WHATSAPP_SESSION_PATH` in `.env`
4. First run will require manual WhatsApp Web QR scan

## Warning
Be aware of WhatsApp's terms of service. Use at your own risk.
