import os
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

os.makedirs('docs', exist_ok=True)

URL = "https://m.etnews.com/news/hot_content_list.html"


def create_session() -> requests.Session:
    session = requests.Session()

    # 간헐적인 네트워크 문제(ConnectTimeout/5xx) 대응
    retries = Retry(
        total=5,
        connect=5,
        read=5,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
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
    # connect timeout / read timeout 분리
    response = session.get(url, timeout=(10, 20))
    response.raise_for_status()
    return response.text


def parse_top_news(html: str):
    soup = BeautifulSoup(html, 'html.parser')
    section = soup.select_one("section.textthumb")
    if section is None:
        raise RuntimeError("크롤링 대상 섹션(section.textthumb)을 찾지 못했습니다.")

    top = section.select('ul li strong a')[:10]
    if not top:
        raise RuntimeError("뉴스 목록을 찾지 못했습니다. 페이지 구조가 변경되었을 수 있습니다.")
    return top


def write_markdown(top):
    now = datetime.now().strftime('%Y-%m-%d')
    with open('docs/index.md', 'w', encoding='utf-8') as f:
        f.write(f"# {now} 많이 본 뉴스\n\n")
        for i, a in enumerate(top, 1):
            title = a.text.strip()
            href = a.get('href', '').strip()
            link = f"https://m.etnews.com{href}" if href.startswith('/') else href
            f.write(f"{i}. [{title}]({link})\n")


if __name__ == "__main__":
    html = fetch_html(URL)
    top_news = parse_top_news(html)
    write_markdown(top_news)
