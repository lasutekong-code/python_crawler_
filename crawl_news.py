import os
import sys
import tempfile
import time
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_DIR = Path(__file__).resolve().parent
DOCS_DIR = BASE_DIR / "docs"
OUTPUT_FILE = DOCS_DIR / "index.md"
BASE_URL = "https://m.etnews.com"
URL = f"{BASE_URL}/news/hot_content_list.html"

DEFAULT_CONNECT_TIMEOUT = float(os.getenv("CRAWLER_CONNECT_TIMEOUT", "10"))
DEFAULT_READ_TIMEOUT = float(os.getenv("CRAWLER_READ_TIMEOUT", "20"))
DEFAULT_MAX_ATTEMPTS = int(os.getenv("CRAWLER_MAX_ATTEMPTS", "3"))
DEFAULT_BACKOFF_SECONDS = float(os.getenv("CRAWLER_BACKOFF_SECONDS", "1"))
DEFAULT_MAX_BACKOFF_SECONDS = float(os.getenv("CRAWLER_MAX_BACKOFF_SECONDS", "4"))
DEFAULT_RETRY_BUDGET_SECONDS = float(os.getenv("CRAWLER_RETRY_BUDGET_SECONDS", "30"))
TRANSIENT_STATUS_CODES = {408, 429, 500, 502, 503, 504}


class CrawlError(RuntimeError):
    pass


def create_session() -> requests.Session:
    session = requests.Session()
    adapter = HTTPAdapter(
        max_retries=Retry(
            total=0,
            connect=0,
            read=0,
            status=0,
            redirect=0,
            allowed_methods=frozenset(["GET"]),
        )
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; python_crawler/1.0)",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        }
    )
    return session


