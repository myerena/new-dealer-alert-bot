"""Tests for the social media monitor module."""


from dealer_alert.collectors.social_monitor import (
    FacebookScraper,
    SocialMonitor,
    SocialPost,
    XTwitterScraper,
)


class TestPlatformDetection:
    """Test URL-to-platform mapping."""

    def test_facebook(self):
        monitor = SocialMonitor()
        assert monitor.detect_platform(
            "https://www.facebook.com/SomeDealership"
        ) == "facebook"
        assert monitor.detect_platform(
            "https://fb.com/page"
        ) == "facebook"

    def test_instagram(self):
        monitor = SocialMonitor()
        assert monitor.detect_platform(
            "https://www.instagram.com/dealer_name/"
        ) == "instagram"

    def test_linkedin(self):
        monitor = SocialMonitor()
        assert monitor.detect_platform(
            "https://www.linkedin.com/company/some-dealer/"
        ) == "linkedin"

    def test_twitter_x(self):
        monitor = SocialMonitor()
        assert monitor.detect_platform(
            "https://twitter.com/SomeDealer"
        ) == "x_twitter"
        assert monitor.detect_platform(
            "https://x.com/SomeDealer"
        ) == "x_twitter"

    def test_unknown(self):
        monitor = SocialMonitor()
        assert monitor.detect_platform(
            "https://www.example.com/"
        ) is None


class TestFacebookScraper:
    """Test Facebook URL conversion."""

    def test_mobile_url_conversion(self):
        scraper = FacebookScraper()
        assert scraper._to_mobile_url(
            "https://www.facebook.com/SomePage"
        ) == "https://mbasic.facebook.com/SomePage"

    def test_mobile_url_already_mobile(self):
        scraper = FacebookScraper()
        result = scraper._to_mobile_url(
            "https://mbasic.facebook.com/SomePage"
        )
        assert "mbasic.facebook.com" in result


class TestXTwitterScraper:
    """Test Twitter/X username extraction."""

    def test_twitter_url(self):
        assert XTwitterScraper._extract_username(
            "https://twitter.com/DealerBob"
        ) == "DealerBob"

    def test_x_url(self):
        assert XTwitterScraper._extract_username(
            "https://x.com/DealerBob"
        ) == "DealerBob"

    def test_with_at_sign(self):
        assert XTwitterScraper._extract_username(
            "https://twitter.com/@DealerBob"
        ) == "DealerBob"

    def test_invalid_url(self):
        assert XTwitterScraper._extract_username(
            "https://example.com/nottwitter"
        ) == ""


class TestPostsToFetchResult:
    """Test converting social posts to FetchResult."""

    def test_empty_posts(self):
        scraper = FacebookScraper()
        result = scraper.posts_to_fetch_result(
            "https://facebook.com/page", []
        )
        assert result.content == ""

    def test_combines_post_text(self):
        scraper = FacebookScraper()
        posts = [
            SocialPost(
                platform="facebook",
                profile_url="https://facebook.com/page",
                post_text="Grand opening this Saturday!",
                post_url="https://facebook.com/post/1",
            ),
            SocialPost(
                platform="facebook",
                profile_url="https://facebook.com/page",
                post_text="Now hiring sales associates",
                post_url="https://facebook.com/post/2",
            ),
        ]
        result = scraper.posts_to_fetch_result(
            "https://facebook.com/page", posts
        )
        assert "Grand opening" in result.content
        assert "Now hiring" in result.content
        assert len(result.links) == 2
