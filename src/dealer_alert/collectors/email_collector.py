"""Email collector — polls a Gmail inbox via OAuth2 for newsletter content.

Connects to a dedicated inbox (e.g., newdealerchecker@gmail.com) that
subscribes to trade newsletters, chamber digests, and dealer association
bulletins. Extracts article text and links from HTML emails and feeds
them into the standard lead extraction pipeline.

Uses Google OAuth2 for authentication:
  1. Place your client_secret_*.json in the project root (or set path in .env)
  2. First run opens a browser to authorize the Gmail account
  3. Token is cached in data/gmail_token.json for subsequent runs
"""

from __future__ import annotations

import base64
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

from bs4 import BeautifulSoup

from ..fetcher.base import content_hash, extract_links
from ..models import FetchResult

logger = logging.getLogger(__name__)

# Gmail API scopes — readonly is all we need
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]


class ParsedEmail(NamedTuple):
    """Parsed email content ready for lead extraction."""

    message_id: str
    subject: str
    sender: str
    date: str
    text_content: str
    html_content: str
    links: list[str]


def _build_gmail_service(
    credentials_file: Path,
    token_file: Path,
):
    """Build an authenticated Gmail API service.

    On first run, opens a browser for the user to authorize.
    Subsequent runs use the cached token.
    """
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None

    # Load cached token if it exists
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), GMAIL_SCOPES)

    # If no valid credentials, run the OAuth flow
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logger.info("Refreshing expired Gmail token...")
            creds.refresh(Request())
        else:
            if not credentials_file.exists():
                raise FileNotFoundError(
                    f"OAuth credentials file not found: {credentials_file}\n"
                    "Download it from Google Cloud Console → "
                    "APIs & Services → Credentials"
                )
            logger.info("No cached token found. Opening browser for Gmail authorization...")
            flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), GMAIL_SCOPES)
            creds = flow.run_local_server(port=0)

        # Cache the token for next time
        token_file.parent.mkdir(parents=True, exist_ok=True)
        with open(token_file, "w") as f:
            f.write(creds.to_json())
        logger.info(f"Gmail token cached at {token_file}")

    return build("gmail", "v1", credentials=creds)


