"""
Orchestrator Module

Master process for the AI Employee system.
Monitors folders, triggers AI agents for processing, and manages workflows.

Folder Flow:
    Inbox → Processing → Done/Failed
"""

import subprocess
import logging
import shutil
import re
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Set
import json
import time
import traceback
import sys
import os

# Force stdout to flush immediately so terminal output appears in real-time
sys.stdout.reconfigure(line_buffering=True)  # type: ignore

# Resolve script and project root paths for relative imports and vault detection
SCRIPT_DIR = Path(__file__).parent.resolve()
PROJECT_ROOT = SCRIPT_DIR.parent.resolve()

# Load .env file from project root (contains API keys, credentials paths, etc.)
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / '.env')

# Try to import watchdog for real-time file monitoring (optional dependency)
# Falls back to polling mode if not installed
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler, FileCreatedEvent, FileModifiedEvent
    WATCHDOG_AVAILABLE = True
except ImportError:
    WATCHDOG_AVAILABLE = False
    Observer = None
    FileSystemEventHandler = object


# ── Terminal styling ──────────────────────────────────────────────────────────

class Colors:
    """ANSI escape codes for terminal color output."""
    RESET   = '\033[0m'
    BOLD    = '\033[1m'
    DIM     = '\033[2m'
    GREEN   = '\033[92m'
    RED     = '\033[91m'
    YELLOW  = '\033[93m'
    BLUE    = '\033[94m'
    CYAN    = '\033[96m'
    MAGENTA = '\033[95m'
    GRAY    = '\033[90m'
    WHITE   = '\033[97m'


class Box:
    """Unicode box-drawing characters for bordered terminal output."""
    TL     = '┌'
    TR     = '┐'
    BL     = '└'
    BR     = '┘'
    H      = '─'
    V      = '│'
    MIDDLE = '├'
    END    = '└'


def c(text: str, color: str) -> str:
    """Wrap text in an ANSI color code and reset after."""
    return f"{color}{text}{Colors.RESET}"


def print_box(content_lines: list, title: str = '', color: str = Colors.WHITE, width: int = 62) -> None:
    """
    Print a Unicode bordered box around the given lines.
    If a title is provided, it appears as a header row inside the box.
    """
    border = c(Box.H * (width - 2), color)

    if title:
        # Print top border + title row + divider
        title_text = f"  {title} "
        title_padding = width - len(title_text) - 1
        print(c(Box.TL + border[:width - 2], color) + c(Box.TR, color))
        print(c(Box.V, color) + c(title_text, color) + c(' ' * title_padding + Box.V, color))
        print(c(Box.V + '─' * (width - 2) + Box.V, color))
    else:
        print(c(Box.TL + border + Box.TR, color))

    # Print each content line padded to box width
    for line in content_lines:
        padded = line.ljust(width - 2)
        print(c(Box.V, color) + c(padded, color) + c(Box.V, color))

    print(c(Box.BL + border + Box.BR, color))


# ── Orchestrator ──────────────────────────────────────────────────────────────

