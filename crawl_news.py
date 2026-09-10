import sys
from pathlib import Path
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_DIR = Path(__file__).resolve().parent
DOCS_DIR = BASE_DIR / "docs"
OUTPUT_FILE = DOCS_DIR / "index.md"

URL = "https://m.etnews.com/news/hot_content_list.html"


class CrawlError(RuntimeError):
    pass


def create_session() -> requests.Session:
    session = requests.Session()

    # 간헐적인 네트워크 문제(ConnectTimeout/5xx) 대응
    retries = Retry(
        total=5,
        connect=5,
        read=5,
        status=5,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        respect_retry_after_header=True,
    )

    adapter = HTTPAdapter(max_retries=retries)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; python_crawler/1.0)",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        }
    )
    return session


def fetch_html(url: str) -> str:
    session = create_session()
    try:
        # connect timeout / read timeout 분리
        response = session.get(url, timeout=(10, 20))
        response.raise_for_status()
        return response.text
    except requests.Timeout as exc:
        raise CrawlError(f"페이지 요청 시간이 초과되었습니다: {url}") from exc
    except requests.RequestException as exc:
        raise CrawlError(f"페이지 요청에 실패했습니다: {url} ({exc})") from exc
    finally:
        session.close()


def parse_top_news(html: str):
    soup = BeautifulSoup(html, 'html.parser')
    section = soup.select_one("section.textthumb")
    if section is None:
        raise CrawlError("크롤링 대상 섹션(section.textthumb)을 찾지 못했습니다.")

    top = section.select('ul li strong a')[:10]
    if not top:
        raise CrawlError("뉴스 목록을 찾지 못했습니다. 페이지 구조가 변경되었을 수 있습니다.")
    return top


def write_markdown(top):
    now = datetime.now().strftime('%Y-%m-%d')
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    lines = [f"# {now} 많이 본 뉴스", ""]
    for i, a in enumerate(top, 1):
        title = a.get_text(strip=True)
        href = a.get('href', '').strip()
        if not title:
            raise CrawlError(f"{i}번째 뉴스 제목이 비어 있습니다.")
        if not href:
            raise CrawlError(f"{i}번째 뉴스 링크를 찾지 못했습니다.")
        lines.append(f"{i}. [{title}]({urljoin(URL, href)})")

    try:
        OUTPUT_FILE.write_text("\n".join(lines) + "\n", encoding='utf-8')
    except OSError as exc:
        raise CrawlError(f"출력 파일을 저장하지 못했습니다: {OUTPUT_FILE}") from exc


def main() -> int:
    try:
        html = fetch_html(URL)
        top_news = parse_top_news(html)
        write_markdown(top_news)
    except CrawlError as exc:
        print(f"[crawler] ERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"[crawler] ERROR: 예상하지 못한 오류가 발생했습니다: {exc}", file=sys.stderr)
        return 1

    print(f"[crawler] Wrote {len(top_news)} items to {OUTPUT_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