class EmailCollector:
    """Polls a Gmail inbox via OAuth2 and extracts newsletter content.

    Usage::

        collector = EmailCollector(
            credentials_file=Path("client_secret.json"),
            token_file=Path("data/gmail_token.json"),
        )
        results = collector.collect()
        for result in results:
            leads = extractor.extract(result)
    """

    def __init__(
        self,
        credentials_file: Path,
        token_file: Path,
        email_address: str = "",
        max_emails: int = 100,
    ):
        self.credentials_file = credentials_file
        self.token_file = token_file
        self.email_address = email_address
        self.max_emails = max_emails
        self._service = None

    def _get_service(self):
        """Lazy-build the Gmail API service."""
        if self._service is None:
            self._service = _build_gmail_service(self.credentials_file, self.token_file)
        return self._service

    def collect(
        self,
        since_date: datetime | None = None,
        mark_read: bool = False,
        dry_run: bool = False,
    ) -> list[FetchResult]:
        """Poll inbox and return FetchResults for each unread email.

        Args:
            since_date: Only fetch emails after this date.
            mark_read: Mark processed emails as read.
            dry_run: Connect and count but don't download content.

        Returns:
            List of FetchResult objects, one per email.
        """
        try:
            service = self._get_service()
        except FileNotFoundError as exc:
            logger.error(str(exc))
            return []
        except Exception as exc:
            logger.error(f"Gmail auth failed: {exc}")
            return []

        # Build search query
        query_parts = ["is:unread"]
        if since_date:
            date_str = since_date.strftime("%Y/%m/%d")
            query_parts.append(f"after:{date_str}")
        query = " ".join(query_parts)

        try:
            # List matching messages
            response = (
                service.users()
                .messages()
                .list(userId="me", q=query, maxResults=self.max_emails)
                .execute()
            )
            messages = response.get("messages", [])

            logger.info(f"Found {len(messages)} unread emails")

            if dry_run:
                return [
                    FetchResult(
                        source_id=0,
                        url=f"email://{self.email_address}/dry-run",
                        content=(f"[DRY RUN] {len(messages)} unread emails found"),
                    )
                ]

            results = []
            for msg_ref in messages[: self.max_emails]:
                msg_id = msg_ref["id"]
                parsed = self._fetch_email(service, msg_id)
                if parsed is None:
                    continue

                result = self._email_to_fetch_result(parsed)
                results.append(result)

                if mark_read:
                    self._mark_read(service, msg_id)

            logger.info(f"Collected {len(results)} emails as FetchResults")
            return results

        except Exception as exc:
            logger.error(f"Gmail API error: {exc}")
            return []

    def _fetch_email(self, service, msg_id: str) -> ParsedEmail | None:
        """Fetch and parse a single email via the Gmail API."""
        try:
            msg = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
            return self._parse_gmail_message(msg)
        except Exception as exc:
            logger.warning(f"Failed to fetch email {msg_id}: {exc}")
            return None

    def _parse_gmail_message(self, msg: dict) -> ParsedEmail:
        """Extract content from a Gmail API message response."""
        headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}

        message_id = headers.get("message-id", msg.get("id", ""))
        subject = headers.get("subject", "")
        sender = headers.get("from", "")
        date = headers.get("date", "")

        # Extract body parts
        text_content = ""
        html_content = ""
        payload = msg.get("payload", {})

        self._extract_parts(payload, text_content, html_content)

        # Walk through parts recursively
        parts_text = []
        parts_html = []
        self._walk_parts(payload, parts_text, parts_html)
        text_content = "\n".join(parts_text)
        html_content = "\n".join(parts_html)

        # Extract links and readable text from HTML
        links = []
        if html_content:
            links = extract_links(html_content, "")
            if not text_content:
                text_content = self._html_to_text(html_content)

        return ParsedEmail(
            message_id=message_id,
            subject=subject,
            sender=sender,
            date=date,
            text_content=text_content,
            html_content=html_content,
            links=links,
        )

    def _walk_parts(
        self,
        payload: dict,
        text_parts: list[str],
        html_parts: list[str],
    ):
        """Recursively walk MIME parts to extract text and HTML."""
        mime_type = payload.get("mimeType", "")
        body = payload.get("body", {})
        data = body.get("data", "")

        if data:
            decoded = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
            if mime_type == "text/plain":
                text_parts.append(decoded)
            elif mime_type == "text/html":
                html_parts.append(decoded)

        # Recurse into sub-parts
        for part in payload.get("parts", []):
            self._walk_parts(part, text_parts, html_parts)

    def _extract_parts(self, payload: dict, text: str, html: str) -> tuple[str, str]:
        """Extract text and HTML from a payload (non-recursive)."""
        body = payload.get("body", {})
        data = body.get("data", "")
        mime = payload.get("mimeType", "")

        if data:
            decoded = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
            if mime == "text/plain":
                text = decoded
            elif mime == "text/html":
                html = decoded

        return text, html

    def _email_to_fetch_result(self, parsed: ParsedEmail) -> FetchResult:
        """Convert a parsed email into a FetchResult for the pipeline."""
        combined_text = f"{parsed.subject}\n\n{parsed.text_content}"

        return FetchResult(
            source_id=0,
            url=f"email://{parsed.sender}/{parsed.message_id}",
            status_code=200,
            content=combined_text,
            content_hash=content_hash(combined_text),
            links=parsed.links,
            fetched_at=datetime.utcnow(),
        )

    def _mark_read(self, service, msg_id: str):
        """Mark an email as read by removing the UNREAD label."""
        try:
            service.users().messages().modify(
                userId="me",
                id=msg_id,
                body={"removeLabelIds": ["UNREAD"]},
            ).execute()
        except Exception as exc:
            logger.warning(f"Failed to mark {msg_id} as read: {exc}")

    @staticmethod
    def _html_to_text(html: str) -> str:
        """Convert HTML to readable plain text."""
        soup = BeautifulSoup(html, "lxml")

        for tag in soup(["script", "style", "head", "nav", "footer"]):
            tag.decompose()

        text = soup.get_text(separator="\n", strip=True)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()
