"""Auto-subscriber — discovers and submits newsletter signup forms.

Crawls known source URLs looking for newsletter signup forms, RSS feed
links, and email subscription endpoints. Can operate in two modes:

  - **discover**: Find forms and report them (safe, no side effects)
  - **subscribe**: Actually submit the email address to found forms

The discovery logic looks for:
  1. HTML forms with email input fields near "subscribe"/"newsletter" text
  2. RSS/Atom feed links in page headers
  3. Mailchimp, Constant Contact, and similar ESP signup endpoints
  4. "Subscribe" / "Sign up" links in page content
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# Patterns that indicate a newsletter signup form or link
SUBSCRIBE_PATTERNS = re.compile(
    r"(newsletter|subscribe|sign.?up|email.?alert|e.?news|"
    r"mailing.?list|stay.?connected|get.?updates|daily.?digest|"
    r"weekly.?update|join.?our|stay.?informed)",
    re.IGNORECASE,
)

# Known email service provider (ESP) form action patterns
ESP_PATTERNS = [
    re.compile(r"list-manage\.com", re.IGNORECASE),       # Mailchimp
    re.compile(r"mailchimp\.com", re.IGNORECASE),          # Mailchimp
    re.compile(r"constantcontact\.com", re.IGNORECASE),    # Constant Contact
    re.compile(r"campaign-archive\.com", re.IGNORECASE),   # Mailchimp archive
    re.compile(r"sendinblue\.com", re.IGNORECASE),         # Brevo/Sendinblue
    re.compile(r"mailerlite\.com", re.IGNORECASE),         # MailerLite
    re.compile(r"convertkit\.com", re.IGNORECASE),         # ConvertKit
    re.compile(r"hubspot", re.IGNORECASE),                 # HubSpot
    re.compile(r"activecampaign", re.IGNORECASE),          # ActiveCampaign
    re.compile(r"aweber\.com", re.IGNORECASE),             # AWeber
    re.compile(r"getresponse\.com", re.IGNORECASE),        # GetResponse
    re.compile(r"feedburner", re.IGNORECASE),              # FeedBurner
]


@dataclass
class DiscoveredSignup:
    """A discovered newsletter signup opportunity."""

    source_url: str
    signup_url: str
    signup_type: str  # "form", "rss", "link", "esp"
    form_action: str = ""
    email_field_name: str = ""
    extra_fields: dict = field(default_factory=dict)
    esp_name: str = ""
    context_text: str = ""  # Nearby text describing the newsletter
    confidence: float = 0.0  # 0.0-1.0 how confident this is a real signup


@dataclass
class SubscribeResult:
    """Result of attempting to subscribe to a newsletter."""

    signup: DiscoveredSignup
    success: bool
    status_code: int = 0
    response_text: str = ""
    error: str = ""


class AutoSubscriber:
    """Discovers and optionally submits newsletter signup forms.

    Usage::

        subscriber = AutoSubscriber(email="newdealerchecker@gmail.com")

        # Discovery only (safe)
        signups = await subscriber.discover_from_urls(urls)

        # Actually subscribe
        results = await subscriber.subscribe_all(signups)
    """

    def __init__(self, email: str, timeout: int = 15):
        self.email = email
        self.timeout = timeout

    async def discover_from_url(
        self,
        url: str,
        client: httpx.AsyncClient,
    ) -> list[DiscoveredSignup]:
        """Discover signup opportunities on a single page."""
        signups = []

        try:
            resp = await client.get(
                url, headers=BROWSER_HEADERS, timeout=self.timeout
            )
            if resp.status_code != 200:
                logger.debug(f"Got {resp.status_code} from {url}")
                return []

            html = resp.text
            soup = BeautifulSoup(html, "lxml")

            # 1. Find RSS/Atom feeds in page head
            signups.extend(self._find_rss_feeds(soup, url))

            # 2. Find email signup forms
            signups.extend(self._find_signup_forms(soup, url))

            # 3. Find subscribe links
            signups.extend(self._find_subscribe_links(soup, url))

        except Exception as exc:
            logger.warning(f"Discovery failed for {url}: {exc}")

        logger.debug(
            f"Found {len(signups)} signup opportunities on {url}"
        )
        return signups

    async def discover_from_urls(
        self,
        urls: list[str],
        max_concurrent: int = 5,
    ) -> list[DiscoveredSignup]:
        """Discover signup opportunities across multiple URLs."""
        import asyncio

        semaphore = asyncio.Semaphore(max_concurrent)
        all_signups: list[DiscoveredSignup] = []

        async with httpx.AsyncClient(
            follow_redirects=True, timeout=self.timeout
        ) as client:

            async def _discover(url: str):
                async with semaphore:
                    result = await self.discover_from_url(url, client)
                    await asyncio.sleep(1.5)  # Polite delay
                    return result

            tasks = [_discover(u) for u in urls]
            results = await asyncio.gather(
                *tasks, return_exceptions=True
            )

            for r in results:
                if isinstance(r, list):
                    all_signups.extend(r)
                elif isinstance(r, Exception):
                    logger.debug(f"Discovery exception: {r}")

        # Deduplicate by signup URL
        seen = set()
        unique = []
        for s in all_signups:
            key = s.signup_url or s.form_action
            if key and key not in seen:
                seen.add(key)
                unique.append(s)

        logger.info(
            f"Discovered {len(unique)} unique signup "
            f"opportunities from {len(urls)} URLs"
        )
        return unique

    async def subscribe_one(
        self,
        signup: DiscoveredSignup,
        client: httpx.AsyncClient,
        dry_run: bool = False,
    ) -> SubscribeResult:
        """Attempt to subscribe to a single signup form.

        Only submits to forms where we can identify the email field.
        """
        if signup.signup_type == "rss":
            # RSS feeds don't need submission — just register as source
            return SubscribeResult(
                signup=signup,
                success=True,
                response_text="RSS feed — register as source",
            )

        if signup.signup_type == "link":
            # Links need manual clicking
            return SubscribeResult(
                signup=signup,
                success=False,
                response_text="Manual signup link — visit in browser",
            )

        if not signup.form_action or not signup.email_field_name:
            return SubscribeResult(
                signup=signup,
                success=False,
                error="Missing form action or email field name",
            )

        if dry_run:
            return SubscribeResult(
                signup=signup,
                success=True,
                response_text=(
                    f"[DRY RUN] Would POST {self.email} "
                    f"to {signup.form_action}"
                ),
            )

        # Build form data
        form_data = {signup.email_field_name: self.email}
        form_data.update(signup.extra_fields)

        try:
            resp = await client.post(
                signup.form_action,
                data=form_data,
                headers={
                    **BROWSER_HEADERS,
                    "Content-Type": (
                        "application/x-www-form-urlencoded"
                    ),
                    "Referer": signup.source_url,
                },
                timeout=self.timeout,
            )
            success = resp.status_code in (200, 301, 302, 303)
            return SubscribeResult(
                signup=signup,
                success=success,
                status_code=resp.status_code,
                response_text=resp.text[:500],
            )

        except Exception as exc:
            return SubscribeResult(
                signup=signup,
                success=False,
                error=str(exc),
            )

    async def subscribe_all(
        self,
        signups: list[DiscoveredSignup],
        dry_run: bool = False,
        max_concurrent: int = 3,
    ) -> list[SubscribeResult]:
        """Attempt to subscribe to all discovered forms."""
        import asyncio

        semaphore = asyncio.Semaphore(max_concurrent)
        results = []

        async with httpx.AsyncClient(
            follow_redirects=True, timeout=self.timeout
        ) as client:

            async def _subscribe(signup: DiscoveredSignup):
                async with semaphore:
                    result = await self.subscribe_one(
                        signup, client, dry_run=dry_run
                    )
                    await asyncio.sleep(3.0)  # Extra polite for POSTs
                    return result

            # Only attempt form and ESP types
            submittable = [
                s
                for s in signups
                if s.signup_type in ("form", "esp")
                and s.form_action
                and s.email_field_name
            ]

            tasks = [_subscribe(s) for s in submittable]
            raw = await asyncio.gather(
                *tasks, return_exceptions=True
            )

            for r in raw:
                if isinstance(r, SubscribeResult):
                    results.append(r)

        # Add non-submittable results
        for s in signups:
            if s.signup_type in ("rss", "link"):
                results.append(
                    SubscribeResult(
                        signup=s,
                        success=s.signup_type == "rss",
                        response_text=(
                            "RSS — register as source"
                            if s.signup_type == "rss"
                            else "Manual — visit in browser"
                        ),
                    )
                )

        return results

    # ── Discovery methods ───────────────────────────────────────

    def _find_rss_feeds(
        self, soup: BeautifulSoup, base_url: str
    ) -> list[DiscoveredSignup]:
        """Find RSS/Atom feed links in page <head>."""
        signups = []

        for link in soup.find_all(
            "link",
            type=re.compile(r"(rss|atom|xml)", re.IGNORECASE),
        ):
            href = link.get("href", "")
            if href:
                full_url = urljoin(base_url, href)
                title = link.get("title", "RSS Feed")
                signups.append(
                    DiscoveredSignup(
                        source_url=base_url,
                        signup_url=full_url,
                        signup_type="rss",
                        context_text=title,
                        confidence=0.9,
                    )
                )

        # Also check for common RSS URL patterns in links
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            if re.search(
                r"(/feed/?$|/rss/?$|\.rss$|/atom/?$|\.xml$)",
                href,
                re.IGNORECASE,
            ):
                full_url = urljoin(base_url, href)
                signups.append(
                    DiscoveredSignup(
                        source_url=base_url,
                        signup_url=full_url,
                        signup_type="rss",
                        context_text=a_tag.get_text(strip=True)[:100],
                        confidence=0.7,
                    )
                )

        return signups

    def _find_signup_forms(
        self, soup: BeautifulSoup, base_url: str
    ) -> list[DiscoveredSignup]:
        """Find HTML forms that look like newsletter signups."""
        signups = []

        for form in soup.find_all("form"):
            # Check if form or nearby text mentions newsletters
            form_text = form.get_text(separator=" ", strip=True)
            form_action = form.get("action", "")

            # Look for email input fields
            email_input = self._find_email_input(form)
            if not email_input:
                continue

            # Is this form related to newsletters?
            is_newsletter = bool(SUBSCRIBE_PATTERNS.search(form_text))

            # Check form action against known ESPs
            esp_name = ""
            if form_action:
                for pattern in ESP_PATTERNS:
                    if pattern.search(form_action):
                        is_newsletter = True
                        esp_name = pattern.pattern.replace(
                            "\\.", "."
                        ).strip(".")
                        break

            # Also check nearby sibling/parent text
            if not is_newsletter:
                parent = form.parent
                if parent:
                    parent_text = parent.get_text(
                        separator=" ", strip=True
                    )[:500]
                    is_newsletter = bool(
                        SUBSCRIBE_PATTERNS.search(parent_text)
                    )

            if not is_newsletter:
                continue

            # Resolve form action URL
            action_url = urljoin(base_url, form_action) if form_action else base_url

            # Collect hidden fields (often needed for ESP forms)
            extra_fields = {}
            for hidden in form.find_all(
                "input", {"type": "hidden"}
            ):
                name = hidden.get("name", "")
                value = hidden.get("value", "")
                if name:
                    extra_fields[name] = value

            # Determine confidence
            confidence = 0.5
            if esp_name:
                confidence = 0.9
            elif email_input and is_newsletter:
                confidence = 0.7

            signups.append(
                DiscoveredSignup(
                    source_url=base_url,
                    signup_url=base_url,
                    signup_type="esp" if esp_name else "form",
                    form_action=action_url,
                    email_field_name=email_input.get("name", "email"),
                    extra_fields=extra_fields,
                    esp_name=esp_name,
                    context_text=form_text[:200],
                    confidence=confidence,
                )
            )

        return signups

    def _find_subscribe_links(
        self, soup: BeautifulSoup, base_url: str
    ) -> list[DiscoveredSignup]:
        """Find links that point to newsletter signup pages."""
        signups = []

        for a_tag in soup.find_all("a", href=True):
            text = a_tag.get_text(strip=True)
            href = a_tag["href"]

            # Check link text for subscribe patterns
            if not SUBSCRIBE_PATTERNS.search(text) and not SUBSCRIBE_PATTERNS.search(href):
                continue

            # Skip if it's just an anchor or mailto
            if href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue

            full_url = urljoin(base_url, href)

            # Don't include if it's the same page
            if urlparse(full_url).path == urlparse(base_url).path:
                continue

            signups.append(
                DiscoveredSignup(
                    source_url=base_url,
                    signup_url=full_url,
                    signup_type="link",
                    context_text=text[:100],
                    confidence=0.4,
                )
            )

        return signups

    @staticmethod
    def _find_email_input(form: Tag):
        """Find the email input field in a form."""
        # Try type="email" first
        email_input = form.find("input", {"type": "email"})
        if email_input:
            return email_input

        # Try common name patterns
        for name_pattern in [
            "email",
            "EMAIL",
            "e-mail",
            "mail",
            "subscriber",
        ]:
            found = form.find("input", {"name": re.compile(name_pattern, re.IGNORECASE)})
            if found:
                return found

        # Try placeholder text
        for inp in form.find_all("input"):
            placeholder = inp.get("placeholder", "")
            if re.search(r"email|e-mail", placeholder, re.IGNORECASE):
                return inp

        return None
