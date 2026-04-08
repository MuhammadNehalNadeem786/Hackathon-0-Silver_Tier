"""
Approval Manager Module

Manages human-in-the-loop approval workflow for sensitive actions.
Monitors Pending_Approval folder and processes Approved/Rejected files.
Part of the Silver Tier AI Employee system.
"""

import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any

from base_watcher import BaseWatcher


class ApprovalManager(BaseWatcher):
    """
    Manages approval workflow for sensitive actions.
    Monitors Pending_Approval and processes Approved/Rejected files.
    """

    def __init__(self, vault_path: str, check_interval: int = 30):
        """
        Initialize Approval Manager.

        Args:
            vault_path: Path to the Obsidian vault root
            check_interval: Seconds between checks (default: 30)
        """
        super().__init__(vault_path, check_interval)

        self.pending_approval = self.vault_path / 'Pending_Approval'
        self.approved = self.vault_path / 'Approved'
        self.rejected = self.vault_path / 'Rejected'

        # Ensure directories exist
        self.pending_approval.mkdir(parents=True, exist_ok=True)
        self.approved.mkdir(parents=True, exist_ok=True)
        self.rejected.mkdir(parents=True, exist_ok=True)

    def check_for_updates(self) -> list:
        """
        Check for new files in Approved and Rejected folders.

        Returns:
            List of files to process
        """
        files_to_process = []

        # Check Approved folder
        try:
            for file in self.approved.iterdir():
                if file.suffix.lower() == '.md':
                    files_to_process.append({
                        'file': file,
                        'action': 'approved'
                    })
        except Exception as e:
            self.logger.error(f'Error checking Approved: {e}')

        # Check Rejected folder
        try:
            for file in self.rejected.iterdir():
                if file.suffix.lower() == '.md':
                    files_to_process.append({
                        'file': file,
                        'action': 'rejected'
                    })
        except Exception as e:
            self.logger.error(f'Error checking Rejected: {e}')

        return files_to_process

    def create_action_file(self, item) -> Optional[Path]:
        """
        Process approved/rejected files and trigger appropriate actions.

        Args:
            item: Dictionary with 'file' and 'action' keys

        Returns:
            Path to processed file, or None if failed
        """
        file_path = item['file']
        action = item['action']

        try:
            content = file_path.read_text(encoding='utf-8')

            # Parse frontmatter to get action type
            frontmatter = self._parse_frontmatter(content)
            action_type = frontmatter.get('action', 'unknown')

            if action == 'approved':
                self._process_approved(file_path, frontmatter)
            elif action == 'rejected':
                self._process_rejected(file_path, frontmatter)

            return file_path

        except Exception as e:
            self.logger.error(f'Error processing {file_path.name}: {e}', exc_info=True)
            return None

    def _parse_frontmatter(self, content: str) -> Dict[str, str]:
        """Extract YAML frontmatter as dictionary."""
        parts = content.split('---', 2)
        if len(parts) < 3:
            return {}

        metadata = {}
        for line in parts[1].strip().split('\n'):
            if ':' in line:
                key, value = line.split(':', 1)
                metadata[key.strip()] = value.strip().strip('"')

        return metadata

    def _process_approved(self, file_path: Path, frontmatter: Dict[str, str]):
        """Process an approved action."""
        action_type = frontmatter.get('action', 'unknown')

        self.logger.info(f'Action approved: {file_path.name} (type: {action_type})')

        # Log the approval
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'action': 'approved',
            'action_type': action_type,
            'file': file_path.name,
            'details': frontmatter
        }

        # Write to approval log
        log_file = self.logs / 'approval_log.json'
        if log_file.exists():
            import json
            logs = json.loads(log_file.read_text())
        else:
            import json
            logs = []

        logs.append(log_entry)
        log_file.write_text(json.dumps(logs, indent=2))

        self.logger.info(f'Approved action logged: {action_type}')

        # The orchestrator will pick this up and execute the actual action
        # This is just the approval tracking

    def _process_rejected(self, file_path: Path, frontmatter: Dict[str, str]):
        """Process a rejected action."""
        action_type = frontmatter.get('action', 'unknown')

        self.logger.warning(f'Action rejected: {file_path.name} (type: {action_type})')

        # Log the rejection
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'action': 'rejected',
            'action_type': action_type,
            'file': file_path.name,
            'details': frontmatter
        }

        # Write to rejection log
        log_file = self.logs / 'rejection_log.json'
        if log_file.exists():
            import json
            logs = json.loads(log_file.read_text())
        else:
            import json
            logs = []

        logs.append(log_entry)
        log_file.write_text(json.dumps(logs, indent=2))

        self.logger.info(f'Rejected action logged: {action_type}')

    def create_approval_request(
        self,
        action_type: str,
        details: Dict[str, Any],
        source_file: str = None
    ) -> Optional[Path]:
        """
        Create a new approval request in Pending_Approval.

        Args:
            action_type: Type of action (email, payment, linkedin_post, etc.)
            details: Dictionary with action details
            source_file: Original file that triggered this request

        Returns:
            Path to created approval request, or None if failed
        """
        try:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f'{action_type.upper()}_{timestamp}.md'

            content = f'''---
type: approval_request
action: {action_type}
created: {datetime.now().isoformat()}
status: pending
source_file: {source_file or 'manual'}
'''

            # Add details to frontmatter
            for key, value in details.items():
                if isinstance(value, str) and (' ' in value or len(value) > 50):
                    content += f'{key}: "{value}"\n'
                else:
                    content += f'{key}: {value}\n'

            content += f'''---

# Approval Required: {action_type.replace('_', ' ').title()}

## Details
'''
            for key, value in details.items():
                content += f'- **{key}**: {value}\n'

            content += f'''

## Source
{source_file or 'Manual request'}

## Actions

### To Approve
1. Review the details above
2. Move this file to `/Approved` folder
3. The orchestrator will execute the action

### To Reject
1. Move this file to `/Rejected` folder
2. Add your reason below

## Reason for Rejection
_If rejected, explain why here_

---
*Created by Approval Manager v0.1.0*
'''

            filepath = self.pending_approval / filename
            filepath.write_text(content, encoding='utf-8')

            self.logger.info(f'Created approval request: {filename}')
            return filepath

        except Exception as e:
            self.logger.error(f'Failed to create approval request: {e}', exc_info=True)
            return None


def main():
    """Run Approval Manager."""
    import argparse

    parser = argparse.ArgumentParser(description='Approval Manager for AI Employee')
    parser.add_argument('--vault', type=str, required=True, help='Path to Obsidian vault')
    parser.add_argument('--interval', type=int, default=30, help='Check interval in seconds')

    args = parser.parse_args()

    manager = ApprovalManager(
        vault_path=args.vault,
        check_interval=args.interval
    )

    try:
        manager.run()
    except KeyboardInterrupt:
        print('\nApproval Manager stopped')


if __name__ == '__main__':
    main()
