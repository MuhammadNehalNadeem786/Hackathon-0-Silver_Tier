# AI Employee - Silver Tier

**Silver Tier** adds external service integration to the Bronze Tier foundation. This release introduces Gmail monitoring and LinkedIn automation with a human-in-the-loop approval workflow. All actions require explicit approval before execution, ensuring safe autonomous operation.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    AI Employee System                     │
├─────────────────────────────────────────────────────────┤
│                                                           │
│  Gmail Watcher          LinkedIn Poster                  │
│  ┌──────────────┐       ┌──────────────────┐            │
│  │ Monitor Gmail│       │ Post to LinkedIn │            │
│  │ Create files │       │ Requires approval│            │
│  └──────┬───────┘       └────────┬─────────┘            │
│         │                        │                       │
│         ▼                        ▼                       │
│  ┌──────────────────────────────────────┐               │
│  │        Needs_Action Folder            │               │
│  │  EMAIL_*.md  │  POST_*.md            │               │
│  └──────────────┴──────────┬───────────┘               │
│                             │                            │
│                             ▼                            │
│                  ┌────────────────────┐                 │
│                  │  Orchestrator      │                 │
│                  │  (Qwen Code)       │                 │
│                  └────────┬───────────┘                 │
│                           │                              │
│              ┌────────────┴────────────┐                │
│              ▼                         ▼                 │
│  ┌──────────────────┐      ┌───────────────────┐       │
│  │ Pending_Approval │      │      Done          │       │
│  │ (HITL Pattern)   │      │  (Completed)       │       │
│  └────────┬─────────┘      └───────────────────┘       │
│           │                                             │
│           ▼ (User approves)                             │
│  ┌──────────────────┐                                   │
│  │     Approved      │                                   │
│  │  → Post to LI     │                                   │
│  └──────────────────┘                                   │
│                                                           │
└─────────────────────────────────────────────────────────┘
```

---

## Architecture Notes

**Qwen Code as the Brain:**
- The orchestrator is configured to use Qwen Code (`ai_agent = 'qwen'`)
- All AI functionality is implemented as Agent Skills
- Skills are defined in `.qwen/skills/` directory
- The orchestrator processes files using the configured AI agent

**Human-in-the-Loop:**
- LinkedIn posts require approval before publishing
- Approval files created in `Pending_Approval/`
- User moves to `Approved/` to authorize
- Orchestrator executes after approval

**Security:**
- Credentials never committed to version control
- `.env` file contains sensitive data
- OAuth tokens auto-managed and refreshable
- `DRY_RUN` mode for safe testing

---

## Silver Tier Checklist

From the hackathon document, Silver Tier requires:

- [x] All Bronze requirements
- [x] **Two or more Watcher scripts** (Gmail + LinkedIn)
- [x] **Automatically Post on LinkedIn** about business
- [x] **Qwen** reasoning loop that creates Plan.md files (via orchestrator)
- [x] **One working MCP server** for external action (email-mcp-server skill)
- [x] **Human-in-the-loop approval workflow** for sensitive actions
- [x] **Basic scheduling** via Task Scheduler (scripts ready)
- [x] **All AI functionality as Agent Skills** (skills defined in `.qwen/skills/`)

---

## File Structure

```
Autonomous FTEs/
├── .qwen/
│   └── skills/                    # AI Agent Skills
│
├── credentials.json               # Gmail OAuth client credentials
├── gmail_token.json               # Gmail OAuth token (auto-created)
├── .env                           # Environment variables (never commit!)
│
├── scripte/
│   ├── base_watcher.py            # Base class for all watchers
│   ├── gmail_watcher.py           # Gmail monitoring
│   ├── linkedin_poster.py         # LinkedIn posting automation
│   ├── orchestrator.py            # Main coordinator
│   ├── approval_manager.py        # HITL approval workflow
│   ├── requirements.txt           # Python dependencies
│   ├── setup_gmail.bat            # Gmail setup script
│   └── setup_linkedin.bat         # LinkedIn setup script
│
└── AI_Employee_Vault/
    ├── Inbox/                     # Incoming tasks
    ├── Needs_Action/              # Items requiring action
    │   ├── EMAIL_*.md             # Gmail action files
    │   └── POST_*.md              # LinkedIn post drafts
    ├── Pending_Approval/          # Awaiting human approval
    │   └── LINKEDIN_*.md          # LinkedIn approval requests
    ├── Approved/                  # Approved actions
    ├── Done/                      # Completed tasks
    ├── Logs/                      # Activity logs
    └── Dashboard.md               # Status overview
