# Plan Generator Skill

## Overview
Automatically generates execution plans (Plan.md files) for complex multi-step tasks detected in Needs_Action.

## Triggers
- Any file in `/Needs_Action`
- Complex multi-step task detected
- User requests planning mode

## Input
- `AI_Employee_Vault/Needs_Action/*.md` - Action files
- AI Agent (Qwen/Claude) reasoning

## Output
- `AI_Employee_Vault/Plans/PLAN_*.md` - Execution plans with checkboxes

## Usage
Built into orchestrator AI processing. No separate script needed.

## Configuration
See `settings.json` for plan generation options.

## Plan Structure
```markdown
---
created: 2026-04-03T10:00:00Z
status: active
source_file: EMAIL_*.md
ai_agent: qwen
---

# Plan: Process [Task]

## Objective
Clear description of what needs to be done

## Steps
- [ ] Step 1: Read and analyze
- [ ] Step 2: Determine actions
- [ ] Step 3: Execute or request approval
- [ ] Step 4: Move to Done
- [ ] Step 5: Update Dashboard

## Notes
Additional context and decisions
```

## Integration
- Called by `orchestrator._process_staged_file()`
- Creates plan before executing actions
- Tracks completion status
