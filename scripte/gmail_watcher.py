"""
Gmail Watcher Module

Monitors Gmail for unread/important emails and creates action files in Needs_Action.
Part of the Silver Tier AI Employee system.
"""

import os
import sys
import base64
import logging
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional
from email.utils import parsedate_to_datetime

# Add scripte directory to path for imports
SCRIPT_DIR = Path(__file__).parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from base_watcher import BaseWatcher


class GmailWatcher(BaseWatcher):
    """
    Watches Gmail for unread or important messages.
    Creates .md action files for each new email found.
    """

    def __init__(
        self,
        vault_path: str,
        credentials_path: str = None, # type: ignore
        check_interval: int = 120,
        max_emails_per_check: int = 10
    ):
        """
        Initialize the Gmail Watcher.

        Args:
            vault_path: Path to the Obsidian vault root
            credentials_path: Path to Gmail OAuth credentials file
            check_interval: Seconds between checks (default: 120)
            max_emails_per_check: Max emails to process per check (default: 10)
        """
        super().__init__(vault_path, check_interval)

        # Credentials can be a JSON file path or loaded from environment
        if credentials_path is None:
            credentials_path = os.getenv('GMAIL_CREDENTIALS_PATH')

        self.credentials_path = credentials_path
        self.max_emails_per_check = max_emails_per_check
        self.service = None
        self.processed_ids_file = self.logs / 'gmail_processed_ids.txt'
        self.processed_ids = set()  # Initialize empty set

        # Load previously processed IDs to avoid duplicates across restarts
        self._load_processed_ids()

    def _load_processed_ids(self):
        """Load processed IDs from log file to persist across restarts."""
        if self.processed_ids_file.exists():
            try:
                content = self.processed_ids_file.read_text().strip()
                if content:
                    self.processed_ids = set(content.split('\n'))
                else:
                    self.processed_ids = set()
            except Exception:
                self.processed_ids = set()
        else:
            self.processed_ids = set()

    def _save_processed_ids(self):
        """Save processed IDs to log file."""
        try:
            # Keep only last 1000 IDs to prevent file bloat
            ids_list = list(self.processed_ids)[-1000:]
            self.processed_ids_file.write_text('\n'.join(ids_list))
        except Exception as e:
            self.logger.warning(f'Failed to save processed IDs: {e}')
            