def _escape_github_annotation(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _write_step_summary(lines) -> None:
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return

    with open(summary_path, "a", encoding="utf-8") as summary_file:
        summary_file.write("\n".join(lines) + "\n")


def _emit_failure_summary(message: str) -> None:
    print(f"::error title=Daily News Crawler failed::{_escape_github_annotation(message)}")
    _write_step_summary(
        [
            "## Daily News Crawler failure",
            "",
            f"- URL: `{URL}`",
            f"- Detail: {message}",
            "- Note: This mitigation retries bounded transient failures only. Persistent upstream/network outages still fail the workflow.",
        ]
    )


def _retry_after_seconds(header_value: str | None) -> float | None:
    if not header_value:
        return None

    try:
        return max(0.0, float(header_value))
    except ValueError:
        pass

    try:
        retry_at = parsedate_to_datetime(header_value)
    except (TypeError, ValueError, IndexError):
        return None

    return max(0.0, retry_at.timestamp() - time.time())


def _classify_connection_error(exc: requests.ConnectionError) -> tuple[str, bool]:
    message = str(exc).lower()
    if "name or service not known" in message or "temporary failure in name resolution" in message:
        return "dns-failure", True
    return "connection-error", True


def _build_failure_message(
    host: str,
    category: str,
    attempt: int,
    max_attempts: int,
    total_elapsed: float,
    detail: str,
) -> str:
    return (
        f"host={host} category={category} attempts={attempt}/{max_attempts} "
        f"elapsed={total_elapsed:.1f}s detail={detail}"
    )


def fetch_html(
    url: str,
    *,
    session_factory=create_session,
    sleep=time.sleep,
    monotonic=time.monotonic,
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
    read_timeout: float = DEFAULT_READ_TIMEOUT,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
    max_backoff_seconds: float = DEFAULT_MAX_BACKOFF_SECONDS,
    retry_budget_seconds: float = DEFAULT_RETRY_BUDGET_SECONDS,
) -> str:
    session = session_factory()
    host = urlparse(url).netloc or url
    started_at = monotonic()
    retry_started_at = None

    try:
        for attempt in range(1, max_attempts + 1):
            try:
                response = session.get(url, timeout=(connect_timeout, read_timeout))
                if response.status_code in TRANSIENT_STATUS_CODES:
                    detail = f"HTTP {response.status_code}"
                    total_elapsed = monotonic() - started_at
                    if retry_started_at is None:
                        retry_started_at = monotonic()
                    retry_elapsed = monotonic() - retry_started_at
                    can_retry = attempt < max_attempts and retry_elapsed < retry_budget_seconds
                    if can_retry:
                        delay = _retry_after_seconds(response.headers.get("Retry-After"))
                        if delay is None:
                            delay = min(backoff_seconds * (2 ** (attempt - 1)), max_backoff_seconds)
                        delay = min(delay, max(0.0, retry_budget_seconds - retry_elapsed))
                        print(
                            f"[crawler] retrying host={host} attempt={attempt}/{max_attempts} "
                            f"category=http-{response.status_code} elapsed={total_elapsed:.1f}s "
                            f"sleep={delay:.1f}s detail={detail}",
                            file=sys.stderr,
                        )
                        response.close()
                        sleep(delay)
                        continue

                    response.close()
                    raise CrawlError(
                        _build_failure_message(
                            host,
                            f"http-{response.status_code}",
                            attempt,
                            max_attempts,
                            total_elapsed,
                            detail,
                        )
                    )

                response.raise_for_status()
                return response.text
            except requests.ConnectTimeout as exc:
                category = "connect-timeout"
                transient = True
                detail = str(exc)
            except requests.ReadTimeout as exc:
                category = "read-timeout"
                transient = True
                detail = str(exc)
            except requests.ConnectionError as exc:
                category, transient = _classify_connection_error(exc)
                detail = str(exc)
            except requests.HTTPError as exc:
                status_code = exc.response.status_code if exc.response is not None else "unknown"
                raise CrawlError(
                    _build_failure_message(
                        host,
                        f"http-{status_code}",
                        attempt,
                        max_attempts,
                        monotonic() - started_at,
                        str(exc),
                    )
                ) from exc
            except requests.RequestException as exc:
                raise CrawlError(
                    _build_failure_message(
                        host,
                        "request-error",
                        attempt,
                        max_attempts,
                        monotonic() - started_at,
                        str(exc),
                    )
                ) from exc

            total_elapsed = monotonic() - started_at
            if retry_started_at is None:
                retry_started_at = monotonic()
            retry_elapsed = monotonic() - retry_started_at
            can_retry = transient and attempt < max_attempts and retry_elapsed < retry_budget_seconds
            if not can_retry:
                raise CrawlError(
                    _build_failure_message(host, category, attempt, max_attempts, total_elapsed, detail)
                )

            delay = min(backoff_seconds * (2 ** (attempt - 1)), max_backoff_seconds)
            delay = min(delay, max(0.0, retry_budget_seconds - retry_elapsed))
            print(
                f"[crawler] retrying host={host} attempt={attempt}/{max_attempts} "
                f"category={category} elapsed={total_elapsed:.1f}s sleep={delay:.1f}s detail={detail}",
                file=sys.stderr,
            )
            sleep(delay)
    finally:
        session.close()

    raise CrawlError(_build_failure_message(host, "retry-exhausted", max_attempts, max_attempts, monotonic() - started_at, "unknown"))


def parse_top_news(html: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    section = soup.select_one("section.textthumb")
    if section is None:
        raise CrawlError("크롤링 대상 섹션(section.textthumb)을 찾지 못했습니다.")

    anchors = section.select("ul li strong a")[:10]
    if not anchors:
        raise CrawlError("뉴스 목록을 찾지 못했습니다. 페이지 구조가 변경되었을 수 있습니다.")

    news_items = []
    for index, anchor in enumerate(anchors, start=1):
        title = anchor.get_text(strip=True)
        href = anchor.get("href", "").strip()
        if not title:
            raise CrawlError(f"{index}번째 뉴스 제목이 비어 있습니다.")
        if not href:
            raise CrawlError(f"{index}번째 뉴스 링크를 찾지 못했습니다.")
        news_items.append((title, urljoin(BASE_URL, href)))

    return news_items


def write_markdown(top_news: list[tuple[str, str]], *, output_file: Path = OUTPUT_FILE) -> Path:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    content_lines = [f"# {today} 많이 본 뉴스", ""]
    content_lines.extend(
        f"{index}. [{title}]({link})" for index, (title, link) in enumerate(top_news, start=1)
    )
    content = "\n".join(content_lines) + "\n"

    temp_file_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            delete=False,
            dir=output_file.parent,
            prefix=f".{output_file.stem}-",
            suffix=".tmp",
        ) as temp_file:
            temp_file.write(content)
            temp_file_path = Path(temp_file.name)
        os.replace(temp_file_path, output_file)
    except OSError as exc:
        if temp_file_path is not None:
            temp_file_path.unlink(missing_ok=True)
        raise CrawlError(f"출력 파일을 저장하지 못했습니다: {output_file}") from exc

    return output_file


def main() -> int:
    try:
        html = fetch_html(URL)
        top_news = parse_top_news(html)
        output_file = write_markdown(top_news)
    except CrawlError as exc:
        message = str(exc)
        print(f"[crawler] ERROR: {message}", file=sys.stderr)
        _emit_failure_summary(message)
        return 1
    except Exception as exc:
        message = f"예상하지 못한 오류가 발생했습니다: {exc}"
        print(f"[crawler] ERROR: {message}", file=sys.stderr)
        _emit_failure_summary(message)
        return 1

    print(f"[crawler] Wrote {len(top_news)} items to {output_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
