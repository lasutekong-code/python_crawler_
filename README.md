# python_crawler
깃헙 액션 뉴스 크롤링

전자신문 모바일 많이 본 뉴스 목록을 `docs/index.md`에 기록합니다.

## 동작 방식

- 대상 URL: `https://m.etnews.com/news/hot_content_list.html`
- 기본 타임아웃: 연결 10초 / 응답 읽기 20초
- 기본 재시도: 최대 3회 시도, 1초→2초 지수 백오프, 재시도 예산 30초
- 재시도 대상: 연결/읽기 타임아웃, DNS/연결 오류, `408/429/500/502/503/504`
- 즉시 실패 대상: 그 외 HTTP 오류, 파싱 실패, 빈 제목/링크

크롤링이 실패하면 기존 `docs/index.md`는 유지되며, 새 날짜로 덮어쓰지 않습니다. 성공 시에는 임시 파일에 먼저 쓴 뒤 `docs/index.md`를 원자적으로 교체해 부분 파일이 남지 않도록 합니다.

## 설정

필요하면 아래 환경 변수로 재시도 정책을 조정할 수 있습니다.

- `CRAWLER_CONNECT_TIMEOUT`
- `CRAWLER_READ_TIMEOUT`
- `CRAWLER_MAX_ATTEMPTS`
- `CRAWLER_BACKOFF_SECONDS`
- `CRAWLER_MAX_BACKOFF_SECONDS`
- `CRAWLER_RETRY_BUDGET_SECONDS`

## 진단 방법

실패 시 워크플로 로그와 Step Summary에 다음과 같은 정보가 남습니다.

- `host=...`
- `category=connect-timeout|read-timeout|dns-failure|connection-error|http-...`
- `attempts=현재/최대`
- `elapsed=...s`

확인 순서:

1. GitHub Actions에서 실패한 `Daily News Crawler` 실행을 엽니다.
2. `python crawl_news.py` 단계의 `category`와 `attempts`를 확인합니다.
3. `connect-timeout`/`dns-failure`이면 외부 네트워크 또는 상류 사이트 상태를 의심하고, `http-429/5xx`이면 일시적인 서버 문제 가능성을 봅니다.
4. `section.textthumb` 또는 뉴스 목록 파싱 오류면 사이트 마크업 변경 여부를 확인합니다.

## 한계

- 이 변경은 간헐적인 네트워크 실패를 완화하는 목적이며, 상류 사이트의 지속적인 장애나 차단을 해결했다고 보장하지는 않습니다.
- PR에서는 오프라인 단위 테스트만 실행하고, 실제 대상 사이트 접속과 `docs/index.md` 푸시는 예약/수동 실행에서만 수행합니다.
