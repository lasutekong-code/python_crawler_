import requests
from bs4 import BeautifulSoup
from datetime import datetime
import os
import sys

os.makedirs('docs', exist_ok=True)
URL = "https://m.etnews.com/news/hot_content_list.html"

try:
    res = requests.get(URL, timeout=30)
    res.raise_for_status()
except requests.exceptions.RequestException as e:
    print(f"Error fetching {URL}: {e}", file=sys.stderr)
    sys.exit(1)

soup = BeautifulSoup(res.text, 'html.parser')

section = soup.select_one("section.textthumb")
if section is None:
    print("Error: could not find 'section.textthumb' in the page.", file=sys.stderr)
    sys.exit(1)

top = section.select('ul li strong a')[:10]

now = datetime.now().strftime('%Y-%m-%d')
with open('docs/index.md','w',encoding='utf-8') as f:
    f.write(f"# {now} 많이 본 뉴스\n\n")
    for i, a in enumerate(top, 1):
        title = a.text.strip()
        link = "https://m.etnews.com"+a['href']
        f.write(f"{i}. [{title}]({link})\n")
