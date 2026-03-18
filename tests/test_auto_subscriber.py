"""Tests for the auto-subscriber module."""

from dealer_alert.collectors.auto_subscriber import AutoSubscriber


class TestFindRssFeeds:
    """Test RSS/Atom feed discovery in HTML."""

    def test_finds_rss_link_tag(self):
        sub = AutoSubscriber(email="test@test.com")
        from bs4 import BeautifulSoup

        html = """
        <html><head>
            <link rel="alternate" type="application/rss+xml"
                  title="News Feed" href="/feed/" />
        </head><body></body></html>
        """
        soup = BeautifulSoup(html, "lxml")
        signups = sub._find_rss_feeds(soup, "https://example.com")

        assert len(signups) >= 1
        assert signups[0].signup_type == "rss"
        assert signups[0].signup_url == "https://example.com/feed/"

    def test_finds_rss_link_in_body(self):
        sub = AutoSubscriber(email="test@test.com")
        from bs4 import BeautifulSoup

        html = """
        <html><body>
            <a href="/rss">RSS Feed</a>
        </body></html>
        """
        soup = BeautifulSoup(html, "lxml")
        signups = sub._find_rss_feeds(soup, "https://example.com")

        assert len(signups) >= 1
        assert "rss" in signups[0].signup_url


class TestFindSignupForms:
    """Test newsletter signup form discovery."""

    def test_finds_mailchimp_form(self):
        sub = AutoSubscriber(email="test@test.com")
        from bs4 import BeautifulSoup

        html = """
        <form action="https://company.us5.list-manage.com/subscribe/post"
              method="POST">
            <label>Subscribe to our newsletter</label>
            <input type="email" name="EMAIL" placeholder="Your email">
            <input type="hidden" name="u" value="abc123">
            <button type="submit">Subscribe</button>
        </form>
        """
        soup = BeautifulSoup(html, "lxml")
        signups = sub._find_signup_forms(soup, "https://example.com")

        assert len(signups) == 1
        assert signups[0].signup_type == "esp"
        assert signups[0].email_field_name == "EMAIL"
        assert signups[0].confidence >= 0.8
        assert "u" in signups[0].extra_fields

    def test_finds_generic_newsletter_form(self):
        sub = AutoSubscriber(email="test@test.com")
        from bs4 import BeautifulSoup

        html = """
        <div>
            <h3>Get our weekly newsletter</h3>
            <form action="/subscribe" method="POST">
                <input type="email" name="email"
                       placeholder="Enter your email">
                <button>Sign Up</button>
            </form>
        </div>
        """
        soup = BeautifulSoup(html, "lxml")
        signups = sub._find_signup_forms(soup, "https://example.com")

        assert len(signups) == 1
        assert signups[0].signup_type == "form"
        assert signups[0].email_field_name == "email"

    def test_ignores_login_form(self):
        sub = AutoSubscriber(email="test@test.com")
        from bs4 import BeautifulSoup

        html = """
        <form action="/login" method="POST">
            <input type="email" name="email">
            <input type="password" name="password">
            <button>Log In</button>
        </form>
        """
        soup = BeautifulSoup(html, "lxml")
        signups = sub._find_signup_forms(soup, "https://example.com")

        # Should not match — no newsletter-related text
        assert len(signups) == 0


class TestFindSubscribeLinks:
    """Test discovery of subscribe/signup links."""

    def test_finds_subscribe_link(self):
        sub = AutoSubscriber(email="test@test.com")
        from bs4 import BeautifulSoup

        html = """
        <a href="/newsletter-signup">Subscribe to Newsletter</a>
        """
        soup = BeautifulSoup(html, "lxml")
        signups = sub._find_subscribe_links(
            soup, "https://example.com"
        )

        assert len(signups) >= 1
        assert signups[0].signup_type == "link"
        assert "newsletter-signup" in signups[0].signup_url

    def test_ignores_same_page_anchor(self):
        sub = AutoSubscriber(email="test@test.com")
        from bs4 import BeautifulSoup

        html = """
        <a href="#newsletter">Subscribe</a>
        """
        soup = BeautifulSoup(html, "lxml")
        signups = sub._find_subscribe_links(
            soup, "https://example.com"
        )

        assert len(signups) == 0
