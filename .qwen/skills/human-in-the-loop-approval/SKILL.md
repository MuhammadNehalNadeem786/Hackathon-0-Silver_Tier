# Human-in-the-Loop Approval Skill

## Overview
Manages approval workflow for sensitive actions like payments, emails to new contacts, and social media posts.

## Triggers
- Sensitive actions: payments, emails, social posts
- Actions exceeding auto-approve thresholds
- AI explicitly requests approval

## Input
- `AI_Employee_Vault/Pending_Approval/*.md` - Approval requests

## Output
- `AI_Employee_Vault/Approved/*.md` - Approved actions
- `AI_Employee_Vault/Rejected/*.md` - Rejected actions with reasons

## Usage
```bash
python scripte/approval_manager.py --vault PATH
```

## Configuration
See `settings.json` for approval thresholds and timeouts.

## Approval Workflow

1. AI detects sensitive action needed
2. Creates approval request in `/Pending_Approval/`
3. Human reviews the request
4. Human moves file to `/Approved/` or `/Rejected/`
5. Orchestrator processes approved files
6. Rejected files are logged with reasons

## Approval Request Format
```markdown
---
type: approval_request
action: email
to: client@example.com
subject: Invoice #123
created: 2026-04-03T10:00:00Z
status: pending
---

## Action Details
Description of what will be done

## To Approve
Move this file to /Approved folder

## To Reject
Move this file to /Rejected folder with reason
```

## Auto-Approve Thresholds
- Email: Known contacts only
- Payment: $0 (always require approval)
- Social Post: Draft only (always require approval)
