"""
Email MCP Server Module

Model Context Protocol server for sending, drafting, and searching emails via Gmail API.
Part of the Silver Tier AI Employee system.
"""

import os
import json
import base64
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional, List
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email import encoders

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('EmailMCP')


class EmailMCPServer:
    """
    MCP server for email operations.
    Exposes methods for sending, drafting, searching, and replying to emails.
    """

    def __init__(self, credentials_path: str = None, dry_run: bool = True):
        """
        Initialize Email MCP Server.

        Args:
            credentials_path: Path to Gmail OAuth credentials
            dry_run: If True, only log actions without executing
        """
        self.credentials_path = credentials_path or os.getenv('GMAIL_CREDENTIALS_PATH')
        self.dry_run = dry_run or os.getenv('DRY_RUN', 'true').lower() == 'true'
        self.service = None
        self.max_emails_per_hour = 20
        self.emails_sent_this_hour = 0
        self.last_reset = datetime.now()

        if self.credentials_path:
            self._authenticate()

    def _authenticate(self) -> bool:
        """Authenticate with Gmail API."""
        try:
            if not Path(self.credentials_path).exists():
                logger.error(f'Credentials not found: {self.credentials_path}')
                return False

            # Load token from the same file Gmail Watcher saves to
            self.token_path = Path(self.credentials_path).parent / 'gmail_token.json'

            if not self.token_path.exists():
                logger.error(f'Gmail token not found at: {self.token_path}')
                logger.info('Run gmail_watcher.py first to complete OAuth and generate gmail_token.json')
                return False

            # Import OAuth2 library for re-authorization flow
            from google_auth_oauthlib.flow import InstalledAppFlow
            import google.auth.transport.requests as auth_requests

            # Try loading the token
            creds = None
            scopes = ['https://www.googleapis.com/auth/gmail.modify']

            try:
                creds = Credentials.from_authorized_user_file(
                    str(self.token_path),
                    scopes
                )
            except Exception:
                logger.warning('Existing token incompatible (wrong scope). Re-authorizing with broader scope...')

            # Token expired or missing — try to refresh or re-authorize
            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    try:
                        creds.refresh(auth_requests.Request())
                        # Save refreshed token
                        with open(str(self.token_path), 'w') as f:
                            f.write(creds.to_json())
                        logger.info('Token refreshed with gmail.modify scope')
                    except Exception as e:
                        logger.error(f'Token refresh failed: {e}')
                        return None
                else:
                    # Need full re-authorization
                    logger.info('No valid token. Starting OAuth flow for gmail.modify scope...')
                    logger.info('Opening browser for authorization...')

                    flow = InstalledAppFlow.from_client_secrets_file(
                        str(self.credentials_path),
                        scopes
                    )
                    creds = flow.run_local_server(port=0)

                    # Save the new token
                    with open(str(self.token_path), 'w') as f:
                        f.write(creds.to_json())
                    logger.info(f'Token saved to: {self.token_path}')

            self.creds = creds
            self.service = build('gmail', 'v1', credentials=self.creds)
            logger.info('Email MCP authenticated via gmail_token.json')
            return True

        except Exception as e:
            logger.error(f'Authentication failed: {e}', exc_info=True)
            return False

    def _check_rate_limit(self) -> bool:
        """Check if we've exceeded rate limit."""
        now = datetime.now()
        if (now - self.last_reset).seconds >= 3600:
            self.emails_sent_this_hour = 0
            self.last_reset = now

        if self.emails_sent_this_hour >= self.max_emails_per_hour:
            logger.warning(f'Rate limit exceeded: {self.max_emails_per_hour} emails/hour')
            return False

        return True

    def send_email(
        self,
        to: str,
        subject: str,
        body: str,
        attachment: str = None,
        cc: str = None,
        bcc: str = None
    ) -> Dict[str, Any]:
        """
        Send an email.

        Args:
            to: Recipient email
            subject: Email subject
            body: Email body (supports HTML)
            attachment: Optional path to file attachment
            cc: Optional CC recipient
            bcc: Optional BCC recipient

        Returns:
            Result dictionary with status and message ID
        """
        if self.dry_run:
            logger.info(f'[DRY RUN] Would send email to {to}')
            logger.info(f'Subject: {subject}')
            logger.info(f'Body: {body[:100]}...')
            return {'status': 'dry_run', 'message_id': None}

        if not self._check_rate_limit():
            return {'status': 'rate_limited', 'message_id': None}

        try:
            # Create message
            message = MIMEMultipart()
            message['to'] = to
            message['subject'] = subject

            if cc:
                message['cc'] = cc
            if bcc:
                message['bcc'] = bcc

            message.attach(MIMEText(body, 'html'))

            # Add attachment if provided
            if attachment and Path(attachment).exists():
                with open(attachment, 'rb') as f:
                    part = MIMEBase('application', 'octet-stream')
                    part.set_payload(f.read())
                    encoders.encode_base64(part)
                    part.add_header(
                        'Content-Disposition',
                        f'attachment; filename="{Path(attachment).name}"'
                    )
                    message.attach(part)

            # Encode message
            raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

            # Send via Gmail API
            sent_message = self.service.users().messages().send(
                userId='me',
                body={'raw': raw}
            ).execute()

            self.emails_sent_this_hour += 1

            result = {
                'status': 'success',
                'message_id': sent_message.get('id'),
                'to': to,
                'subject': subject,
                'timestamp': datetime.now().isoformat()
            }

            logger.info(f'Email sent successfully: {sent_message.get("id")}')
            return result

        except Exception as e:
            logger.error(f'Failed to send email: {e}', exc_info=True)
            return {'status': 'error', 'error': str(e)}

    def draft_email(
        self,
        to: str,
        subject: str,
        body: str
    ) -> Dict[str, Any]:
        """
        Create a draft email without sending.

        Args:
            to: Recipient email
            subject: Email subject
            body: Email body

        Returns:
            Result with draft ID
        """
        if self.dry_run:
            logger.info(f'[DRY RUN] Would create draft to {to}')
            return {'status': 'dry_run', 'draft_id': None}

        try:
            message = MIMEText(body, 'html')
            message['to'] = to
            message['subject'] = subject

            raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

            draft = self.service.users().drafts().create(
                userId='me',
                body={
                    'message': {
                        'raw': raw
                    }
                }
            ).execute()

            result = {
                'status': 'success',
                'draft_id': draft.get('id'),
                'to': to,
                'subject': subject
            }

            logger.info(f'Draft created: {draft.get("id")}')
            return result

        except Exception as e:
            logger.error(f'Failed to create draft: {e}', exc_info=True)
            return {'status': 'error', 'error': str(e)}

    def search_emails(
        self,
        query: str = '',
        max_results: int = 10
    ) -> Dict[str, Any]:
        """
        Search for emails in inbox.

        Args:
            query: Gmail search query (e.g., 'is:unread', 'from:client@example.com')
            max_results: Maximum number of results

        Returns:
            List of matching emails
        """
        if self.dry_run:
            logger.info(f'[DRY RUN] Would search: {query}')
            return {'status': 'dry_run', 'messages': []}

        try:
            results = self.service.users().messages().list(
                userId='me',
                q=query,
                maxResults=max_results
            ).execute()

            messages = results.get('messages', [])
            email_list = []

            for msg in messages:
                full_msg = self.service.users().messages().get(
                    userId='me',
                    id=msg['id'],
                    format='metadata',
                    metadataHeaders=['From', 'To', 'Subject', 'Date']
                ).execute()

                headers = {h['name']: h['value'] for h in full_msg['payload']['headers']}
                email_list.append({
                    'id': msg['id'],
                    'from': headers.get('From', ''),
                    'to': headers.get('To', ''),
                    'subject': headers.get('Subject', ''),
                    'date': headers.get('Date', ''),
                    'snippet': full_msg.get('snippet', '')
                })

            return {
                'status': 'success',
                'count': len(email_list),
                'messages': email_list
            }

        except Exception as e:
            logger.error(f'Search failed: {e}', exc_info=True)
            return {'status': 'error', 'error': str(e)}

    def reply_email(
        self,
        message_id: str,
        body: str
    ) -> Dict[str, Any]:
        """
        Reply to an existing email.

        Args:
            message_id: Gmail message ID to reply to
            body: Reply body

        Returns:
            Result with sent message ID
        """
        if self.dry_run:
            logger.info(f'[DRY RUN] Would reply to message {message_id}')
            return {'status': 'dry_run', 'message_id': None}

        try:
            # Get original message
            original = self.service.users().messages().get(
                userId='me',
                id=message_id,
                format='metadata',
                metadataHeaders=['From', 'Subject', 'Message-ID', 'References']
            ).execute()

            headers = {h['name']: h['value'] for h in original['payload']['headers']}

            # Create reply
            message = MIMEMultipart()
            message['to'] = headers.get('From', '')

            subject = headers.get('Subject', '')
            if not subject.startswith('Re:'):
                subject = f'Re: {subject}'
            message['subject'] = subject

            # Set reply headers
            if 'Message-ID' in headers:
                message['In-Reply-To'] = headers['Message-ID']
            if 'References' in headers:
                message['References'] = headers['References']

            # Convert plain text body to HTML for proper line rendering
            html_body = body.replace('\n\n', '</p><p>').replace('\n', '<br>')
            html_body = f'<p>{html_body}</p>'

            message.attach(MIMEText(html_body, 'html'))

            raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

            sent = self.service.users().messages().send(
                userId='me',
                body={'raw': raw}
            ).execute()

            self.emails_sent_this_hour += 1

            return {
                'status': 'success',
                'message_id': sent.get('id'),
                'in_reply_to': message_id
            }

        except Exception as e:
            logger.error(f'Reply failed: {e}', exc_info=True)
            return {'status': 'error', 'error': str(e)}


def main():
    """Run Email MCP Server."""
    import argparse

    parser = argparse.ArgumentParser(description='Email MCP Server for AI Employee')
    parser.add_argument('--credentials', type=str, help='Path to Gmail credentials')
    parser.add_argument('--dry-run', action='store_true', help='Dry run mode')

    args = parser.parse_args()

    server = EmailMCPServer(
        credentials_path=args.credentials,
        dry_run=args.dry_run
    )

    logger.info(f'Email MCP Server started (Dry Run: {server.dry_run})')
    logger.info('Available methods: send_email, draft_email, search_emails, reply_email')


if __name__ == '__main__':
    main()