# ── Gmail authentication ──────────────────────────────────────────────────
    
    def _authenticate(self) -> bool:
        """
        Authenticate with Gmail API using OAuth2 credentials.
        Handles the OAuth flow including browser authorization on first run.

        Returns:
            True if authentication successful, False otherwise
        """
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow
            import google.auth.transport.requests as auth_requests
            
            if not self.credentials_path or not Path(self.credentials_path).exists():
                self.logger.error(f'Gmail credentials not found at: {self.credentials_path}')
                self.logger.info('Set GMAIL_CREDENTIALS_PATH environment variable')
                return False

            # Define the token path for storing OAuth tokens
            self.token_path = Path(self.credentials_path).parent / 'gmail_token.json'

            # Try to load existing token first
            if self.token_path.exists():
                try:
                    self.creds = Credentials.from_authorized_user_file(
                        str(self.token_path),
                        ['https://www.googleapis.com/auth/gmail.modify']
                    )
                    
                    if self.creds and self.creds.valid:
                        self.logger.info('Gmail credentials loaded from token')
                        self.service = build('gmail', 'v1', credentials=self.creds)
                        return True
                    
                    # Token expired, try to refresh
                    if self.creds and self.creds.expired and self.creds.refresh_token:
                        request = auth_requests.Request()
                        self.creds.refresh(request)
                        # Save refreshed token using new method
                        with open(str(self.token_path), 'w') as token_file:
                            token_file.write(self.creds.to_json())
                        self.logger.info('Gmail token refreshed')
                        self.service = build('gmail', 'v1', credentials=self.creds)
                        return True
                except Exception as e:
                    self.logger.warning(f'Token load failed, will re-authorize: {e}')

            # No valid token, need to authorize via browser
            self.logger.info('No valid token found. Starting OAuth flow...')
            self.logger.info('Opening browser for Gmail authorization...')
            
            SCOPES = ['https://www.googleapis.com/auth/gmail.modify']
            
            flow = InstalledAppFlow.from_client_secrets_file(
                str(self.credentials_path),
                SCOPES
            )
            
            # Run local server to get authorization
            self.creds = flow.run_local_server(port=0)
            
            # Save the credentials for future use - FIXED: replaced .to_file() with proper JSON save
            with open(str(self.token_path), 'w') as token_file:
                token_file.write(self.creds.to_json())
            self.logger.info(f'Gmail token saved to: {self.token_path}')
            
            self.service = build('gmail', 'v1', credentials=self.creds)
            self.logger.info('Gmail service authenticated successfully')
            return True

        except Exception as e:
            self.logger.error(f'Gmail authentication failed: {e}', exc_info=True)
            return False

    def check_for_updates(self) -> List[Dict[str, Any]]:
        """
        Check for unread or important emails.

        Returns:
            List of email metadata dictionaries
        """
        if not self.service:
            if not self._authenticate():
                return []

        try:
            # Query unread emails (removed is:important to catch ALL unread emails)
            # You can customize this query based on your needs:
            # - 'is:unread' - all unread emails
            # - 'is:unread category:primary' - only primary tab
            # - 'is:unread -category:promotions -category:social' - exclude promotions/social
            results = self.service.users().messages().list( # type: ignore
                userId='me',
                q='is:unread',  # ← Changed: now catches ALL unread emails
                maxResults=self.max_emails_per_check
            ).execute()

            messages = results.get('messages', [])
            self.logger.info(f'Found {len(messages)} unread emails')

            new_emails = []
            for msg in messages:
                if msg['id'] not in self.processed_ids:
                    self.logger.debug(f"New email detected: {msg['id']}")
                    # Get full message details
                    full_msg = self.service.users().messages().get( # type: ignore
                        userId='me',
                        id=msg['id'],
                        format='full'
                    ).execute()
                    new_emails.append(full_msg)
                else:
                    self.logger.debug(f"Skipping already processed: {msg['id']}")

            self.logger.info(f'{len(new_emails)} new emails to process')
            return new_emails

        except HttpError as error:
            self.logger.error(f'Gmail API error: {error}', exc_info=True)
            return []
        except Exception as e:
            self.logger.error(f'Error checking for updates: {e}', exc_info=True)
            return []

    def _extract_email_data(self, message: Dict) -> Dict[str, str]:
        """
        Extract useful data from a Gmail message.

        Args:
            message: Full Gmail message object

        Returns:
            Dictionary with extracted email fields
        """
        payload = message.get('payload', {})
        headers = {h['name']: h['value'] for h in payload.get('headers', [])}

        # Extract email body
        body = ''
        if 'parts' in payload:
            for part in payload['parts']:
                if part.get('mimeType') == 'text/plain':
                    body_data = part.get('body', {}).get('data', '')
                    if body_data:
                        body = base64.urlsafe_b64decode(body_data).decode('utf-8')
                        break
        elif 'body' in payload:
            body_data = payload['body'].get('data', '')
            if body_data:
                body = base64.urlsafe_b64decode(body_data).decode('utf-8')

        # Parse date
        date_str = headers.get('Date', '')
        try:
            date_obj = parsedate_to_datetime(date_str)
            received = date_obj.isoformat()
        except Exception:
            received = datetime.now().isoformat()

        return {
            'message_id': message.get('id', 'unknown'),
            'from': headers.get('From', 'Unknown'),
            'to': headers.get('To', ''),
            'subject': headers.get('Subject', 'No Subject'),
            'date': received,
            'snippet': message.get('snippet', ''),
            'body': body[:2000],  # Truncate very long bodies
        }

    def create_action_file(self, item: Dict[str, Any]) -> Optional[Path]:
        """
        Create a .md action file in the Needs_Action folder for an email.

        Args:
            item: Email data dictionary

        Returns:
            Path to the created file, or None if failed
        """
        try:
            email_data = self._extract_email_data(item)

            # Guard: skip if already processed (prevents double-creation)
            if email_data['message_id'] in self.processed_ids:
                self.logger.debug(f"Skipping already processed email: {email_data['message_id']}")
                return None

            # Sanitize filename
            safe_subject = self.safe_filename(email_data['subject'])[:50]
            filename = f"EMAIL_{email_data['message_id']}_{safe_subject}.md"

            # Determine priority
            priority = 'high' if 'important' in email_data.get('snippet', '').lower() else 'normal'

            content = f'''---
type: email
from: "{email_data['from']}"
subject: "{email_data['subject']}"
received: {email_data['date']}
priority: {priority}
status: pending
message_id: {email_data['message_id']}
---

# Email: {email_data['subject']}

## From
{email_data['from']}

## Received
{email_data['date']}

## Snippet
{email_data['snippet']}

## Body
{email_data['body']}

## Suggested Actions
- [ ] Reply to sender
- [ ] Forward to relevant party
- [ ] Archive after processing
- [ ] Flag for follow-up if needed

## Notes
_Add your notes here before processing_
'''

            filepath = self.needs_action / filename
            filepath.write_text(content, encoding='utf-8')

            # Mark as processed
            self.processed_ids.add(email_data['message_id'])
            self._save_processed_ids()

            return filepath

        except Exception as e:
            self.logger.error(f'Failed to create action file: {e}', exc_info=True)
            return None

# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    """Run the Gmail Watcher."""
    import argparse

    parser = argparse.ArgumentParser(description='Gmail Watcher for AI Employee')
    parser.add_argument('--vault', type=str, required=True, help='Path to Obsidian vault')
    parser.add_argument('--credentials', type=str, help='Path to Gmail credentials JSON')
    parser.add_argument('--interval', type=int, default=120, help='Check interval in seconds')
    parser.add_argument('--max-emails', type=int, default=10, help='Max emails per check')
    parser.add_argument('--once', action='store_true', help='Run once (for OAuth setup)')
    parser.add_argument('--clear-history', action='store_true', help='Clear processed email history')

    args = parser.parse_args()

    watcher = GmailWatcher(
        vault_path=args.vault,
        credentials_path=args.credentials,
        check_interval=args.interval,
        max_emails_per_check=args.max_emails
    )

    # Clear processed history if requested
    if args.clear_history:
        if watcher.processed_ids_file.exists():
            watcher.processed_ids_file.unlink()
            print(f"✓ Cleared processed email history: {watcher.processed_ids_file}")
        else:
            print("No processed email history found")
        return

    if args.once:
        # Run once for OAuth setup
        print("\n=== Gmail OAuth Setup Mode ===")
        print("Authenticating with Gmail...")
        if watcher._authenticate():
            print("✓ Authentication successful!")
            print("✓ Token saved for future use")
            print("\nChecking for unread emails...")
            emails = watcher.check_for_updates()
            if emails:
                print(f"\nFound {len(emails)} unread important email(s):")
                for email in emails:
                    email_data = watcher._extract_email_data(email)
                    print(f"\n  From: {email_data['from']}")
                    print(f"  Subject: {email_data['subject']}")
                    filepath = watcher.create_action_file(email)
                    if filepath:
                        print(f"  ✓ Action file created: {filepath.name}")
            else:
                print("No unread important emails found")
        else:
            print("✗ Authentication failed")
            return
    else:
        try:
            watcher.run()
        except KeyboardInterrupt:
            print('\nGmail Watcher stopped')


if __name__ == '__main__':
    main()