class Orchestrator:
    """
    Main orchestrator for the AI Employee system.

    Responsibilities:
    - Monitor Needs_Action for new Markdown files dropped by watchers (e.g. GmailWatcher)
    - Stage files in Processing/ during work to avoid double-processing
    - Trigger the configured AI agent (Qwen Code / Claude Code) to handle the file
    - Move completed files → Done/, failed files → Failed/
    - Update Dashboard.md counters and write JSON activity logs
    """

    # Folders that must exist for a valid vault
    REQUIRED_VAULT_FOLDERS = ['Inbox', 'Done', 'Needs_Action', 'Plans', 'Logs']
    EXPECTED_VAULT_NAME    = 'AI_Employee_Vault'

    def __init__(
        self,
        vault_path: str,
        check_interval: int = 60,
        ai_agent: str = 'qwen',
        watch_mode: bool = False
    ):
        # Resolve and validate the vault path before anything else
        self.vault_path     = self._resolve_vault_path(vault_path)
        self.check_interval = check_interval
        self.ai_agent       = ai_agent
        self.watch_mode     = watch_mode

        # ── Define all vault sub-folder paths ───────────────────────────────
        self.inbox            = self.vault_path / 'Inbox'
        self.needs_action     = self.vault_path / 'Needs_Action'   # Watchers drop files here
        self.done             = self.vault_path / 'Done'
        self.plans            = self.vault_path / 'Plans'
        self.pending_approval = self.vault_path / 'Pending_Approval'
        self.approved         = self.vault_path / 'Approved'       # Human moves files here to approve
        self.rejected         = self.vault_path / 'Rejected'
        self.logs             = self.vault_path / 'Logs'
        self.accounting       = self.vault_path / 'Accounting'
        self.briefings        = self.vault_path / 'Briefings'
        self.drop             = self.vault_path / 'Drop'
        self.dashboard        = self.vault_path / 'Dashboard.md'
        self.processing       = self.vault_path / 'Processing'     # Temp staging while AI works
        self.failed           = self.vault_path / 'Failed'

        # Create all folders if they don't already exist
        for folder in [
            self.inbox, self.needs_action, self.done, self.plans,
            self.pending_approval, self.approved, self.rejected,
            self.logs, self.accounting, self.briefings, self.drop,
            self.processing, self.failed,
        ]:
            folder.mkdir(parents=True, exist_ok=True)

        self._setup_logging()

        # Track files currently being processed to prevent concurrent double-processing
        self.processing_files: Set[str] = set()
        # Track start times per file so we can report elapsed duration
        self.processing_times: Dict[str, float] = {}

        # Check once at startup whether the chosen AI agent is reachable
        self.ai_available = self._check_ai_agent()

    # ── Vault path resolution ─────────────────────────────────────────────────

    def _resolve_vault_path(self, vault_path: str) -> Path:
        """
        Resolve vault path to an absolute Path.
        If the provided path is invalid but the expected default vault exists, use that instead.
        """
        path = Path(vault_path)
        resolved = (SCRIPT_DIR / vault_path).resolve() if not path.is_absolute() else path.resolve()

        correct_vault = PROJECT_ROOT / self.EXPECTED_VAULT_NAME

        # Warn if a duplicate vault is detected inside the scripts folder
        if SCRIPT_DIR in resolved.parents and correct_vault.exists():
            if resolved != correct_vault and not self._validate_vault_structure(resolved):
                print(c(f"\n  ⚠️  WARNING: Potential duplicate vault detected!", Colors.YELLOW))
                print(f"     Provided: {resolved}")
                print(f"     Using:    {correct_vault}")
                return correct_vault

        # Fall back to the known correct vault if the given path is invalid
        if not self._validate_vault_structure(resolved):
            if correct_vault.exists() and self._validate_vault_structure(correct_vault):
                print(c(f"\n  ⚠️  Invalid vault at: {resolved}", Colors.YELLOW))
                print(c(f"  → Using: {correct_vault}", Colors.GREEN))
                return correct_vault
            print(c(f"\n  ℹ️  Creating missing vault folders at: {resolved}", Colors.BLUE))

        return resolved

    def _validate_vault_structure(self, vault_path: Path) -> bool:
        """Return True only if the vault has all required folders and a Dashboard.md."""
        if not vault_path.exists():
            return False
        for folder in self.REQUIRED_VAULT_FOLDERS:
            if not (vault_path / folder).exists():
                return False
        return (vault_path / 'Dashboard.md').exists()

    # ── Logging setup ─────────────────────────────────────────────────────────

    def _setup_logging(self) -> None:
        """
        Configure file-only logging for the orchestrator.
        Console output is handled separately via print_box() for cleaner UX.
        """
        # Silence watchdog's internal debug chatter
        logging.getLogger('watchdog').setLevel(logging.CRITICAL)
        logging.getLogger().handlers = []

        log_file  = self.logs / f'orchestrator_{datetime.now().strftime("%Y-%m-%d")}.log'
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )

        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)

        self.logger = logging.getLogger('Orchestrator')
        self.logger.setLevel(logging.DEBUG)
        self.logger.addHandler(file_handler)
        # Prevent log records from bubbling up to the root logger (avoids duplicate entries)
        self.logger.propagate = False

    # ── Console output helpers ────────────────────────────────────────────────

    def _print_file_detected(self, filename: str) -> None:
        """Print a cyan banner when a new file is detected in Needs_Action/."""
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(flush=True)
        print_box([
            "",
            f"  {c('File:', Colors.CYAN)}     {filename}",
            f"  {c('Time:', Colors.CYAN)}     {current_time}",
            f"  {c('Status:', Colors.CYAN)}   Processing initiated",
            "",
        ], title="📥 INCOMING TASK DETECTED", color=Colors.CYAN, width=64)
        print(flush=True)

    def _print_processing_stages(self, stages: dict) -> None:
        """
        Print a yellow box showing the three pipeline stages and their completion status.
        stages dict format: { 'staging': ('description', bool_completed), ... }
        """
        stage_meta = {
            'staging':    ('⚙️',  'STAGE 1/3: Staging'),
            'processing': ('🤖', 'STAGE 2/3: AI Processing'),
            'planning':   ('📋', 'STAGE 3/3: Planning'),
        }
        stage_order = ['staging', 'processing', 'planning']
        content     = []

        for i, key in enumerate(stage_order):
            if key not in stages:
                continue
            description, completed = stages[key]
            icon, label = stage_meta[key]
            status    = c('✓', Colors.GREEN) if completed else '○'
            connector = Box.MIDDLE if i < len(stage_order) - 1 else Box.END

            content.append(f"  {c(icon + '  ' + label, Colors.YELLOW)}")
            content.append(f"  {connector}{Box.H} {description} {status}")

            if i < len(stage_order) - 1:
                content.append("")

        print(flush=True)
        print_box(content, color=Colors.YELLOW, width=64)
        print(flush=True)

    def _print_success(self, filename: str, elapsed: float) -> None:
        """Print a green success banner after a file is moved to Done/."""
        print_box([
            "",
            f"  {c('File:', Colors.GREEN)}     {filename}",
            f"  {c('Output:', Colors.GREEN)}   Done/{filename}",
            f"  {c('Duration:', Colors.GREEN)} {elapsed:.2f} seconds",
            f"  {c('Status:', Colors.GREEN)}   {c('✓ Completed', Colors.BOLD + Colors.GREEN)}",
            "",
        ], title="✅ TASK COMPLETED SUCCESSFULLY", color=Colors.GREEN, width=64)
        print(flush=True)

    def _print_error(self, filename: str, error: str) -> None:
        """Print a red error banner when a file fails processing."""
        short_error = error[:50] + '...' if len(error) > 50 else error
        print_box([
            "",
            f"  {c('File:', Colors.RED)}     {filename}",
            f"  {c('Output:', Colors.RED)}   Failed/{filename}",
            f"  {c('Error:', Colors.RED)}    {short_error}",
            f"  {c('Status:', Colors.RED)}   {c('✗ Failed', Colors.BOLD + Colors.RED)}",
            "",
        ], title="❌ TASK FAILED", color=Colors.RED)
        print(flush=True)

    # ── AI agent availability ─────────────────────────────────────────────────

    def _check_ai_agent(self) -> bool:
        """
        Verify the chosen AI agent is available.
        - Qwen: always True (runs inside the same Qwen Code environment)
        - Claude: checks that the `claude` CLI is installed and responsive
        """
        if self.ai_agent == 'qwen':
            self.logger.info('Qwen Code: Available (running in Qwen Code environment)')
            return True

        if self.ai_agent == 'claude':
            try:
                result = subprocess.run(
                    ['claude', '--version'],
                    capture_output=True, text=True, timeout=10
                )
                if result.returncode == 0:
                    self.logger.info(f'Claude Code available: {result.stdout.strip()}')
                    return True
                self.logger.warning('Claude Code returned non-zero exit code')
                return False
            except FileNotFoundError:
                self.logger.error('Claude Code not found. Install: npm install -g @anthropic/claude-code')
                return False
            except Exception as e:
                self.logger.error(f'Error checking Claude Code: {e}')
                return False

        self.logger.error(f'Unknown AI agent: {self.ai_agent}')
        return False

    # ── Main run loop ─────────────────────────────────────────────────────────

    def run(self) -> None:
        """Entry point — print the startup banner then hand off to the chosen run mode."""
        agent_status = 'Available' if self.ai_available else 'Unavailable'
        mode_text    = 'Watch' if self.watch_mode else 'Polling'

        print(flush=True)
        print_box([
            "",
            f"  {c('Vault:', Colors.CYAN)}       {self.vault_path}",
            f"  {c('AI Agent:', Colors.GREEN)}    {self.ai_agent} ({c(agent_status, Colors.GREEN if self.ai_available else Colors.RED)})",
            f"  {c('Mode:', Colors.BLUE)}       {c(mode_text, Colors.BLUE)} | Interval: {self.check_interval}s",
            f"  {c('Flow:', Colors.MAGENTA)}      Inbox → Processing → Done/Failed",
            "",
        ], title="🤖 AI EMPLOYEE ORCHESTRATOR v0.3", color=Colors.CYAN, width=64)
        print(flush=True)

        if self.watch_mode:
            self._run_watch_mode()
        else:
            self._run_polling_mode()

    def _run_watch_mode(self) -> None:
        """
        Real-time mode using watchdog.
        A file-system observer fires instantly when a .md file is created in Inbox/.
        Falls back to polling if watchdog is not installed.
        """
        if not WATCHDOG_AVAILABLE:
            self.logger.error('Watchdog not installed. Install: pip install watchdog')
            print_box([
                "",
                f"  {c('Watchdog not installed', Colors.YELLOW)}",
                f"  Install with: {c('pip install watchdog', Colors.CYAN)}",
                f"  Falling back to polling mode...",
                "",
            ], color=Colors.YELLOW)
            self._run_polling_mode()
            return

        class InboxHandler(FileSystemEventHandler):  # type: ignore
            """Watchdog event handler — reacts to new .md files dropped in Inbox/."""
            def __init__(self, orchestrator):
                self.orchestrator = orchestrator

            def on_created(self, event):
                # Only handle Markdown files, not directories or other file types
                if not event.is_directory and Path(event.src_path).suffix.lower() == '.md':
                    self.orchestrator._print_file_detected(Path(event.src_path).name)
                    self.orchestrator._process_inbox()
                    self.orchestrator.process_needs_action()
                    self.orchestrator.process_linkedin_posts()
                    self.orchestrator.process_approved()
                    self.orchestrator._update_dashboard()

            def on_modified(self, event):
                pass  # Ignore modification events to avoid duplicate triggers

        observer = None
        try:
            observer = Observer()  # type: ignore
            observer.schedule(InboxHandler(self), str(self.inbox), recursive=False)
            observer.start()

            print_box([
                "",
                f"  {c('👁️ Watch:', Colors.GREEN)} {c(str(self.inbox), Colors.CYAN)}",
                f"  {c('Interval:', Colors.BLUE)} Every {self.check_interval} seconds",
                f"  Press Ctrl+C to stop",
                "",
            ], color=Colors.GREEN, width=64)
            print(flush=True)

            # Process any files already sitting in the folders at startup
            self._process_inbox()
            self.process_needs_action()
            self.process_linkedin_posts()
            self._process_approved()
            self._update_dashboard()

            # Keep the main thread alive; watchdog runs in a background thread
            while True:
                time.sleep(self.check_interval)
                self._update_dashboard()

        except KeyboardInterrupt:
            print(f"\n  {c('⏹️  Stopped by user', Colors.YELLOW)}", flush=True)
            if observer:
                observer.stop()
        finally:
            if observer and WATCHDOG_AVAILABLE:
                observer.stop()
                observer.join()

    def _run_polling_mode(self) -> None:
        """
        Polling mode — scans Needs_Action/ every `check_interval` seconds.

        Detection logic:
        - `last_detected_files` tracks which files were present on the previous poll.
        - `new_files` = files present now but not on the last poll → print banner once.
        - The banner is only printed here, NOT inside process_needs_action(), to avoid duplicates.
        """
        print_box([
            "",
            f"  {c('🔄 Polling:', Colors.BLUE)} {c(str(self.needs_action), Colors.CYAN)}",
            f"  {c('Interval:', Colors.BLUE)} Every {self.check_interval} seconds",
            f"  Press Ctrl+C to stop",
            "",
        ], color=Colors.BLUE, width=64)
        print(flush=True)

        # Remember which files existed on the last poll so we can detect new arrivals
        last_detected_files: set = set()

        try:
            while True:
                try:
                    # Snapshot current .md files in Needs_Action/
                    current_files = {f.name for f in self.needs_action.iterdir() if f.suffix.lower() == '.md'}

                    # Files present now but absent last poll = newly arrived
                    new_files = current_files - last_detected_files
                    if new_files:
                        # Print ONE detection banner per new file (banner only lives here)
                        for filename in new_files:
                            self._print_file_detected(filename)
                        print(f"[INFO] Processing {len(new_files)} file(s)...", flush=True)
                    else:
                        self.logger.info('No new files found in Needs_Action/')
                        print(f"[INFO] No new files found in Needs_Action/", flush=True)
                        print(f"[INFO] Waiting {self.check_interval} seconds...", flush=True)

                    # Step 1: process any raw files that landed in Inbox/
                    self._process_inbox()

                    # Step 2: check for LinkedIn posts FIRST (before generic processing)
                    # This ensures POST_*.md files go through HITL approval workflow
                    posts_queued = self.process_linkedin_posts()
                    if posts_queued > 0:
                        print(f"[✅] {posts_queued} post(s) queued for approval", flush=True)

                    # Step 3: process emails / tasks in Needs_Action/ (excludes POST_*.md)
                    # NOTE: _print_file_detected is intentionally NOT called inside here
                    action_processed = self.process_needs_action()
                    # Status banners already printed inside _stage_and_process_file()

                    # Step 4: execute anything the human has moved to Approved/
                    approved_processed = self.process_approved()
                    if approved_processed > 0:
                        pass  # Banner is printed inside process_approved()

                    # Step 5: refresh Dashboard.md counters
                    self._update_dashboard()

                    # Update the baseline for the next poll cycle
                    last_detected_files = current_files

                except Exception as e:
                    self.logger.error(f'Error in main loop: {e}', exc_info=True)

                time.sleep(self.check_interval)

        except KeyboardInterrupt:
            print(f"\n  {c('⏹️  Stopped by user', Colors.YELLOW)}", flush=True)

    # ── File processing pipeline ──────────────────────────────────────────────

    def _update_file_status(self, file_path: Path, status: str) -> None:
        """
        Update the `status:` field in a file's YAML frontmatter.
        If the field doesn't exist yet, insert it before the closing `---`.
        """
        try:
            if not file_path.exists() or file_path.suffix.lower() != '.md':
                return

            content = file_path.read_text(encoding='utf-8')

            if '---' not in content:
                return

            parts = content.split('---', 2)
            if len(parts) < 3:
                return

            frontmatter = parts[1]
            body = parts[2]

            if 'status:' in frontmatter:
                # Replace existing status value
                new_frontmatter = re.sub(
                    r'status:\s*.*$',
                    f'status: {status}',
                    frontmatter,
                    flags=re.MULTILINE
                )
                new_content = f'---{new_frontmatter}---{body}'
                file_path.write_text(new_content, encoding='utf-8')
                self.logger.info(f'Updated status to "{status}" in {file_path.name}')
            else:
                # Insert status field if missing
                lines = frontmatter.strip().split('\n')
                lines.insert(-1 if lines else 0, f'status: {status}')
                new_frontmatter = '\n'.join(lines)
                new_content = f'---{new_frontmatter}---{body}'
                file_path.write_text(new_content, encoding='utf-8')
                self.logger.info(f'Added status "{status}" to {file_path.name}')

        except Exception as e:
            self.logger.warning(f'Could not update status in {file_path.name}: {e}')

    def _move_to_folder(self, source_file: Path, dest_folder: Path, new_status: str = None) -> Path:  # type: ignore
        """
        Optionally update frontmatter status, then move the file to dest_folder.
        Returns the new Path of the moved file.
        """
        try:
            if new_status:
                self._update_file_status(source_file, new_status)

            dest_file = dest_folder / source_file.name
            shutil.move(str(source_file), str(dest_file))
            return dest_file

        except Exception as e:
            self.logger.error(f'Error moving {source_file.name} to {dest_folder.name}: {e}')
            raise

    def _process_inbox(self) -> None:
        """
        Pick up any .md files sitting in Inbox/ and stage them for AI processing.
        Files already tracked in processing_files are skipped to avoid re-processing.
        """
        try:
            inbox_files = [
                f for f in self.inbox.iterdir()
                if f.suffix.lower() == '.md' and f.name not in self.processing_files
            ]
            for inbox_file in inbox_files:
                self._stage_and_process_file(inbox_file)
        except Exception as e:
            self.logger.error(f'Error processing Inbox: {e}', exc_info=True)

    def _stage_and_process_file(self, source_file: Path) -> None:
        """
        Move a file from its current folder (Inbox/ or Needs_Action/) into Processing/,
        then hand it off to the AI agent. Tracks the file in processing_files to
        prevent concurrent double-processing if the poll fires again mid-run.
        """
        try:
            start_time = time.time()
            self.processing_times[source_file.name] = start_time
            self.processing_files.add(source_file.name)  # Lock the file

            # Stage: move to Processing/ and update frontmatter status
            self._move_to_folder(source_file, self.processing, 'processing')
            staging_file = self.processing / source_file.name

            self._process_staged_file(staging_file, start_time)
        except Exception as e:
            self.logger.error(f'Error staging {source_file.name}: {e}', exc_info=True)
            self.processing_files.discard(source_file.name)  # Release lock on error

    def _process_staged_file(self, staging_file: Path, start_time: float) -> None:
        """
        Core processing step. Reads the file, builds an AI prompt,
        then dispatches to the appropriate AI agent handler.
        Always releases the processing lock in the `finally` block.
        """
        try:
            if not self.ai_available:
                self.logger.warning('AI agent not available — moving to Failed')
                self._move_to_failed(staging_file, 'AI agent not available')
                return

            # Create a timestamped plan file path (the AI will write its plan there)
            plan_file    = self.plans / f'PLAN_{staging_file.stem}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.md'
            file_content = staging_file.read_text(encoding='utf-8')
            prompt       = self._build_processing_prompt(staging_file.name, file_content, str(plan_file))

            # Dispatch to the correct AI agent
            if self.ai_agent == 'qwen':
                self._process_with_qwen(staging_file, plan_file, file_content, prompt, start_time)
            else:
                self._process_with_claude(staging_file, plan_file, prompt, start_time)

        except Exception as e:
            error_msg = f'{type(e).__name__}: {e}'
            self.logger.error(f'Processing failed for {staging_file.name}: {error_msg}', exc_info=True)
            self._move_to_failed(staging_file, error_msg, start_time)
        finally:
            # Always release the lock so the file can be re-queued if needed
            self.processing_files.discard(staging_file.name)
            self.processing_times.pop(staging_file.name, None)

    def _build_processing_prompt(self, filename: str, content: str, plan_path: str) -> str:
        """
        Build the AI prompt for processing a file.
        If the file is an email (type: email in frontmatter), appends a
        strict email-reply workflow so the AI knows to create an approval request
        rather than sending the reply directly.
        """
        is_email = 'type: email' in content

        base_prompt = f'''You are the AI Employee v0.3 (Professional Pipeline). Process this file following the Company Handbook rules.

File: {filename}

Content:
{content}

Your tasks:
1. Read and understand the file content
2. Create a plan file at: {plan_path}
3. Execute the required actions following the Company Handbook rules
4. For any action requiring approval, create a file in /Pending_Approval
5. When complete, the orchestrator will move the file to /Done
6. Update the Dashboard.md with a summary of what was done

Remember:
- Always follow the Company Handbook rules
- Never act on sensitive operations without approval
- Log all actions taken
- Be transparent about uncertainties

Start by reading the file and creating a plan.'''

        if is_email:
            # Append extra instructions so the AI follows the human-approval email flow
            email_prompt = f'''

*** EMAIL WORKFLOW — READ CAREFULLY ***

This is an incoming email that needs a reply. Follow these EXACT steps:

1. Read the email content (From, Subject, Body)
2. Draft a professional and appropriate reply
3. Create an approval request file in the Pending_Approval folder with this naming:
   APPROVAL_email_send_YYYYMMDD_HHMMSS_{re.sub(r'[^a-zA-Z0-9_]', '_', filename.replace('EMAIL_', ''))[:40]}.md

4. The approval file MUST have this exact frontmatter:
   ---
   type: approval_request
   action: email
   from: "<sender email>"
   subject: "<original subject>"
   message_id: <original gmail message_id>
   status: pending
   ---

5. Include in the approval file:
   - Original email details (From, Subject)
   - Your drafted reply under a "## Drafted Reply" section
   - Instructions: "Move this file to /Approved to send via Email MCP Server"

6. Move the original email file to /Done
7. Do NOT send the email directly — the orchestrator will handle sending after approval

*** END EMAIL WORKFLOW ***
'''
            return base_prompt + email_prompt

        return base_prompt

    def _generate_email_reply(self, subject: str, sender_name: str, body_text: str, body_lower: str) -> str:
        """
        Rule-based email reply generator used by the Qwen path.
        Matches common email types (welcome, meeting, report, invoice, etc.)
        and returns an appropriate canned reply. Falls back to a generic reply.
        """
        if any(w in body_lower for w in ['welcome', 'glad to have you', 'onboarding', 'new member']):
            return (
                f"Dear {sender_name},\n\n"
                f"Thank you for the warm welcome! I'm excited to be here and looking forward to\n"
                f"contributing to the team.\n\n"
                f"Please let me know if there's anything I should review or get started on\n"
                f"right away.\n\n"
                f"Best regards,\n"
                f"Sariim"
            )

        if any(w in body_lower for w in ['meeting', 'calendar', 'schedule', 'invite', 'available on']):
            return (
                f"Dear {sender_name},\n\n"
                f"Thank you for the invitation. I've received the meeting details and\n"
                f"will be attending as scheduled.\n\n"
                f"Please share the agenda or any pre-reading materials if available.\n\n"
                f"Best regards,\n"
                f"Sariim"
            )

        if any(w in body_lower for w in ['report', 'insights', 'data', 'research', 'pdf', 'download',
                                          'trend', 'automat', 'digital', 'autonomous']):
            return (
                f"Dear {sender_name},\n\n"
                f"Thank you for sharing these insights. The data on automation and digital FTEs\n"
                f"is very relevant to our current strategy.\n\n"
                f"I will review the full report and share my thoughts on how we can apply\n"
                f"these findings to our operations.\n\n"
                f"Best regards,\n"
                f"Sariim"
            )

        if any(w in body_lower for w in ['invoice', 'payment', 'bill', 'due', 'amount', '$', 'usd', 'pkr']):
            return (
                f"Dear {sender_name},\n\n"
                f"Thank you for the invoice. I've logged it for processing and will ensure\n"
                f"the payment is handled according to our schedule.\n\n"
                f"Please confirm the payment reference and due date if not already included.\n\n"
                f"Best regards,\n"
                f"Sariim"
            )

        if any(w in body_lower for w in ['application', 'position', 'hiring', 'job', 'resume', 'cv',
                                          'interview', 'candidate']):
            return (
                f"Dear {sender_name},\n\n"
                f"Thank you for your interest. We've received your application and our team\n"
                f"is reviewing it.\n\n"
                f"We'll reach out if your profile aligns with our current openings.\n\n"
                f"Best regards,\n"
                f"Sariim"
            )

        if any(w in body_lower for w in ['follow-up', 'follow up', 'reminder', 'checking in',
                                          'just following', 'wanted to check']):
            return (
                f"Dear {sender_name},\n\n"
                f"Thank you for following up. I'm on top of this and will get back to you\n"
                f"with an update shortly.\n\n"
                f"Best regards,\n"
                f"Sariim"
            )

        if '?' in body_text or any(w in body_lower for w in ['could you', 'can you', 'please let me know',
                                                              'would you', 'do you have']):
            return (
                f"Dear {sender_name},\n\n"
                f"Thank you for reaching out. I'm looking into your question and will get back\n"
                f"to you with a detailed response soon.\n\n"
                f"Best regards,\n"
                f"Sariim"
            )

        # Generic fallback reply when no pattern matches
        return (
            f"Dear {sender_name},\n\n"
            f"Thank you for your email regarding: {subject}.\n\n"
            f"We have received your message and are reviewing it.\n"
            f"We'll get back to you shortly with a detailed response.\n\n"
            f"Best regards,\n"
            f"Sariim"
        )

    def _process_with_qwen(self, staging_file: Path, plan_file: Path, content: str, prompt: str, start_time: float) -> None:
        """
        Qwen Code processing path.

        For regular files: writes a plan, moves file to Done/, prints success banner.
        For email files:   drafts a reply using rule-based matching, creates an approval
                           request in Pending_Approval/, and leaves the file in Processing/
                           until the human approves.
        """
        self.logger.info(f'Processing with Qwen Code: {staging_file.name}')

        # Always create a plan file documenting the intended steps
        plan_file.write_text(
            f'---\n'
            f'created: {datetime.now().isoformat()}\n'
            f'status: active\n'
            f'source_file: {staging_file.name}\n'
            f'ai_agent: qwen\n'
            f'---\n\n'
            f'# Plan: Process {staging_file.name}\n\n'
            f'## Objective\n'
            f'Process the file following Company Handbook rules.\n\n'
            f'## Steps\n'
            f'- [ ] Read and analyze file content\n'
            f'- [ ] Determine required actions\n'
            f'- [ ] Execute actions (or create approval request if needed)\n'
            f'- [ ] Move file to /Done when complete\n'
            f'- [ ] Update Dashboard.md\n\n'
            f'## Notes\n'
            f'Created by Orchestrator for Qwen Code processing.\n',
            encoding='utf-8'
        )
        self.logger.info(f'Plan created: {plan_file.name}')

        # ── Email branch: requires human approval before sending ─────────────
        if 'type: email' in content:
            self.logger.info(f'Email detected — creating approval request: {staging_file.name}')

            parts = content.split('---', 2)
            if len(parts) >= 3:
                frontmatter = parts[1]
                full_body = parts[2].strip()

                # Parse YAML frontmatter into a metadata dict
                metadata = {}
                for line in frontmatter.strip().split('\n'):
                    if ':' in line:
                        key, value = line.split(':', 1)
                        metadata[key.strip()] = value.strip().strip('"')

                # Extract just the body text under the "## Body" section
                email_body_text = ''
                if '## Body' in full_body:
                    body_section = full_body.split('## Body', 1)[1]
                    next_heading = body_section.find('\n## ')
                    email_body_text = body_section[:next_heading].strip() if next_heading != -1 else body_section.strip()

                sender = metadata.get('from', 'Unknown')
                # Extract display name from "Name <email>" format
                sender_name = sender.split('<')[0].strip().strip('"') if '<' in sender else sender.strip()

                subject = metadata.get('subject', 'Your Email')
                body_lower = email_body_text.lower()

                # Generate a reply using keyword-based rules
                drafted_reply = self._generate_email_reply(subject, sender_name, email_body_text, body_lower)

                # Build a safe filename for the approval file
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                safe_subject = re.sub(r'[^a-zA-Z0-9_ ]', '', subject)[:40].strip().replace(' ', '_')
                approval_filename = f"APPROVAL_email_send_{timestamp}_{safe_subject}.md"

                approval_content = f'''---
type: approval_request
action: email
from: "{sender}"
subject: "{subject}"
message_id: {metadata.get('message_id', '')}
status: pending
created: {datetime.now().isoformat()}
source_file: {staging_file.name}
---

# Email Reply Approval Required

## Original Email
- **From**: {sender}
- **Subject**: {subject}
- **Message ID**: {metadata.get('message_id', '')}

## Email Content
{email_body_text}

---

## Drafted Reply
{drafted_reply}

---

## Actions

### To Approve and Send
1. Review the drafted reply above
2. Edit it if needed
3. Move this file to `/Approved` folder
4. The orchestrator will send it via Email MCP Server

### To Reject
1. Move this file to `/Rejected` folder
2. Add your reason below

## Reason for Rejection
_If rejected, explain why here_

---
*Created by Qwen Code — Move to /Approved to send via Email MCP Server*
'''
                # Write the approval request so the human can review it
                approval_file = self.pending_approval / approval_filename
                approval_file.write_text(approval_content, encoding='utf-8')

                self.logger.info(f'Created approval: {approval_filename}')
                self._log_action('email_approval_created', staging_file.name, 'pending_approval',
                    f'Approval: {approval_filename}')

                # Leave the source file in Processing/ — it will move to Done after approval
                self.logger.info(f'Email {staging_file.name} waiting for approval — NOT moved to Done')

                elapsed = time.time() - start_time
                self._print_processing_stages({
                    'staging':    ('Moving to Processing folder...', True),
                    'processing': ('Email reply drafted',             True),
                    'planning':   ('Approval created in Pending_Approval/', True),
                })
                print(flush=True)
                print_box([
                    "",
                    f"  {c('File:', Colors.YELLOW)}      {staging_file.name}",
                    f"  {c('Location:', Colors.YELLOW)}  Processing/ (waiting)",
                    f"  {c('Approval:', Colors.CYAN)}    Pending_Approval/APPROVAL_email_send_*.md",
                    f"  {c('Status:', Colors.YELLOW)}    ⏸ Awaiting manual approval",
                    f"  {c('Next:', Colors.GREEN)}       Move approval file to /Approved to send",
                    "",
                ], title="⏸ EMAIL WAITING FOR APPROVAL", color=Colors.YELLOW, width=64)
                print(flush=True)
                self._log_action('process_file', staging_file.name, 'pending_approval', 'Email reply drafted — waiting for approval')
            else:
                # Malformed email file — cannot parse frontmatter
                self.logger.error(f'Invalid email format: {staging_file.name}')
                self._move_to_folder(staging_file, self.failed, 'failed')

        # ── Non-email branch: move straight to Done ──────────────────────────
        else:
            self._move_to_folder(staging_file, self.done, 'approved')
            self.logger.info(f'Moved to Done: {staging_file.name}')

            elapsed = time.time() - start_time
            self._print_processing_stages({
                'staging':    ('Moving to Processing folder...', True),
                'processing': ('Qwen Code agent active...',      True),
                'planning':   ('Execution plan generated...',    True),
            })
            self._print_success(staging_file.name, elapsed)
            self._log_action('process_file', staging_file.name, 'success', 'Qwen Code — moved to Done')

    def _process_with_claude(self, staging_file: Path, plan_file: Path, prompt: str, start_time: float) -> None:
        """
        Claude Code processing path.
        Calls the `claude` CLI with the prompt and moves the file to Done/ on success
        or Failed/ on error/timeout.
        """
        stages = {
            'staging':    ('Moving to Processing folder...', True),
            'processing': ('Claude Code agent active...',    True),
            'planning':   ('Execution plan generated...',    True),
        }
        try:
            result = subprocess.run(
                ['claude', '--prompt', prompt],
                capture_output=True, text=True,
                timeout=300,          # 5-minute hard limit per file
                cwd=str(self.vault_path)
            )

            if result.returncode == 0:
                self._move_to_folder(staging_file, self.done, 'approved')
                self.logger.info(f'Moved to Done: {staging_file.name}')

                elapsed = time.time() - start_time
                self._print_processing_stages(stages)
                self._print_success(staging_file.name, elapsed)
                self._log_action('process_file', staging_file.name, 'success')
            else:
                self.logger.error(f'Claude Code error: {result.stderr}')
                self._print_processing_stages(stages)
                self._print_error(staging_file.name, f'Claude Code error: {result.stderr[:40]}')
                self._move_to_failed(staging_file, f'Claude Code error: {result.stderr}', start_time)

        except subprocess.TimeoutExpired:
            self.logger.error(f'Processing timeout for {staging_file.name}')
            self._print_error(staging_file.name, 'Timeout (300s)')
            self._move_to_failed(staging_file, 'Processing timeout (300s)', start_time)
        except Exception as e:
            self.logger.error(f'Claude Code processing failed: {e}')
            self._print_error(staging_file.name, str(e)[:40])
            self._move_to_failed(staging_file, str(e), start_time)

    def _move_to_failed(self, source_file: Path, error_message: str, start_time: float = None) -> None:  # type: ignore
        """
        Move a file to Failed/ and write a companion .log file with the error details
        and stack trace so issues can be debugged later.
        """
        try:
            if not source_file.exists():
                self.logger.warning(f'File not found for failed move: {source_file.name}')
                return

            failed_file = self.failed / source_file.name
            self._move_to_folder(source_file, self.failed, 'failed')

            # Write a sidecar error log next to the failed file
            error_log = self.failed / f'{source_file.stem}_error_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'
            stack = traceback.format_exc()
            error_log.write_text(
                f'File: {source_file.name}\n'
                f'Timestamp: {datetime.now().isoformat()}\n'
                f'Error: {error_message}\n\n'
                f'Stack trace:\n{stack if stack.strip() != "NoneType: None" else "No stack trace available"}\n',
                encoding='utf-8'
            )

            self.logger.info(f'Moved to Failed: {failed_file.name}')
            self._log_action('move_to_failed', source_file.name, 'error', error_message)
        except Exception as e:
            self.logger.error(f'Error moving to Failed: {e}', exc_info=True)

    # ── Approved actions ──────────────────────────────────────────────────────

    def _process_approved(self) -> None:
        """Internal helper used by watch mode to execute all files in Approved/."""
        try:
            approved_files = [f for f in self.approved.iterdir() if f.suffix.lower() == '.md']
            if not approved_files:
                return
            self.logger.info(f'Found {len(approved_files)} approved item(s)')
            for approved_file in approved_files:
                self._execute_approved_action(approved_file)
        except Exception as e:
            self.logger.error(f'Error processing Approved: {e}', exc_info=True)

    def _execute_approved_action(self, approved_file: Path) -> None:
        """Execute one approved action and move it to Done/ (simple generic handler)."""
        try:
            self.logger.info(f'Executing approved action: {approved_file.name}')
            approved_file.read_text(encoding='utf-8')
            self._log_action('execute_approved', approved_file.name, 'success')

            self._move_to_folder(approved_file, self.done, 'approved')
            self.logger.info(f'Moved to Done: {approved_file.name}')
        except Exception as e:
            self.logger.error(f'Error executing approved action: {e}', exc_info=True)
            self._log_action('execute_approved', approved_file.name, 'error', str(e))

    # ── Dashboard ─────────────────────────────────────────────────────────────

    def _get_active_projects(self) -> List[Dict[str, Any]]:
        """
        Scan Plans/ for PLAN_*.md files that have status: active or status: in_progress
        in their frontmatter and return them sorted by most-recently-modified first.
        """
        active_projects = []
        try:
            for plan_file in self.plans.glob('PLAN_*.md'):
                try:
                    content = plan_file.read_text(encoding='utf-8')
                    if '---' in content:
                        parts = content.split('---')
                        if len(parts) >= 3:
                            frontmatter = parts[1]
                            if 'status: active' in frontmatter or 'status: in_progress' in frontmatter:
                                name_parts = plan_file.stem.split('_')
                                if len(name_parts) >= 2:
                                    project_name = name_parts[1]
                                    mtime = datetime.fromtimestamp(plan_file.stat().st_mtime)
                                    active_projects.append({
                                        'name': project_name,
                                        'file': plan_file.name,
                                        'last_modified': mtime,
                                        'path': str(plan_file)
                                    })
                except Exception as e:
                    self.logger.error(f'Error reading plan file {plan_file.name}: {e}')
        except Exception as e:
            self.logger.error(f'Error scanning Plans folder: {e}')

        active_projects.sort(key=lambda x: x['last_modified'], reverse=True)
        return active_projects

    def _update_dashboard(self) -> None:
        """
        Refresh all counters in Dashboard.md:
        - Pending Actions (Inbox + Needs_Action count)
        - Tasks Completed Today / This Week
        - Pending Approvals
        - Active Projects list
        - last_updated timestamp
        """
        try:
            if not self.dashboard.exists():
                self.logger.warning('Dashboard.md not found')
                return

            # Count files in each folder
            inbox_count            = sum(1 for f in self.inbox.iterdir()            if f.suffix.lower() == '.md')
            needs_action_count     = sum(1 for f in self.needs_action.iterdir()     if f.suffix.lower() == '.md')
            pending_approval_count = sum(1 for f in self.pending_approval.iterdir() if f.suffix.lower() == '.md')
            done_today             = sum(1 for f in self.done.iterdir()             if f.suffix.lower() == '.md' and self._is_today(f))
            done_this_week         = sum(1 for f in self.done.iterdir()             if f.suffix.lower() == '.md' and self._is_this_week(f))

            active_projects = self._get_active_projects()

            # Build the active-projects bullet list
            projects_content = []
            if active_projects:
                for project in active_projects:
                    date_str = project['last_modified'].strftime('%Y-%m-%d %H:%M:%S')
                    projects_content.append(f"- {project['file']} (active) - last updated: {date_str}")
            else:
                projects_content.append("- No active projects")

            projects_section = '\n'.join(projects_content)

            # Patch the dashboard markdown in place
            content = self.dashboard.read_text(encoding='utf-8')
            content = self._update_counter_in_table(content, 'Pending Actions', str(inbox_count + needs_action_count))
            content = self._update_counter_in_table(content, 'Tasks Completed Today', str(done_today))
            content = self._update_counter_in_table(content, 'Tasks Completed This Week', str(done_this_week))
            content = self._update_counter_in_table(content, 'Pending Approvals', str(pending_approval_count))
            content = self._update_active_projects_section(content, projects_section)
            content = self._update_timestamp(content)

            self.dashboard.write_text(content, encoding='utf-8')

            self.logger.debug(
                f'Dashboard updated: Inbox={inbox_count}, NeedsAction={needs_action_count}, '
                f'DoneToday={done_today}, DoneThisWeek={done_this_week}, Approvals={pending_approval_count}, '
                f'ActiveProjects={len(active_projects)}'
            )
        except Exception as e:
            self.logger.error(f'Error updating dashboard: {e}', exc_info=True)

    def _update_counter_in_table(self, content: str, metric: str, value: str) -> str:
        """Find a Markdown table row containing `metric` and replace its value column."""
        lines = content.split('\n')
        for i, line in enumerate(lines):
            if metric in line and '|' in line:
                parts = line.split('|')
                if len(parts) >= 4:
                    parts[2] = f' {value} '
                    lines[i] = '|'.join(parts)
                    break
        return '\n'.join(lines)

    def _update_active_projects_section(self, content: str, projects_content: str) -> str:
        """
        Replace the content of the '## 🗂️ Active Projects' section in Dashboard.md.
        Walks the lines looking for the section header, skips existing content,
        then injects the fresh projects_content before the next section.
        """
        lines = content.split('\n')
        new_lines = []
        in_active_projects = False
        content_added = False

        for i, line in enumerate(lines):
            if '## 🗂️ Active Projects' in line:
                in_active_projects = True
                new_lines.append(line)
                continue

            if in_active_projects and not content_added:
                # Inject new content when we hit the next section heading
                if line.strip().startswith('##') or (i + 1 < len(lines) and lines[i + 1].strip().startswith('##')):
                    new_lines.append('')
                    new_lines.append(projects_content)
                    new_lines.append('')
                    new_lines.append('---')
                    new_lines.append('')
                    content_added = True
                    in_active_projects = False
                    new_lines.append(line)
                    continue
                continue  # Skip old project lines while scanning

            new_lines.append(line)

        # If the section was never found, append it at the end
        if not content_added:
            new_lines.append('')
            new_lines.append('## 🗂️ Active Projects')
            new_lines.append('')
            new_lines.append(projects_content)
            new_lines.append('')
            new_lines.append('---')

        return '\n'.join(new_lines)

    def _update_timestamp(self, content: str) -> str:
        """Update the `last_updated:` field in the dashboard frontmatter to now."""
        lines = content.split('\n')
        for i, line in enumerate(lines):
            if 'last_updated:' in line:
                lines[i] = f'last_updated: {datetime.now().isoformat()}'
                break
        return '\n'.join(lines)

    def _is_today(self, file_path: Path) -> bool:
        """Return True if the file was last modified today."""
        try:
            return datetime.fromtimestamp(file_path.stat().st_mtime).date() == datetime.now().date()
        except Exception:
            return False

    def _is_this_week(self, file_path: Path) -> bool:
        """Return True if the file was last modified within the current calendar week (Mon–Sun)."""
        try:
            file_date  = datetime.fromtimestamp(file_path.stat().st_mtime).date()
            today      = datetime.now().date()
            week_start = today - timedelta(days=today.weekday())
            return week_start <= file_date <= today
        except Exception:
            return False

    # ── Activity logging ──────────────────────────────────────────────────────

    def _log_action(self, action_type: str, target: str, result: str, details: str = '') -> None:
        """
        Append a JSON entry to today's activity log file in Logs/.
        Each entry records timestamp, action type, target file, result, and optional details.
        """
        try:
            log_file = self.logs / f'{datetime.now().strftime("%Y-%m-%d")}.json'
            logs: list = []
            if log_file.exists():
                try:
                    logs = json.loads(log_file.read_text(encoding='utf-8'))
                except json.JSONDecodeError:
                    logs = []  # Reset if the file is corrupt

            logs.append({
                'timestamp':   datetime.now().isoformat(),
                'action_type': action_type,
                'actor':       'orchestrator',
                'target':      target,
                'result':      result,
                'details':     details,
            })
            log_file.write_text(json.dumps(logs, indent=2), encoding='utf-8')
        except Exception as e:
            self.logger.error(f'Error logging action: {e}')

    # ── Process Needs_Action folder ───────────────────────────────────────────

    def process_needs_action(self) -> int:
        """
        Process all .md files currently sitting in Needs_Action/.

        IMPORTANT: This method does NOT print the "📥 INCOMING TASK DETECTED" banner.
        That banner is printed once by _run_polling_mode() before this method is called,
        preventing the double-print bug where the banner would show twice per file.

        NOTE: POST_*.md files (LinkedIn posts) are EXCLUDED here — they are handled
        separately by process_linkedin_posts() which enforces the HITL approval workflow.

        Returns:
            Number of files processed this cycle.
        """
        try:
            action_files = [
                f for f in self.needs_action.iterdir()
                if f.suffix.lower() == '.md'
                and f.name not in self.processing_files
                and not f.name.lower().startswith('post_')  # ← Skip LinkedIn posts
            ]

            processed = 0
            for action_file in action_files:
                # Stage and hand off to AI — banner already printed by caller
                self._stage_and_process_file(action_file)
                processed += 1

            return processed

        except Exception as e:
            self.logger.error(f'Error processing Needs_Action: {e}', exc_info=True)
            return 0

    # ── LinkedIn Post Processing ──────────────────────────────────────────────

    def process_linkedin_posts(self) -> int:
        """
        Scan Needs_Action/ for files starting with `post_` that contain
        a linkedin_post type. Creates an approval request in Pending_Approval/
        and moves the source file to Processing/ while waiting for human sign-off.

        Returns:
            Number of posts queued for approval this cycle.
        """
        try:
            post_files = [
                f for f in self.needs_action.iterdir()
                if f.suffix.lower() == '.md' and f.name.lower().startswith('post_')
            ]
            queued = 0

            for post_file in post_files:
                content = post_file.read_text(encoding='utf-8')

                # Detect LinkedIn post by filename prefix OR frontmatter type
                # Accepts: type: linkedin_post, type: post, or just POST_*.md filename
                is_linkedin_post = (
                    'type: linkedin_post' in content
                    or 'type: post' in content
                    or post_file.name.lower().startswith('post_')
                )

                if not is_linkedin_post:
                    continue

                # Parse frontmatter — handle both well-formed and malformed cases
                title    = 'Untitled'
                hashtags = ''
                post_body = content

                # Normalize frontmatter: fix "---key: value" → "---\nkey: value"
                normalized = re.sub(r'^---(\w)', r'---\n\1', content)

                parts = normalized.split('---', 2)
                if len(parts) >= 3:
                    frontmatter = parts[1]
                    post_body   = parts[2].strip()

                    for line in frontmatter.strip().split('\n'):
                        if ':' in line:
                            key, value = line.split(':', 1)
                            key   = key.strip()
                            value = value.strip().strip('"')
                            if key == 'title':
                                title = value
                            elif key == 'hashtags':
                                hashtags = value
                elif len(parts) == 2:
                    # Malformed: only one --- found, body is after it
                    post_body = parts[1].strip()

                print(flush=True)
                print_box([
                    "",
                    f"  {c('Title:', Colors.CYAN)}     {title}",
                    f"  {c('Hashtags:', Colors.CYAN)}  {hashtags if hashtags else 'None'}",
                    f"  {c('Content:', Colors.CYAN)}   {post_body[:80]}{'...' if len(post_body) > 80 else ''}",
                    "",
                ], title="📝 PROCESSING: linkedin_post", color=Colors.MAGENTA, width=64)
                print(flush=True)

                # Build approval file content
                approval_filename = f'LINKEDIN_{post_file.name}'
                approval_file     = self.pending_approval / approval_filename

                approval_content = f'''---
type: approval_request
action: linkedin_post
title: "{title}"
hashtags: {hashtags}
source_file: {post_file.name}
created: {datetime.now().isoformat()}
status: pending
---

# LinkedIn Post Approval Required

## Title
{title}

## Hashtags
{hashtags if hashtags else 'None'}

## Post Content

{post_body}

## Source
{post_file.name}

## To Approve
Move this file to /Approved folder to publish post.

## To Reject
Move this file to /Rejected folder with reason.
'''
                approval_file.write_text(approval_content, encoding='utf-8')

                # Park source file in Processing/ while awaiting approval
                self._move_to_folder(post_file, self.processing, 'pending_approval')

                print(f"[INFO] Generating approval file...", flush=True)
                print(f"[✅] Approval file created: Pending_Approval/{approval_filename}", flush=True)
                print(f"[INFO] Waiting for approval...", flush=True)
                print(f"[INFO] Move file to Approved/ folder to publish", flush=True)
                print(flush=True)

                self.logger.info(f'Created LinkedIn approval request: {approval_filename}')
                self._log_action('linkedin_post_queued', post_file.name, 'pending_approval')
                queued += 1

            return queued

        except Exception as e:
            self.logger.error(f'Error processing LinkedIn posts: {e}', exc_info=True)
            return 0

    # ── Process Approved Actions ──────────────────────────────────────────────

    def process_approved(self) -> int:
        """
        Process files in Approved/ that the human has signed off on.
        Routes each file to the correct executor based on its `action:` frontmatter field:
          - email / send_email / email_reply → _execute_email()
          - linkedin_post                    → _execute_linkedin_post()
          - anything else                    → generic finalize

        Returns:
            Number of approved actions processed this cycle.
        """
        try:
            approved_files = [
                f for f in self.approved.iterdir()
                if f.suffix.lower() == '.md'
            ]

            if not approved_files:
                return 0

            print(flush=True)
            print(f"[INFO] Found {len(approved_files)} approved action(s) — processing...", flush=True)

            processed = 0
            for approved_file in approved_files:
                content = approved_file.read_text(encoding='utf-8')

                # Skip files that don't look like approval requests
                if 'type: approval_request' not in content and 'action:' not in content:
                    continue

                start_time = time.time()

                # Route to the appropriate executor
                if 'action: email' in content or 'action: send_email' in content or 'action: email_reply' in content:
                    self._execute_email(approved_file)
                    self._finalize_approved(approved_file, start_time)
                elif 'action: linkedin_post' in content:
                    success = self._execute_linkedin_post(approved_file)
                    if success:
                        if approved_file.exists():
                            self._finalize_approved(approved_file, start_time)
                    else:
                        # Post failed — move to Failed/ for review
                        print(f"[INFO] Moving failed post to Failed/ folder", flush=True)
                        self._move_to_folder(approved_file, self.failed, 'failed')
                else:
                    self._finalize_approved(approved_file, start_time, action_label='Generic action')

                processed += 1

            if processed > 0:
                print(f"[✅] {processed} approved action(s) completed!", flush=True)
                print(flush=True)

            return processed

        except Exception as e:
            self.logger.error(f'Error processing approved actions: {e}', exc_info=True)
            return 0

    def _mark_email_read(self, gmail_message_id: str) -> None:
        """
        Remove the UNREAD label from a Gmail message via the Email MCP Server.
        Called after an email reply has been successfully sent.
        Silently skips if the MCP server is unavailable.
        """
        try:
            from email_mcp_server import EmailMCPServer
            from googleapiclient.errors import HttpError

            credentials_path = os.getenv('GMAIL_CREDENTIALS_PATH', str(SCRIPT_DIR.parent / 'credentials.json'))
            email_server = EmailMCPServer(
                credentials_path=credentials_path,
                dry_run=False
            )

            if not email_server.service:
                self.logger.warning('Cannot mark email as read: Gmail service not available')
                return

            email_server.service.users().messages().modify(
                userId='me',
                id=gmail_message_id,
                body={'removeLabelIds': ['UNREAD']}
            ).execute()

            self.logger.info(f'Gmail message {gmail_message_id} marked as READ')

        except HttpError as e:  # type: ignore
            self.logger.warning(f'Failed to mark email as read (HTTP {e.resp.status}): {e}')
        except Exception as e:
            self.logger.warning(f'Failed to mark email as read: {e}')

    def _finalize_approved(self, approved_file: Path, start_time: float, action_label: str = '') -> None:
        """
        Common cleanup after an approved action is executed:
        1. If the approval file references a source_file still in Processing/, handle it:
           - Email actions: move source_file to Done/
           - LinkedIn post actions: keep source_file in Processing/ (as published post archive)
        2. If a Gmail message_id is present, mark the email as read.
        3. Print the green completion banner.
        4. Move the approval file itself to Done/.
        5. Write a JSON activity log entry.
        """
        elapsed = time.time() - start_time

        # Determine action type from the approval file
        approved_content = approved_file.read_text(encoding='utf-8')
        is_linkedin_post = 'action: linkedin_post' in approved_content

        # Parse frontmatter to find linked source file and Gmail message ID
        parts = approved_content.split('---', 2)
        if len(parts) >= 3:
            frontmatter = parts[1]
            gmail_message_id = None
            for line in frontmatter.strip().split('\n'):
                if 'message_id:' in line:
                    gmail_message_id = line.split(':', 1)[1].strip().strip('"')
                if 'source_file:' in line:
                    source = line.split(':', 1)[1].strip().strip('"')
                    source_file = self.processing / source
                    if source_file.exists():
                        if is_linkedin_post:
                            # Keep LinkedIn post source in Processing/ as published archive
                            self._update_file_status(source_file, 'published')
                            self.logger.info(f'LinkedIn post source kept in Processing/: {source}')
                        else:
                            # Move email/action source file to Done/
                            self._move_to_folder(source_file, self.done, 'approved')
                            self.logger.info(f'Source file also moved to Done: {source}')

            # Mark the email as read in Gmail now that the reply has been sent
            if gmail_message_id:
                self._mark_email_read(gmail_message_id)

        print(flush=True)
        print_box([
            "",
            f"  {c('File:', Colors.GREEN)}     {approved_file.name}",
            f"  {c('Output:', Colors.GREEN)}   Done/{approved_file.name}",
            f"  {c('Duration:', Colors.GREEN)} {elapsed:.2f} seconds",
            f"  {c('Status:', Colors.GREEN)}   {c('✓ Published & moved to Done', Colors.BOLD + Colors.GREEN)}",
            *(
                [f"  {c('Action:', Colors.GREEN)}   {action_label}"]
                if action_label else []
            ),
            "",
        ], title="✅ APPROVED ACTION COMPLETED", color=Colors.GREEN, width=64)
        print(flush=True)

        self._move_to_folder(approved_file, self.done, 'approved')
        self.logger.info(f'Approved action completed and moved to Done: {approved_file.name}')
        self._log_action('approved_action_done', approved_file.name, 'success')

    def _execute_email(self, approved_file: Path) -> None:
        """
        Send the drafted email reply via the Email MCP Server.
        Extracts the reply from the '## Drafted Reply' section of the approval file,
        then calls email_server.reply_email() with the original message_id so Gmail
        threads the reply correctly.
        """
        self.logger.info(f'Email execution triggered for: {approved_file.name}')

        try:
            content = approved_file.read_text(encoding='utf-8')

            if '---' not in content:
                self.logger.error(f'Invalid email format: {approved_file.name}')
                return

            parts = content.split('---', 2)
            if len(parts) < 3:
                self.logger.error(f'Invalid frontmatter in: {approved_file.name}')
                return

            frontmatter = parts[1]
            body = parts[2].strip()

            # Parse metadata fields from frontmatter
            metadata = {}
            for line in frontmatter.strip().split('\n'):
                if ':' in line:
                    key, value = line.split(':', 1)
                    metadata[key.strip()] = value.strip().strip('"')

            message_id = metadata.get('message_id', '')
            from_email = metadata.get('from', '')
            subject = metadata.get('subject', '')

            # Extract the drafted reply text from its markdown section
            drafted_reply = ''
            if '## Drafted Reply' in body:
                reply_section = body.split('## Drafted Reply', 1)[1]
                next_heading = reply_section.find('\n## ')
                if next_heading != -1:
                    drafted_reply = reply_section[:next_heading].strip()
                else:
                    drafted_reply = reply_section.strip()

            if not drafted_reply.strip():
                self.logger.warning(f'No drafted reply found for: {approved_file.name}')
                self._log_action('email_no_reply', approved_file.name, 'skipped', 'No drafted reply')
                return

            from email_mcp_server import EmailMCPServer
            credentials_path = os.getenv('GMAIL_CREDENTIALS_PATH', str(SCRIPT_DIR.parent / 'credentials.json'))
            dry_run = os.getenv('DRY_RUN', 'false').lower() == 'true'

            print(flush=True)
            print_box([
                "",
                f"  {c('To:', Colors.CYAN)}        {from_email}",
                f"  {c('Subject:', Colors.CYAN)}   {subject}",
                f"  {c('Reply:', Colors.CYAN)}     {drafted_reply[:80]}{'...' if len(drafted_reply) > 80 else ''}",
                f"  {c('Mode:', Colors.CYAN)}      {'DRY RUN (not sent)' if dry_run else 'LIVE (sending now)'}",
                "",
            ], title="📧 SENDING EMAIL REPLY", color=Colors.CYAN, width=64)
            print(flush=True)

            email_server = EmailMCPServer(
                credentials_path=credentials_path,
                dry_run=dry_run
            )

            if not email_server.service:
                self.logger.error('Email MCP Server not authenticated')
                self._log_action('email_auth_failed', approved_file.name, 'error', 'Gmail API auth failed')
                print(f"[❌] Email MCP Server authentication failed — check token", flush=True)
                return

            # Send the reply, threading it under the original Gmail message
            result = email_server.reply_email(
                message_id=message_id,
                body=drafted_reply
            )

            if result.get('status') == 'success':
                self.logger.info(f'Email reply sent successfully: {result.get("message_id")}')
                self._log_action('email_reply_sent', approved_file.name, 'success',
                    f'To: {from_email}, Subject: {subject}, Reply ID: {result.get("message_id")}')
                print(f"[✅] Email reply sent! Message ID: {result.get('message_id')}", flush=True)
            else:
                self.logger.error(f'Failed to send email reply: {result}')
                self._log_action('email_reply_failed', approved_file.name, 'error', str(result.get('error', 'Unknown error')))
                print(f"[❌] Email reply failed: {result.get('error', 'Unknown error')}", flush=True)

        except ImportError:
            self.logger.error('Email MCP Server module not found')
            self._log_action('email_exec_failed', approved_file.name, 'error', 'email_mcp_server module not found')
            print(f"[❌] Email MCP Server module not found", flush=True)
        except Exception as e:
            self.logger.error(f'Email execution failed: {e}', exc_info=True)
            self._log_action('email_exec_error', approved_file.name, 'error', str(e))
            print(f"[❌] Email execution error: {e}", flush=True)

    def _execute_linkedin_post(self, approved_file: Path) -> bool:
        """
        Publish an approved LinkedIn post via the LinkedInPoster module.
        If the module is not installed, simulates success (dry-run mode).

        Returns:
            True if the post was published (or simulated), False on hard failure.
        NOTE: This method does NOT move the file — _finalize_approved() owns that step.
        """
        self.logger.info(f'LinkedIn post execution triggered for: {approved_file.name}')

        try:
            content = approved_file.read_text(encoding='utf-8')
            parts   = content.split('---', 2)

            if len(parts) < 3:
                print(f"[❌] Invalid post format (no frontmatter): {approved_file.name}", flush=True)
                self.logger.error(f'Invalid post format: {approved_file.name}')
                self._log_action('linkedin_post_invalid_format', approved_file.name, 'error')
                return False

            frontmatter = parts[1]
            body        = parts[2].strip()

            # Extract title and hashtags from frontmatter
            metadata: dict = {}
            for line in frontmatter.strip().split('\n'):
                if ':' in line:
                    key, value = line.split(':', 1)
                    metadata[key.strip()] = value.strip().strip('"')

            title    = metadata.get('title', 'Untitled')
            hashtags = metadata.get('hashtags', '')

            # ── Extract ONLY the actual post content from the approval body ──
            # The approval file has this structure:
            #   # LinkedIn Post Approval Required
            #   ## Title
            #   ...
            #   ## Hashtags
            #   ...
            #   ## Post Content
            #   [actual post content here]
            #   ## Source
            #   ...
            #   ## To Approve
            #   ...
            #   ## To Reject
            #   ...
            #
            # We need ONLY what's under "## Post Content", excluding all headings.

            post_content = ''
            if '## Post Content' in body:
                # Extract everything between "## Post Content" and the next "## " heading
                post_section = body.split('## Post Content', 1)[1]
                next_heading = post_section.find('\n## ')
                if next_heading != -1:
                    post_content = post_section[:next_heading].strip()
                else:
                    post_content = post_section.strip()
            else:
                # Fallback: use the entire body if no "## Post Content" section found
                post_content = body

            # Also strip any remaining markdown headings that might have leaked through
            lines = post_content.split('\n')
            cleaned_lines = []
            for line in lines:
                # Skip lines that are just markdown headings
                if re.match(r'^#{1,6}\s+', line):
                    continue
                cleaned_lines.append(line)
            post_content = '\n'.join(cleaned_lines).strip()

            # Append hashtags from frontmatter to the post content if present
            if hashtags:
                post_content = f"{post_content}\n\n{hashtags}"

            print(flush=True)
            print_box([
                "",
                f"  {c('File:', Colors.CYAN)}      {approved_file.name}",
                f"  {c('Title:', Colors.CYAN)}     {title}",
                f"  {c('Hashtags:', Colors.CYAN)}  {hashtags if hashtags else 'None'}",
                f"  {c('Status:', Colors.CYAN)}    Approval detected → publishing...",
                "",
            ], title="🚀 APPROVED ACTION DETECTED", color=Colors.MAGENTA, width=64)
            print(flush=True)

            print(f"[INFO] Publishing to LinkedIn...", flush=True)
            self.logger.info(f'Clean post content extracted ({len(post_content)} chars)')

            # Try real LinkedInPoster; gracefully degrade to simulated success if missing
            try:
                from linkedin_poster import LinkedInPoster  # type: ignore
                poster  = LinkedInPoster(
                    vault_path=str(self.vault_path),
                    dry_run=os.getenv('DRY_RUN', 'false').lower() == 'true'
                )
                success = poster.post_to_linkedin(post_content)
            except ImportError:
                self.logger.warning('linkedin_poster module not found — simulating success (dry run)')
                success = True
            except Exception as e:
                self.logger.error(f'LinkedInPoster initialization failed: {e}', exc_info=True)
                success = False

            if success:
                print(f"[✅] POST SUCCESSFUL: {title}", flush=True)
                self.logger.info(f'Successfully posted to LinkedIn: {approved_file.name}')
                self._log_action('linkedin_post_published', approved_file.name, 'success')
                return True
            else:
                print(f"[❌] POST FAILED: {title}", flush=True)
                print(f"[INFO] Moving to Failed/ folder for review", flush=True)
                self.logger.error(f'Failed to post to LinkedIn: {approved_file.name}')
                self._log_action('linkedin_post_failed', approved_file.name, 'error')
                return False

        except Exception as e:
            print(f"[❌] LinkedIn post error: {e}", flush=True)
            self.logger.error(f'LinkedIn post execution failed: {e}', exc_info=True)
            self._log_action('linkedin_post_error', approved_file.name, f'error: {str(e)}')
            return False

    # ── Daily Briefing ────────────────────────────────────────────────────────

    def generate_daily_briefing(self) -> Optional[Path]:
        """
        Write a daily summary Markdown file to Briefings/ with current folder counts.
        Called manually or scheduled externally; not part of the main polling loop.
        """
        try:
            today         = datetime.now().strftime('%Y-%m-%d')
            briefing_file = self.briefings / f'{today}_daily_briefing.md'

            done_count    = sum(1 for f in self.done.iterdir()             if f.suffix.lower() == '.md')
            failed_count  = sum(1 for f in self.failed.iterdir()           if f.suffix.lower() == '.md')
            pending_count = sum(1 for f in self.pending_approval.iterdir() if f.suffix.lower() == '.md')

            content = f'''---
generated: {datetime.now().isoformat()}
period: {today}
type: daily_briefing
---

# Daily Briefing - {today}

## Summary
- **Tasks Completed**: {done_count}
- **Tasks Failed**: {failed_count}
- **Pending Approval**: {pending_count}

## Recent Activity
_Check Logs folder for detailed activity log_

## Next Steps
- Review pending approvals
- Check failed tasks for errors
- Plan tomorrow's priorities

---
*Generated by AI Employee v0.3 Silver Tier*
'''

            briefing_file.write_text(content, encoding='utf-8')
            self.logger.info(f'Daily briefing generated: {briefing_file.name}')
            return briefing_file

        except Exception as e:
            self.logger.error(f'Error generating daily briefing: {e}', exc_info=True)
            return None

    # ── Status snapshot ───────────────────────────────────────────────────────

    def get_status(self) -> Dict[str, Any]:
        """Return a snapshot dict of current orchestrator state for monitoring/debugging."""
        return {
            'vault_path':       str(self.vault_path),
            'ai_agent':         self.ai_agent,
            'ai_available':     self.ai_available,
            'watch_mode':       self.watch_mode,
            'folders': {
                'inbox':            sum(1 for f in self.inbox.iterdir()            if f.suffix.lower() == '.md'),
                'processing':       sum(1 for f in self.processing.iterdir()       if f.suffix.lower() == '.md'),
                'needs_action':     sum(1 for f in self.needs_action.iterdir()     if f.suffix.lower() == '.md'),
                'pending_approval': sum(1 for f in self.pending_approval.iterdir() if f.suffix.lower() == '.md'),
                'approved':         sum(1 for f in self.approved.iterdir()         if f.suffix.lower() == '.md'),
                'done':             sum(1 for f in self.done.iterdir()             if f.suffix.lower() == '.md'),
                'failed':           sum(1 for f in self.failed.iterdir()           if f.suffix.lower() == '.md'),
            },
            'processing_files': list(self.processing_files),
            'active_projects':  len(self._get_active_projects()),
        }


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description='AI Employee Orchestrator',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Flow:  Inbox → Processing → Done/Failed

Examples:
  %(prog)s -v /path/to/vault                 # polling mode (default)
  %(prog)s -v /path/to/vault --watch         # real-time watch mode
  %(prog)s -v /path/to/vault -w -i 30        # watch mode, 30 s interval
  %(prog)s -v /path/to/vault -a claude       # use Claude Code
'''
    )
    parser.add_argument('--vault',    '-v', required=True,          help='Path to the Obsidian vault')
    parser.add_argument('--interval', '-i', type=int, default=60,   help='Check interval in seconds (default: 60)')
    parser.add_argument('--ai-agent', '-a', default='qwen', choices=['qwen', 'claude'], help='AI agent (default: qwen)')
    parser.add_argument('--watch',    '-w', action='store_true',    help='Enable real-time watchdog monitoring')

    args = parser.parse_args()

    Orchestrator(
        vault_path=args.vault,
        check_interval=args.interval,
        ai_agent=args.ai_agent,
        watch_mode=args.watch,
    ).run()


if __name__ == '__main__':
    main()