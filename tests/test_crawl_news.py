import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

import crawl_news


class FakeClock:
    def __init__(self):
        self.current = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.current

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.current += seconds


class FakeResponse:
    def __init__(self, *, status_code=200, text="", headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            error = requests.HTTPError(f"{self.status_code} Server Error")
            error.response = self
            raise error

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []
        self.closed = False

    def get(self, url, timeout):
        self.calls.append((url, timeout))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def close(self):
        self.closed = True


class CrawlNewsTests(unittest.TestCase):
    def test_fetch_html_retries_timeout_then_recovers(self):
        clock = FakeClock()
        session = FakeSession(
            [
                requests.ConnectTimeout("connect timeout"),
                FakeResponse(text="<html>ok</html>"),
            ]
        )

        html = crawl_news.fetch_html(
            crawl_news.URL,
            session_factory=lambda: session,
            sleep=clock.sleep,
            monotonic=clock.monotonic,
            max_attempts=3,
            backoff_seconds=1,
            max_backoff_seconds=2,
            retry_budget_seconds=10,
        )

        self.assertEqual(html, "<html>ok</html>")
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(clock.sleeps, [1])
        self.assertTrue(session.closed)

    def test_fetch_html_raises_after_retry_exhaustion(self):
        clock = FakeClock()
        session = FakeSession(
            [
                requests.ConnectTimeout("connect timeout"),
                requests.ConnectTimeout("connect timeout"),
                requests.ConnectTimeout("connect timeout"),
            ]
        )

        with self.assertRaises(crawl_news.CrawlError) as context:
            crawl_news.fetch_html(
                crawl_news.URL,
                session_factory=lambda: session,
                sleep=clock.sleep,
                monotonic=clock.monotonic,
                max_attempts=3,
                backoff_seconds=1,
                max_backoff_seconds=2,
                retry_budget_seconds=10,
            )

        self.assertIn("category=connect-timeout", str(context.exception))
        self.assertIn("attempts=3/3", str(context.exception))
        self.assertEqual(clock.sleeps, [1, 2])
        self.assertTrue(session.closed)

    def test_fetch_html_retries_transient_http_and_fails_fast_on_permanent_http(self):
        clock = FakeClock()
        retry_session = FakeSession(
            [
                FakeResponse(status_code=503),
                FakeResponse(text="<html>recovered</html>"),
            ]
        )

        html = crawl_news.fetch_html(
            crawl_news.URL,
            session_factory=lambda: retry_session,
            sleep=clock.sleep,
            monotonic=clock.monotonic,
            max_attempts=3,
            backoff_seconds=1,
            max_backoff_seconds=2,
            retry_budget_seconds=10,
        )

        self.assertEqual(html, "<html>recovered</html>")
        self.assertEqual(clock.sleeps, [1])
        self.assertTrue(retry_session.closed)

        permanent_clock = FakeClock()
        permanent_session = FakeSession([FakeResponse(status_code=404)])

        with self.assertRaises(crawl_news.CrawlError) as context:
            crawl_news.fetch_html(
                crawl_news.URL,
                session_factory=lambda: permanent_session,
                sleep=permanent_clock.sleep,
                monotonic=permanent_clock.monotonic,
                max_attempts=3,
                backoff_seconds=1,
                max_backoff_seconds=2,
                retry_budget_seconds=10,
            )

        self.assertIn("category=http-404", str(context.exception))
        self.assertEqual(permanent_clock.sleeps, [])
        self.assertTrue(permanent_session.closed)

    def test_parse_top_news_rejects_missing_and_empty_markup(self):
        with self.assertRaises(crawl_news.CrawlError):
            crawl_news.parse_top_news("<html><body></body></html>")

        too_short_html = """
        <section class="textthumb">
          <ul>
            <li><strong><a href="/1">하나</a></strong></li>
          </ul>
        </section>
        """
        with self.assertRaises(crawl_news.CrawlError):
            crawl_news.parse_top_news(too_short_html)

        html = """
        <section class="textthumb">
          <ul>
            <li><strong><a href="">  </a></strong></li>
          </ul>
        </section>
        """
        with self.assertRaises(crawl_news.CrawlError):
            crawl_news.parse_top_news(html)

    def test_write_markdown_writes_expected_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_file = Path(temp_dir) / "docs" / "index.md"
            with patch.object(crawl_news, "datetime") as mocked_datetime:
                mocked_datetime.now.return_value.strftime.return_value = "2026-09-10"
                crawl_news.write_markdown(
                    [
                        ("첫 번째 뉴스", "https://m.etnews.com/1"),
                        ("두 번째 뉴스", "https://m.etnews.com/2"),
                    ],
                    output_file=output_file,
                )

            self.assertEqual(
                output_file.read_text(encoding="utf-8"),
                "# 2026-09-10 많이 본 뉴스\n\n"
                "1. [첫 번째 뉴스](https://m.etnews.com/1)\n"
                "2. [두 번째 뉴스](https://m.etnews.com/2)\n",
            )

    def test_main_preserves_existing_output_on_fetch_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_file = Path(temp_dir) / "docs" / "index.md"
            output_file.parent.mkdir(parents=True, exist_ok=True)
            output_file.write_text("existing content\n", encoding="utf-8")

            with (
                patch.object(crawl_news, "OUTPUT_FILE", output_file),
                patch.object(crawl_news, "URL", "https://m.etnews.com/news/hot_content_list.html"),
                patch.object(crawl_news, "fetch_html", side_effect=crawl_news.CrawlError("network failed")),
            ):
                exit_code = crawl_news.main()

            self.assertEqual(exit_code, 1)
            self.assertEqual(output_file.read_text(encoding="utf-8"), "existing content\n")


if __name__ == "__main__":
    unittest.main()
