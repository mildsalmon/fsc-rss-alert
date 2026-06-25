# T3 — RSS 어댑터 + MOFA fetch profile

의존: T1 · Effort(CC): ~15분 · 상태: TODO

## Context
MOFA 독자제재는 7소스 중 유일한 네이티브 RSS다. v1의 `feed.py`(feedparser + fetch/parse 분리)를
90% 재사용한다. 함정 하나: MOFA의 모든 경로가 path-scoped `TMOSHCooKie`를 set하기 전엔 HTTP 307로
자기 자신에 무한 리다이렉트한다.

경계: `RssAdapter`는 RSS 메커니즘만 안다. 외교부의 쿠키 이름/307 우회 절차는 RSS 일반 지식이 아니므로
어댑터 본체가 아니라 소스별 fetch profile이 가진다.

## Verified Source (조사 확정)
- 피드 URL: `https://www.mofa.go.kr/www/brd/rss.do?brdId=235` → `application/rss+xml` (~84KB).
- 항목 링크: `m_4080/view.do?seq=NNNNNN`. `<guid>`/`<link>` 존재 → 안정키 사용(content-hash 폴백 불필요).
- captcha/Cloudflare 없음. 마찰은 307 쿠키 게이트뿐.

## Proposed Change
- `feed_collector/adapters/rss.py` — `RssAdapter(SourcePort)`.
- `feed_collector/adapters/http_fetch.py` — `DefaultHttpFetcher`, `MofaCookieGateFetcher` 같은 바이트 fetch profile.

- **fetch/parse 분리 유지(v1 계승):** `RssAdapter`는 주입받은 fetcher로 RSS 바이트를 받고, 받은 바이트를
  `feedparser`에 넘긴다. feedparser 자체는 사이트별 쿠키 핸드셰이크를 못 하므로 fetch 단계는 분리한다.
- **MOFA 2-hit은 fetch profile 책임:** `MofaCookieGateFetcher`가 `requests.Session` 쿠키자를 쓰고,
  첫 요청을 cookie seed로 사용한 뒤 같은 세션으로 재요청한다. `TMOSHCooKie` 같은 외교부 세부값은 이
  클래스나 소스 설정에만 존재해야 하며 `RssAdapter`에는 등장하지 않는다.
- 레지스트리는 `mechanism: rss`로 `RssAdapter`를 만들고, `params.fetch_profile: mofa_cookie_gate` 같은
  소스 설정으로 fetcher를 선택한다. `source.id == "mofa"` 조건문은 피한다.
- 브라우저형 `User-Agent` 유지. timeout·retry는 v1 값 계승.
- `feedparser` 엔트리 → `Item`(item_id=guid/link, title, link, published=`dateutil`로 datetime 파싱).
- config 주도: 피드 URL·fetch profile·empty_result_policy를 SourceConfig에서 받음(하드코딩 금지).

## Acceptance Criteria
1. MOFA fetch profile을 주입받은 `RssAdapter.fetch()`가 MOFA 피드에서 `list[Item]`(≥1) 반환,
   item_id=guid/link, published=datetime.
2. MOFA fetch profile이 쿠키 미보유 첫 요청의 307 루프를 2-hit으로 통과(쿠키자 재사용 확인).
3. `RssAdapter` 코드에는 `TMOSHCooKie`나 `mofa` 분기가 없다.
4. fetch 실패(timeout/5xx) 시 `PollError` 계열 예외 → 코어가 seen 비전진(누락 방지).
5. 파싱 0건이면 예외(조용한 0건 금지) → T6의 FetchFailureReason으로 분류.

## Testing
| Layer | What | Count |
|-------|------|-------|
| Unit | feedparser 엔트리→Item 매핑, published datetime 파싱, fetcher 주입 | +3 |
| Unit | MOFA fetch profile 307→cookie seed→재요청 흐름(requests mock) | +1 |
| Integration | 실제 MOFA 피드 2-hit fetch(네트워크 필요, 마킹) | +1 |

## Files Reference
| File | Change |
|------|--------|
| `feed_collector/adapters/rss.py` | 신규: `RssAdapter` (v1 `parse_entries` 로직 이식) |
| `feed_collector/adapters/http_fetch.py` | 신규: 기본 fetcher + MOFA cookie-gate fetcher |

## Out of Scope
- RSS 이외 소스 처리. FSC 보도자료 피드는 슬라이스1 대상 아님.

## Rollback
- 신규 어댑터. 문제 시 소스 레지스트리에서 mofa 항목 제거하면 비활성.

## 리뷰 반영 (2026-06-20)
- **MOFA 2-hit을 fetch profile 알고리즘으로 명시(Codex #8):** "allow_redirects=True"는 307 self-loop에서 max-redirects를
  칠 수 있어 부정확. 정확한 절차:
  1) `session.get(url, allow_redirects=False)` → 307 응답의 `Set-Cookie`(TMOSHCooKie)를 세션 쿠키자에 저장.
  2) 같은 세션으로 `session.get(url)` 재요청(이제 쿠키 보유 → 200). 리다이렉트는 bounded(max 3).
  쿠키가 이미 있으면 1번 생략. 2회 시도 후에도 200이 아니면 예외(→ FetchFailureReason).
- **0건 정책(Codex #6):** MOFA는 `empty_result_policy: error`(0건=구조변경 의심). SourceConfig로 받음.
- **책임 분리:** `RssAdapter`는 cookie name을 모른다. MOFA의 쿠키 이름은 `MofaCookieGateFetcher`나
  `SourceConfig.params`에만 둔다.
