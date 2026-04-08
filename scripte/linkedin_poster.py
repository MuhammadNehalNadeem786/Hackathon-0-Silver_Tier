"""
LinkedIn Poster Module

Posts business updates to LinkedIn using Playwright browser automation.
Requires human approval before posting (HITL pattern).
Part of the Silver Tier AI Employee system.
"""

import os
import sys
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List

# Load environment variables from .env file
from dotenv import load_dotenv
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
load_dotenv(PROJECT_ROOT / '.env')

if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

try:
    from base_watcher import BaseWatcher as BaseWatcher # type: ignore
except ImportError:
    import logging as _logging
    from pathlib import Path as _Path

    class BaseWatcher:  # type: ignore[no-redef]
        """Fallback stub when base_watcher module is unavailable."""

        def __init__(self, vault_path: str, check_interval: int = 60) -> None:
            self.vault_path = _Path(vault_path)
            self.check_interval = check_interval
            self.logger = _logging.getLogger(self.__class__.__name__)
            self.needs_action     = self.vault_path / "Needs_Action"
            self.pending_approval = self.vault_path / "Pending_Approval"
            self.approved         = self.vault_path / "Approved"
            self.rejected         = self.vault_path / "Rejected"
            self.done             = self.vault_path / "Done"

            for folder in [
                self.needs_action, self.pending_approval,
                self.approved, self.rejected, self.done,
            ]:
                folder.mkdir(parents=True, exist_ok=True)


