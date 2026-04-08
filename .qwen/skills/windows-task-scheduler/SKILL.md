# Windows Task Scheduler Skill

## Overview
Integrates AI Employee with Windows Task Scheduler for automated daily briefings, periodic processing, and scheduled tasks.

## Triggers
- Daily at 8:00 AM: Generate CEO briefing
- Daily at 9:00 AM: Post to LinkedIn (if posts queued)
- Every hour: Process Needs_Action folder
- Weekly Sunday 10 PM: Generate weekly audit

## Input
- Windows Task Scheduler
- Scheduled task definitions

## Output
- `AI_Employee_Vault/Briefings/YYYY-MM-DD_daily_briefing.md`
- Processed inbox and action files
- Weekly audit reports

## Usage
Setup Windows Task Scheduler tasks:

```powershell
# Daily Briefing at 8:00 AM
schtasks /create /tn "AI_Employee_Daily_Briefing" /tr "python orchestrator.py --vault PATH --briefing" /sc daily /st 08:00

# Process Inbox every hour
schtasks /create /tn "AI_Employee_Process_Inbox" /tr "python orchestrator.py --vault PATH --process" /sc hourly

# LinkedIn Post at 9:00 AM
schtasks /create /tn "AI_Employee_LinkedIn" /tr "python orchestrator.py --vault PATH --linkedin" /sc daily /st 09:00
```

## Configuration
See `settings.json` for scheduled task definitions.

## Scheduled Tasks

| Task | Trigger | Command | Output |
|------|---------|---------|--------|
| Daily Briefing | Daily 8:00 AM | `orchestrator.py --briefing` | Briefings/ |
| Process Inbox | Every hour | `orchestrator.py --process` | Process Needs_Action/ |
| LinkedIn Post | Daily 9:00 AM | `orchestrator.py --linkedin` | Pending_Approval/ |
| Weekly Audit | Sunday 10:00 PM | `orchestrator.py --audit` | Briefings/weekly_*.md |

## Windows Task Scheduler Setup

1. Open Task Scheduler (`taskschd.msc`)
2. Create Basic Task
3. Set trigger (daily/hourly/weekly)
4. Action: Start a program
   - Program: `python`
   - Arguments: `orchestrator.py --vault "PATH" --briefing`
   - Start in: `S:\Personal AI Employee\Autonomous FTEs\scripte`
5. Check "Run whether user is logged on or not"
6. Check "Run with highest privileges"

## Dependencies
- Windows OS
- Python in system PATH
- All other skills configured
