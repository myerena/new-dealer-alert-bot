"""Tests for the email collector module."""

from pathlib import Path

from dealer_alert.collectors.email_collector import EmailCollector, ParsedEmail


class TestHtmlToText:
    """Test HTML-to-text conversion used on newsletter bodies."""

    def test_strips_tags(self):
        html = "<p>Hello <b>world</b></p>"
        result = EmailCollector._html_to_text(html)
        assert "Hello" in result
        assert "world" in result
        assert "<p>" not in result

    def test_strips_scripts(self):
        html = "<script>alert('hi')</script><p>Content here</p>"
        result = EmailCollector._html_to_text(html)
        assert "alert" not in result
        assert "Content here" in result

    def test_strips_navigation(self):
        html = "<nav>Menu items</nav><p>The real content</p>"
        result = EmailCollector._html_to_text(html)
        assert "Menu items" not in result
        assert "The real content" in result

    def test_collapses_whitespace(self):
        html = "<p>Line 1</p><br><br><br><br><p>Line 2</p>"
        result = EmailCollector._html_to_text(html)
        assert "\n\n\n" not in result

    def test_empty_input(self):
        assert EmailCollector._html_to_text("") == ""


class TestEmailToFetchResult:
    """Test conversion of parsed emails to FetchResults."""

    def test_combines_subject_and_body(self):
        parsed = ParsedEmail(
            message_id="<test@example.com>",
            subject="Grand Opening: New Dealer in Dallas",
            sender="newsletter@trade.com",
            date="Wed, 18 Mar 2026 10:00:00 -0500",
            text_content="A new dealer has opened in Dallas, TX.",
            html_content="",
            links=["https://example.com/article"],
        )

        collector = EmailCollector(
            credentials_file=Path("/fake/creds.json"),
            token_file=Path("/fake/token.json"),
        )
        result = collector._email_to_fetch_result(parsed)

        assert "Grand Opening" in result.content
        assert "Dallas" in result.content
        assert result.links == ["https://example.com/article"]
        assert result.url.startswith("email://")