class LinkedInPoster(BaseWatcher):
    """
    Posts content to LinkedIn using browser automation.
    Always requires human approval before publishing.
    """

    def __init__(
        self,
        vault_path: str,
        email: Optional[str] = None,
        password: Optional[str] = None,
        dry_run: Optional[bool] = None,
    ) -> None:
        """
        Initialize the LinkedIn Poster.

        Args:
            vault_path: Path to the Obsidian vault root
            email: LinkedIn email (from env if not provided)
            password: LinkedIn password (from env if not provided)
            dry_run: If True, only log what would be posted. Reads from .env DRY_RUN if not provided.
        """
        super().__init__(vault_path, check_interval=300)

        self.email    = email    or os.getenv("LINKEDIN_EMAIL")
        self.password = password or os.getenv("LINKEDIN_PASSWORD")

        # Read DRY_RUN from .env file (default to false = live mode)
        if dry_run is None:
            env_value = os.getenv("DRY_RUN", "false").lower()
            self.dry_run = env_value in ("true", "1", "yes", "on")
        else:
            self.dry_run = dry_run

        self.session_path = self.vault_path / ".linkedin_session"
        self.session_path.mkdir(parents=True, exist_ok=True)

        self.logger.info(f"LinkedIn Poster initialized (Dry Run: {self.dry_run})")

    def authenticate(self, page: Any) -> bool:
        """
        Authenticate with LinkedIn using browser automation.

        Args:
            page: Playwright page object

        Returns:
            True if authentication successful
        """
        try:
            self.logger.info("Navigating to LinkedIn...")
            page.goto("https://www.linkedin.com/login", timeout=30000)

            if "feed" in page.url:
                self.logger.info("Already authenticated")
                return True

            page.fill("#username", self.email or "")
            page.fill("#password", self.password or "")
            page.click('[type="submit"]')
            page.wait_for_url("**/feed/**", timeout=30000)
            self.logger.info("Successfully authenticated with LinkedIn")
            return True

        except PlaywrightTimeout:
            self.logger.error("LinkedIn login timeout. Check credentials.")
            return False
        except Exception as e:
            self.logger.error(f"LinkedIn authentication error: {e}", exc_info=True)
            return False

    def post_to_linkedin(
        self,
        content: str,
        image_path: Optional[str] = None,
    ) -> bool:
        """
        Post content to LinkedIn using browser automation.

        Args:
            content: Post text content
            image_path: Optional path to image to attach

        Returns:
            True if post successful
        """
        if self.dry_run:
            self.logger.info("=" * 60)
            self.logger.info("[DRY RUN MODE] Would post to LinkedIn:")
            self.logger.info(f"Content length: {len(content)} characters")
            self.logger.info(f"Content preview: {content[:200]}...")
            self.logger.info("=" * 60)
            return True

        browser = None
        context = None

        try:
            self.logger.info("LIVE MODE: Starting LinkedIn browser automation...")

            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=False,
                    args=["--start-maximized"],
                )
                context = browser.new_context(
                    viewport={"width": 1280, "height": 720},
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                )
                page = context.new_page()

                self.logger.info("Navigating to LinkedIn feed...")
                page.goto("https://www.linkedin.com/feed/", timeout=30000, wait_until="domcontentloaded")
                
                # Wait for feed to load (more lenient approach)
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=10000)
                except PlaywrightTimeout:
                    self.logger.warning("Feed load timeout, continuing anyway...")
                
                # Give the page time to render
                page.wait_for_timeout(3000)

                if "login" in page.url.lower():
                    self.logger.info("Login required, authenticating...")
                    if not self.authenticate(page):
                        self.logger.error("Authentication failed")
                        return False
                    
                    # After login, navigate back to feed
                    page.goto("https://www.linkedin.com/feed/", timeout=30000, wait_until="domcontentloaded")
                    page.wait_for_timeout(3000)

                self.logger.info("Creating new post...")
                post_selectors = [
                    'button:has-text("Start a post")',
                    'div[role="button"]:has-text("Start a post")',
                    '.share-box-feed-entry__trigger',
                    'button[aria-label*="Start a post"]',
                    'button[aria-label*="post"]',
                    "div.share-box__trigger",
                ]

                clicked = False
                for selector in post_selectors:
                    try:
                        element = page.wait_for_selector(selector, timeout=5000)
                        if element and element.is_visible():
                            element.click()
                            clicked = True
                            self.logger.info(f"Clicked post button: {selector}")
                            break
                    except PlaywrightTimeout:
                        continue
                    except Exception as e:
                        self.logger.debug(f"Selector {selector} failed: {e}")

                if not clicked:
                    self.logger.error("Could not find post creation button")
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    screenshot_path = self.session_path / f"no_post_button_{timestamp}.png"
                    page.screenshot(path=str(screenshot_path))
                    self.logger.info(f"Screenshot saved to {screenshot_path}")
                    return False

                # Wait for composer to appear
                page.wait_for_timeout(3000)

                composer_selectors = [
                    ".ql-editor",
                    'div[role="textbox"]',
                    '[contenteditable="true"]',
                    'div[aria-label*="post"]',
                ]

                editor = None
                for selector in composer_selectors:
                    try:
                        editor = page.wait_for_selector(selector, timeout=3000)
                        if editor:
                            self.logger.info(f"Found composer: {selector}")
                            break
                    except PlaywrightTimeout:
                        continue

                if not editor:
                    self.logger.error("Could not find post composer")
                    return False

                self.logger.info("Filling post content...")
                clean_content = self._clean_linkedin_content(content)

                # Focus the editor using keyboard Tab to avoid shadow DOM interception
                self.logger.info("Focusing composer via keyboard Tab...")
                page.wait_for_timeout(1000)
                
                # Try clicking first; if it fails, fall back to keyboard focus
                try:
                    editor.click(timeout=5000)
                    self.logger.info("Editor focused via click")
                except Exception:
                    # Fallback: use Tab key to reach the composer
                    self.logger.info("Click failed, using Tab key to focus composer...")
                    # Press Tab multiple times to reach the post composer
                    for _ in range(5):
                        page.keyboard.press("Tab")
                        page.wait_for_timeout(200)
                    self.logger.info("Sent Tab keys to focus composer")

                page.wait_for_timeout(500)

                # Clear any existing content first using keyboard
                page.keyboard.press("Control+a")
                page.keyboard.press("Backspace")
                page.wait_for_timeout(300)

                # Paste content using clipboard (most reliable method for rich text editors)
                try:
                    # Use JavaScript to set the content directly in the contenteditable div
                    # This bypasses shadow DOM interception issues
                    js_code = """
                    (content) => {
                        const editors = document.querySelectorAll('[contenteditable="true"]');
                        for (const ed of editors) {
                            if (ed.getAttribute('aria-label') && ed.getAttribute('aria-label').toLowerCase().includes('post')) {
                                ed.innerText = content;
                                ed.dispatchEvent(new Event('input', { bubbles: true }));
                                return true;
                            }
                        }
                        // Fallback: try any contenteditable
                        if (editors.length > 0) {
                            editors[0].innerText = content;
                            editors[0].dispatchEvent(new Event('input', { bubbles: true }));
                            return true;
                        }
                        return false;
                    }
                    """
                    result = page.evaluate(js_code, clean_content)
                    if result:
                        self.logger.info(f"Content set via JavaScript ({len(clean_content)} chars)")
                    else:
                        self.logger.warning("JavaScript setContent failed, falling back to keyboard typing")
                        raise Exception("JS setContent returned false")
                except Exception as e:
                    self.logger.debug(f"JavaScript setContent failed: {e}")
                    # Final fallback: keyboard typing in chunks
                    chunk_size = 500
                    for i in range(0, len(clean_content), chunk_size):
                        chunk = clean_content[i:i+chunk_size]
                        page.keyboard.type(chunk, delay=5)
                        page.wait_for_timeout(100)
                    self.logger.info(f"Content typed via keyboard ({len(clean_content)} chars)")
                
                page.wait_for_timeout(1500)

                if image_path and Path(image_path).exists():
                    self.logger.info(f"Uploading image: {image_path}")
                    try:
                        upload_selectors = [
                            'button[aria-label*="image"]',
                            'button[aria-label*="photo"]',
                            'input[type="file"]',
                        ]
                        for selector in upload_selectors:
                            try:
                                if selector == 'input[type="file"]':
                                    file_input = page.query_selector(selector)
                                    if file_input:
                                        file_input.set_input_files(str(Path(image_path).absolute()))
                                        self.logger.info("Image uploaded successfully")
                                        page.wait_for_timeout(3000)
                                        break
                                else:
                                    button = page.query_selector(selector)
                                    if button:
                                        button.click()
                                        page.wait_for_timeout(1000)
                                        file_input = page.query_selector('input[type="file"]')
                                        if file_input:
                                            file_input.set_input_files(str(Path(image_path).absolute()))
                                            self.logger.info("Image uploaded successfully")
                                            page.wait_for_timeout(3000)
                                            break
                            except Exception as e:
                                self.logger.debug(f"Upload attempt failed: {e}")
                    except Exception as e:
                        self.logger.warning(f"Image upload failed: {e}")

                self.logger.info("Publishing post...")
                page.wait_for_timeout(3000)

                # Take screenshot before posting for debugging
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                screenshot_path = self.session_path / f"before_posting_{timestamp}.png"
                page.screenshot(path=str(screenshot_path))
                self.logger.info(f"Screenshot saved to {screenshot_path}")

                # Try multiple approaches to find and click the Post button
                posted = False

                # Approach 1: Ctrl+Enter keyboard shortcut (most reliable, try first)
                self.logger.info("Trying Ctrl+Enter keyboard shortcut to post...")
                try:
                    page.keyboard.press("Control+Enter")
                    page.wait_for_timeout(3000)
                    # Check if dialog closed (post succeeded)
                    dialog_still_open = page.query_selector('div[role="dialog"]')
                    if not dialog_still_open:
                        posted = True
                        self.logger.info("✅ Used Ctrl+Enter keyboard shortcut — dialog closed")
                    else:
                        self.logger.info("Dialog still open after Ctrl+Enter, trying other methods...")
                except Exception as e:
                    self.logger.debug(f"Ctrl+Enter failed: {e}")

                # Approach 2: JavaScript dispatch click to bypass shadow DOM
                if not posted:
                    self.logger.info("Trying JavaScript dispatch_event to click Post button...")
                    try:
                        js_click = """
                        () => {
                            const buttons = Array.from(document.querySelectorAll('button'));
                            const postBtn = buttons.find(b => 
                                b.textContent.trim() === 'Post' || 
                                b.getAttribute('aria-label')?.includes('Post')
                            );
                            if (postBtn && !postBtn.disabled) {
                                postBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }));
                                return true;
                            }
                            // Try in dialog
                            const dialog = document.querySelector('[role="dialog"]');
                            if (dialog) {
                                const dialogBtn = dialog.querySelectorAll('button');
                                const postInDialog = Array.from(dialogBtn).find(b => 
                                    b.textContent.trim() === 'Post'
                                );
                                if (postInDialog && !postInDialog.disabled) {
                                    postInDialog.dispatchEvent(new MouseEvent('click', { bubbles: true }));
                                    return true;
                                }
                            }
                            return false;
                        }
                        """
                        result = page.evaluate(js_click)
                        if result:
                            posted = True
                            self.logger.info("✅ Clicked Post button via JavaScript dispatch_event")
                        else:
                            self.logger.warning("JS dispatch found no Post button")
                    except Exception as e:
                        self.logger.debug(f"JS dispatch failed: {e}")

                # Approach 3: Playwright click as last resort
                if not posted:
                    self.logger.info("Trying Playwright click on Post button selectors...")
                    post_button_selectors = [
                        'button:has-text("Post")',
                        'button[aria-label*="Post"]',
                        'button[data-artdeco-is-focused="true"]',
                        '.artdeco-button--primary:has-text("Post")',
                        'div[role="dialog"] button:has-text("Post")',
                    ]

                    for selector in post_button_selectors:
                        try:
                            post_button = page.wait_for_selector(selector, timeout=3000)
                            if post_button and post_button.is_enabled() and post_button.is_visible():
                                post_button.scroll_into_view_if_needed()
                                page.wait_for_timeout(500)
                                # Use dispatch_event instead of click to bypass shadow DOM
                                post_button.evaluate("el => el.dispatchEvent(new MouseEvent('click', { bubbles: true }))")
                                posted = True
                                self.logger.info(f"✅ Clicked post button via dispatch_event: {selector}")
                                break
                            else:
                                self.logger.debug(f"Post button found but not clickable: {selector}")
                        except PlaywrightTimeout:
                            continue
                        except Exception as e:
                            self.logger.debug(f"Post button {selector} failed: {e}")

                # Approach 4: Try clicking by role (with dispatch_event fallback)
                if not posted:
                    self.logger.info("Trying role-based selector with dispatch_event...")
                    try:
                        post_button = page.get_by_role("button", name="Post")
                        if post_button.is_visible():
                            post_button.scroll_into_view_if_needed()
                            page.wait_for_timeout(500)
                            # Use dispatch_event to bypass shadow DOM
                            post_button.evaluate("el => el.dispatchEvent(new MouseEvent('click', { bubbles: true }))")
                            posted = True
                            self.logger.info("✅ Clicked post button by role via dispatch_event")
                    except Exception as e:
                        self.logger.debug(f"Role-based dispatch_event failed: {e}")

                if not posted:
                    self.logger.error("❌ Could not find or click Post button after multiple attempts")
                    # Take screenshot for debugging
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    screenshot_path = self.session_path / f"post_button_failed_{timestamp}.png"
                    page.screenshot(path=str(screenshot_path))
                    self.logger.info(f"Screenshot saved to {screenshot_path}")
                    return False

                # Wait for post to be published and dialog to close
                page.wait_for_timeout(5000)

                # Take screenshot after successful post for confirmation
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                screenshot_path = self.session_path / f"after_posting_{timestamp}.png"
                page.screenshot(path=str(screenshot_path))
                self.logger.info(f"Post confirmation screenshot saved to {screenshot_path}")

                # Verify post was published
                self.logger.info("✅ Successfully posted to LinkedIn!")
                return True

        except Exception as e:
            self.logger.error(f"Failed to post to LinkedIn: {e}", exc_info=True)
            return False
        finally:
            # Clean up browser resources gracefully
            try:
                if context:
                    context.close()
                    page.wait_for_timeout(500)  # Give time for cleanup
                    self.logger.debug("Browser context closed")
            except Exception as e:
                self.logger.debug(f"Context close completed: {e}")
            
            try:
                if browser:
                    browser.close()
                    self.logger.debug("Browser closed")
            except Exception as e:
                self.logger.debug(f"Browser close completed: {e}")

    def _clean_linkedin_content(self, content: str) -> str:
        """Clean and format content for LinkedIn."""
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                content = parts[2].strip()

        lines = []
        for line in content.split("\n"):
            if line.startswith("#"):
                line = line.lstrip("#").strip()
            lines.append(line)

        content = "\n".join(lines)
        content = content.replace("**", "").replace("*", "").replace("`", "")
        content = "\n\n".join([p.strip() for p in content.split("\n\n") if p.strip()])

        if len(content) > 2900:
            content = content[:2900] + "..."

        return content.strip()

    def prepare_post_content(self, action_file: Path) -> Dict[str, Any]:
        """
        Extract post content from action file.

        Args:
            action_file: Path to POST_*.md file

        Returns:
            Dictionary with post content fields
        """
        content = action_file.read_text(encoding="utf-8")

        parts = content.split("---", 2)
        if len(parts) >= 3:
            frontmatter = parts[1]
            body = parts[2].strip()

            metadata: Dict[str, str] = {}
            for line in frontmatter.strip().split("\n"):
                if ":" in line:
                    key, value = line.split(":", 1)
                    metadata[key.strip()] = value.strip()

            return {
                "title":    metadata.get("title", "Business Update"),
                "content":  body,
                "hashtags": metadata.get("hashtags", ""),
                "metadata": metadata,
            }

        return {
            "title":    "Business Update",
            "content":  content,
            "hashtags": "",
            "metadata": {},
        }

    def create_approval_request(
        self,
        post_data: Dict[str, Any],
        source_file: Path,
    ) -> Optional[Path]:
        """
        Create approval request for a LinkedIn post.

        Args:
            post_data: Post content dictionary
            source_file: Original POST_*.md file

        Returns:
            Path to approval file, or None if failed
        """
        try:
            approval_file = self.pending_approval / f"LINKEDIN_{source_file.name}"

            approval_content = f"""---
type: approval_request
action: linkedin_post
title: "{post_data['title']}"
source_file: {source_file.name}
created: {datetime.now().isoformat()}
status: pending
---

# LinkedIn Post Approval Required

## Post Preview

{post_data['content']}

## Hashtags
{post_data.get('hashtags', 'None')}

## Source File
{source_file.name}

## Actions

### To Approve
1. Review the post content above
2. Move this file to `/Approved` folder
3. The orchestrator will publish the post

### To Reject
1. Move this file to `/Rejected` folder
2. Add your reason for rejection below

## Reason for Rejection
_If rejected, explain why_

---
*Created by LinkedIn Poster v0.2.0*
"""
            approval_file.write_text(approval_content, encoding="utf-8")
            self.logger.info(f"Created approval request: {approval_file.name}")
            return approval_file

        except Exception as e:
            self.logger.error(f"Failed to create approval request: {e}", exc_info=True)
            return None

    def check_for_updates(self) -> List[Path]:
        """
        Check for new items to process.
        LinkedInPoster is triggered on-demand, not by monitoring.
        Returns empty list as this is handled by the orchestrator.
        """
        return []

    def create_action_file(self, item: Any) -> Optional[Path]:
        """
        Create a .md action file in the Needs_Action folder.
        LinkedInPoster creates action files via post preparation, not monitoring.
        Returns None as this is handled by the orchestrator.
        """
        return None


def main() -> None:
    """Test LinkedIn posting."""
    import argparse

    parser = argparse.ArgumentParser(description="LinkedIn Poster for AI Employee")
    parser.add_argument("--vault",      type=str, required=True, help="Path to Obsidian vault")
    parser.add_argument("--email",      type=str, help="LinkedIn email")
    parser.add_argument("--password",   type=str, help="LinkedIn password")
    parser.add_argument("--dry-run",    action="store_true",  default=False, help="Dry run mode")
    parser.add_argument("--no-dry-run", dest="dry_run", action="store_false", help="Live mode")

    args = parser.parse_args()

    poster = LinkedInPoster(
        vault_path=args.vault,
        email=args.email,
        password=args.password,
        dry_run=args.dry_run,
    )

    print(f"LinkedIn Poster initialized (Dry Run: {poster.dry_run})")
    print("This script is called by the orchestrator, not run directly.")


if __name__ == "__main__":
    main()