```

---

## Quick Start

### 1. Install Dependencies
```bash
cd scripte
pip install -r requirements.txt
playwright install chromium
```

### 2. Setup Gmail
```bash
setup_gmail.bat
```
- Opens browser for Gmail OAuth authorization
- Saves token to `gmail_token.json` (in parent directory)
- Creates action files in `AI_Employee_Vault/Needs_Action/`

### 3. Setup LinkedIn
```bash
setup_linkedin.bat
```
Or manually edit `.env` file in parent directory:
```env
LINKEDIN_EMAIL=your_email@example.com
LINKEDIN_PASSWORD=your_password
DRY_RUN=true  # Change to false when ready
```

### 4. Run Orchestrator
```bash
python orchestrator.py --vault "AI_Employee_Vault" --watch
```

---

## Components

### Base Watcher (`base_watcher.py`)
- Base class providing common functionality for all watchers
- File system monitoring utilities
- Logging and error handling

### Gmail Watcher (`gmail_watcher.py`)
- Monitors Gmail every 2 minutes for unread/important emails
- Creates `.md` action files in `Needs_Action/`
- Persists processed email IDs across restarts

### LinkedIn Poster (`linkedin_poster.py`)
- Monitors `Needs_Action` for `POST_*.md` files
- Creates approval requests in `Pending_Approval/`
- Posts to LinkedIn after human approval

### Orchestrator (`orchestrator.py`)
- Manages both watchers
- Processes files through approval workflow
- Uses Qwen Code as the AI brain

### Approval Manager (`approval_manager.py`)
- Human-in-the-loop (HITL) approval workflow
- Manages file transitions between directories

---

## Commands

**Important:** All commands must be run from the `scripte` directory after running `cd scripte`

**Recommended:** Use the orchestrator to run both Gmail and LinkedIn automation together:

| Component | Command |
|-----------|---------|
| **Orchestrator (both services)** | `python orchestrator.py --vault "AI_Employee_Vault" --watch` |
| Orchestrator (one-time run) | `python orchestrator.py --vault "AI_Employee_Vault" --once` |

**Individual service commands (for testing/debugging):**

| Component | Command |
|-----------|---------|
| Gmail (once) | `python gmail_watcher.py --vault "AI_Employee_Vault" --credentials credentials.json --once` |
| Gmail (continuous) | `python gmail_watcher.py --vault "AI_Employee_Vault" --credentials credentials.json` |
| LinkedIn (dry run) | `python linkedin_poster.py --vault "AI_Employee_Vault" --dry-run` |
| LinkedIn (live) | `python linkedin_poster.py --vault "AI_Employee_Vault"` |

**Note:** `credentials.json` should be placed in the parent directory (`Autonomous FTEs/`), not in `scripte/`.

**Pro tip:** Instead of running Gmail and LinkedIn watchers separately, just run the orchestrator once. It will:
- Monitor Gmail for incoming emails
- Watch for LinkedIn post drafts
- Process files through the approval workflow
- Handle both services automatically

---

## Test Workflow

### Test Gmail
1. Send yourself an email, mark as important
2. Run from `scripte/`:
```bash
python gmail_watcher.py --vault "AI_Employee_Vault" --credentials credentials.json --once
```
3. Check: `../AI_Employee_Vault/Needs_Action/EMAIL_*.md`

### Test LinkedIn
1. Create `../AI_Employee_Vault/Needs_Action/POST_test.md`:
```markdown
---
type: post
title: "Test Post"
hashtags: "#Test"
---
This is a test post.
```
2. Run orchestrator:
```bash
python orchestrator.py --vault "AI_Employee_Vault" --watch
```
3. Move `../AI_Employee_Vault/Pending_Approval/LINKEDIN_*.md` → `../AI_Employee_Vault/Approved/`
4. Post publishes automatically

---

## Environment Variables (`.env`)

Place this file in the parent directory (`Autonomous FTEs/`)

| Variable | Default | Required |
|----------|---------|----------|
| `LINKEDIN_EMAIL` | - | For LinkedIn |
| `LINKEDIN_PASSWORD` | - | For LinkedIn |
| `DRY_RUN` | `true` | No |
| `WATCHER_CHECK_INTERVAL` | `120` | No |

**Security Note:** Never commit `.env` to version control. Add it to `.gitignore`.

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Gmail OAuth fails | Delete `gmail_token.json`, re-run setup, verify Gmail API enabled |
| LinkedIn login fails | Check `.env` credentials, keep `headless=false` for 2FA |
| No action files | Check `../AI_Employee_Vault/Logs/` folder, verify vault path |
| Dependencies fail | `pip install --upgrade pip`, install Visual C++ Build Tools (Windows) |
| "File not found" errors | Ensure you're running commands from `scripte/` directory |
| `credentials.json` not found | Place file in `Autonomous FTEs/` parent directory |

---

## Directory Path Reference

| Location | Path |
|----------|------|
| Scripts | `./scripte/` |
| Vault | `./AI_Employee_Vault/` |
| Credentials | `./credentials.json` |
| Gmail Token | `./gmail_token.json` |
| Environment | `./.env` |
| Agent Skills | `./.qwen/skills/` |

---

## Next Steps (Gold Tier)

- [ ] WhatsApp Watcher
- [ ] Facebook/Instagram posting
- [ ] Twitter/X posting
- [ ] Ralph Wiggum loop
- [ ] Weekly CEO Briefing
- [ ] Odoo integration

---

## Version History

| Version | Status | Date | Features |
|---------|--------|------|----------|
| Silver | ✅ Complete | Current | Gmail Watcher + LinkedIn Auto Poster |
| Gold | 🚧 Planned | TBD | Multi-platform + Integration |

---

## Support

For issues or questions:

- Check `../AI_Employee_Vault/Logs/` folder for detailed error messages
- Verify all dependencies are installed
- Ensure `.env` file has correct credentials in parent directory
- Run commands with `--help` for additional options
- Make sure you're in the `scripte/` directory before running commands
