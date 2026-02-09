# Python Web Crawler

BFS 기반 범용 웹 크롤러. 링크를 따라가며 여러 페이지를 자동 수집하고 JSON/CSV로 저장합니다.

## 설치

```bash
pip install -r requirements.txt
```

## 사용법

```bash
# 기본 크롤링 (depth=2, max 100 pages, JSON 출력)
python -m crawler https://example.com

# 깊이 3, 최대 500페이지, CSV 출력
python -m crawler https://example.com -d 3 -n 500 -f csv

# 느린 크롤링 (2초 간격)
python -m crawler https://example.com --delay 2.0

# URL 패턴 필터링
python -m crawler https://example.com --url-pattern "/blog/"

# 상세 로깅
python -m crawler https://example.com -v

# JSON + CSV 동시 출력
python -m crawler https://example.com -f both
```

## CLI 옵션

| 옵션 | 기본값 | 설명 |
|------|--------|------|
| `url` | (필수) | 시작 URL |
| `-d, --max-depth` | 2 | 최대 링크 깊이 |
| `-n, --max-pages` | 100 | 최대 크롤링 페이지 수 |
| `--delay` | 1.0 | 요청 간 대기 시간(초) |
| `--timeout` | 10 | HTTP 타임아웃(초) |
| `--retries` | 2 | 실패 시 재시도 횟수 |
| `--no-robots` | - | robots.txt 무시 |
| `--allow-external` | - | 외부 도메인 허용 |
| `--url-pattern` | - | URL 정규식 필터 (반복 가능) |
| `-f, --format` | json | 출력 형식: json, csv, both |
| `-o, --output-dir` | output | 결과 저장 디렉토리 |
| `-v, --verbose` | - | 상세 로깅 |

## 프로젝트 구조

```
crawler/
├── __init__.py    # 버전 정보
├── __main__.py    # python -m crawler 진입점
├── cli.py         # argparse CLI
├── config.py      # CrawlConfig 설정
├── engine.py      # BFS 크롤 엔진
├── fetcher.py     # HTTP 요청 (재시도, 속도 제한)
├── parser.py      # HTML 파싱 (BeautifulSoup)
├── robots.py      # robots.txt 준수
├── filters.py     # URL 필터링
├── storage.py     # JSON/CSV 저장
└── models.py      # 데이터 모델
```
