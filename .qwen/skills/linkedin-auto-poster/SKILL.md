# LinkedIn Auto Poster Skill

## Overview
Automatically posts business updates to LinkedIn using Playwright browser automation with human-in-the-loop approval.

## Triggers
- `POST_*.md` files in `/Needs_Action`
- Scheduled daily business update post
- Keyword 'post to linkedin' in message

## Input
- `AI_Employee_Vault/Needs_Action/POST_*.md`
- Business goals and content templates

## Output
- LinkedIn posts (after approval)
- `AI_Employee_Vault/Pending_Approval/LINKEDIN_*.md`
- `AI_Employee_Vault/Done/` - Completed posts
- `AI_Employee_Vault/Logs/` - Engagement metrics

## Usage
```bash
python scripte/linkedin_poster.py --vault PATH
```

## Configuration
See `settings.json` for post limits and approval requirements.

## Dependencies
- playwright>=1.40.0

## Setup
1. Install Playwright: `pip install playwright`
2. Install browsers: `playwright install chromium`
3. Set `LINKEDIN_EMAIL` and `LINKEDIN_PASSWORD` in `.env`
4. Test with `DRY_RUN=true` first

## Rate Limits
- Maximum 3 posts per day
- Requires human approval before posting
