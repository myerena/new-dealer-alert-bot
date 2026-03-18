"""Email collector — polls a Gmail inbox via IMAP for newsletter content.

Connects to a dedicated inbox (e.g., newdealerchecker@gmail.com) that
subscribes to trade newsletters, chamber digests, and dealer association
bulletins. Extracts article text and links from HTML emails and feeds
them into the standard lead extraction pipeline.

Requires an App Password for Gmail (not the account password):
  1. Enable 2FA on the Gmail account
  2. Go to https://myaccount.google.com/apppasswords
  3. Generate an app password for "Mail"
  4. Set GMAIL_APP_PASSWORD in .env
"""

from __future__ import annotations

import contextlib
import email
import email.policy
import imaplib
import logging
import re
from datetime import datetime
from email.message import EmailMessage
from typing import NamedTuple

from bs4 import BeautifulSoup

from ..fetcher.base import content_hash, extract_links
from ..models import FetchResult

logger = logging.getLogger(__name__)


class ParsedEmail(NamedTuple):
    """Parsed email content ready for lead extraction."""

    message_id: str
    subject: str
    sender: str
    date: datetime | None
    text_content: str
    html_content: str
    links: list[str]


class EmailCollector:
    """Polls a Gmail inbox via IMAP and extracts newsletter content.

    Usage::

        collector = EmailCollector(
            email_address="newdealerchecker@gmail.com",
            app_password="xxxx xxxx xxxx xxxx",
        )
        results = collector.collect(mark_read=True)
        for result in results:
            leads = extractor.extract(result)
    """

    IMAP_HOST = "imap.gmail.com"
    IMAP_PORT = 993

    def __init__(
        self,
        email_address: str,
        app_password: str,
        imap_host: str = "",
        imap_port: int = 0,
        folder: str = "INBOX",
        max_emails: int = 100,
    ):
        self.email_address = email_address
        self.app_password = app_password
        self.imap_host = imap_host or self.IMAP_HOST
        self.imap_port = imap_port or self.IMAP_PORT
        self.folder = folder
        self.max_emails = max_emails

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
        if dry_run:
            return self._dry_run_collect(since_date)

        results = []
        try:
            conn = self._connect()
            message_ids = self._search_unread(conn, since_date)
            logger.info(
                f"Found {len(message_ids)} unread emails in {self.folder}"
            )

            for msg_id in message_ids[: self.max_emails]:
                parsed = self._fetch_email(conn, msg_id)
                if parsed is None:
                    continue

                result = self._email_to_fetch_result(parsed)
                results.append(result)

                if mark_read:
                    self._mark_read(conn, msg_id)

            conn.close()
            conn.logout()

        except imaplib.IMAP4.error as exc:
            logger.error(f"IMAP error: {exc}")
        except Exception as exc:
            logger.error(f"Email collection error: {exc}")

        logger.info(f"Collected {len(results)} emails as FetchResults")
        return results

    def _connect(self) -> imaplib.IMAP4_SSL:
        """Connect and authenticate to the IMAP server."""
        logger.debug(f"Connecting to {self.imap_host}:{self.imap_port}")
        conn = imaplib.IMAP4_SSL(self.imap_host, self.imap_port)
        conn.login(self.email_address, self.app_password)
        conn.select(self.folder, readonly=False)
        return conn

    def _search_unread(
        self,
        conn: imaplib.IMAP4_SSL,
        since_date: datetime | None = None,
    ) -> list[bytes]:
        """Search for unread messages, optionally since a date."""
        criteria = ["UNSEEN"]
        if since_date:
            date_str = since_date.strftime("%d-%b-%Y")
            criteria.append(f"SINCE {date_str}")

        search_str = "(" + " ".join(criteria) + ")"
        status, data = conn.search(None, search_str)

        if status != "OK" or not data[0]:
            return []

        return data[0].split()

    def _fetch_email(
        self,
        conn: imaplib.IMAP4_SSL,
        msg_id: bytes,
    ) -> ParsedEmail | None:
        """Fetch and parse a single email message."""
        try:
            status, data = conn.fetch(msg_id, "(RFC822)")
            if status != "OK" or not data[0]:
                return None

            raw = data[0][1]
            msg = email.message_from_bytes(
                raw, policy=email.policy.default
            )
            return self._parse_message(msg)

        except Exception as exc:
            logger.warning(f"Failed to parse email {msg_id}: {exc}")
            return None

    def _parse_message(self, msg: EmailMessage) -> ParsedEmail:
        """Extract useful content from an email message."""
        message_id = msg.get("Message-ID", "")
        subject = msg.get("Subject", "")
        sender = msg.get("From", "")
        date = msg.get("Date")

        # Parse date
        parsed_date = None
        if date:
            with contextlib.suppress(TypeError, ValueError):
                parsed_date = email.utils.parsedate_to_datetime(str(date))

        text_content = ""
        html_content = ""

        if msg.is_multipart():
            for part in msg.walk():
                ct = part.get_content_type()
                if ct == "text/plain":
                    text_content += part.get_content() or ""
                elif ct == "text/html":
                    html_content += part.get_content() or ""
        else:
            ct = msg.get_content_type()
            body = msg.get_content() or ""
            if ct == "text/plain":
                text_content = body
            elif ct == "text/html":
                html_content = body

        # Extract links and readable text from HTML
        links = []
        if html_content:
            links = extract_links(html_content, "")
            # If no plain text, extract from HTML
            if not text_content:
                text_content = self._html_to_text(html_content)

        return ParsedEmail(
            message_id=message_id,
            subject=subject,
            sender=sender,
            date=parsed_date,
            text_content=text_content,
            html_content=html_content,
            links=links,
        )

    def _email_to_fetch_result(self, parsed: ParsedEmail) -> FetchResult:
        """Convert a parsed email into a FetchResult for the pipeline."""
        # Combine subject + body for extraction
        combined_text = f"{parsed.subject}\n\n{parsed.text_content}"

        return FetchResult(
            source_id=0,  # Email sources get ID 0 (inbox)
            url=f"email://{parsed.sender}/{parsed.message_id}",
            status_code=200,
            content=combined_text,
            content_hash=content_hash(combined_text),
            links=parsed.links,
            fetched_at=parsed.date or datetime.utcnow(),
        )

    def _mark_read(self, conn: imaplib.IMAP4_SSL, msg_id: bytes):
        """Mark an email as read (SEEN)."""
        conn.store(msg_id, "+FLAGS", "\\Seen")

    def _dry_run_collect(
        self, since_date: datetime | None
    ) -> list[FetchResult]:
        """Connect, count unread, but don't download."""
        try:
            conn = self._connect()
            message_ids = self._search_unread(conn, since_date)
            count = len(message_ids)
            conn.close()
            conn.logout()
            logger.info(f"[DRY RUN] Found {count} unread emails")
            return [
                FetchResult(
                    source_id=0,
                    url=f"email://{self.email_address}/dry-run",
                    content=f"[DRY RUN] {count} unread emails found",
                )
            ]
        except Exception as exc:
            logger.error(f"[DRY RUN] Email connection failed: {exc}")
            return []

    @staticmethod
    def _html_to_text(html: str) -> str:
        """Convert HTML to readable plain text."""
        soup = BeautifulSoup(html, "lxml")

        # Remove script, style, and header elements
        for tag in soup(["script", "style", "head", "nav", "footer"]):
            tag.decompose()

        text = soup.get_text(separator="\n", strip=True)

        # Collapse multiple newlines
        text = re.sub(r"\n{3,}", "\n\n", text)

        return text.strip()
