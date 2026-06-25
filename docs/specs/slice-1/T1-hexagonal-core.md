# T1 — 헥사고날 골격 + 패키지 리네임

의존: 없음 (슬라이스1의 기반) · Effort(CC): ~25분 · 상태: TODO

## Context
v1은 단일 FSC 피드를 `feedparser`로만 처리하는 절차적 구조다. v2는 4\~~5종 수집 메커니즘을
하나의 균일 파이프라인 뒤에 숨기는 헥사고날(ports & adapters)로 간다. T1은 그 **골격**만 세운다 —
어댑터·sqlite·Slack 구현은 후속 task. 코어 로직(diff/신규감지/첫실행/실패처리)을 순수 함수로
추출해두면 T3\~~T7이 이 위에 붙는다.

## Current State (재사용 대상, 이번 세션 확인됨)
- `fsc_rss_alert/app.py:run()` — diff/신규감지/첫실행(116-121)/oldest-first(127)/실패시 비전진(135-140) 로직 보유.
- `fsc_rss_alert/feed.py` — `FeedEntry`(id/title/link/published), fetch/parse 분리.
- `fsc_rss_alert/notify.py` — `Notifier` ABC (= outbound port 원형).
- `fsc_rss_alert/state.py` — seen merge / 실패상태.
- `fsc_rss_alert/config.py` — env 튜너블, 하드코딩 `FEED_URL`.

## Proposed Change
1. **패키지 리네임** `fsc_rss_alert/` → `feed_collector/`. `pyproject.toml`·`main.py`·import 경로·
   launchd plist의 모듈 경로, "FSC RSS" 고정 문자열을 소스명 주입형으로 갱신.
2. **`feed_collector/domain.py`** — `Item` frozen dataclass:
   `item_id: str`, `title: str`, `link: str`, `published: datetime | None`. (published는 datetime 정규화)
3. **`feed_collector/ports.py`** — `typing.Protocol` 4개:
   - `SourcePort.fetch() -> list[Item]`
   - `StatePort` (is_first_run / dedup용 seen 조회 / mark_seen / advance / last_run·last_success 갱신)
   - `NotifierPort.send(channel_id: str, item: Item) -> None`
   - `AuditPort.log(source_id, item) -> None`
4. **`feed_collector/core/poll.py`** + **`core/dedup.py`** — `app.py:run()`의 알맹이를 순수 함수로 추출.
   첫실행 분기·oldest-first·실패시 비전진을 **반드시 보존**. 의사코드는 설계 §Architecture 참조.
   코어는 I/O 라이브러리를 import하지 않는다(순수).
5. `feed.py`/`notify.py`/`state.py`는 T2~T5에서 adapters/로 이동하므로, T1에서는 **인터페이스만** 확정하고
   기존 모듈은 임시로 남겨둔다(컴파일 깨지지 않게).

## Acceptance Criteria
1. `import feed_collector` 동작, `fsc_rss_alert` 잔존 import 없음(`grep -r fsc_rss_alert` = 0, 문서 제외).
2. `Item`·4개 Protocol·`poll()`·`dedup()`가 정의되고 mypy/pyright 타입체크 통과.
3. `poll()` 단위테스트: 첫실행=기준선만 저장·발송0 / 신규=oldest-first 발송 / 발송실패=seen 비전진. (가짜 port 주입)
4. 코어 모듈이 httpx·bs4·feedparser·slack-sdk·urllib을 import하지 않음(grep 확인).

## Testing
| Layer | What | Count |
|-------|------|-------|
| Unit | `poll()` 첫실행/신규/실패 분기, `dedup()` | +5 |

## Files Reference
| File | Change |
|------|--------|
| `feed_collector/` (← `fsc_rss_alert/`) | 디렉토리 리네임 |
| `feed_collector/domain.py` | 신규: `Item` |
| `feed_collector/ports.py` | 신규: 4 Protocol |
| `feed_collector/core/poll.py`, `core/dedup.py` | 신규: 순수 코어 |
| `pyproject.toml`, `main.py`, `launchd/*.plist` | 리네임 반영 |

## Out of Scope
- 어댑터/ sqlite/ Slack 실제 구현 (T2~T5). T1은 인터페이스 + 코어만.

## Rollback
- 순수 추가/리네임. 문제 시 git revert. state.json 포맷 변경 없음(T2에서).

## 리뷰 반영 (2026-06-20)
- **T1a / T1b로 분리(발견 A-2):** T1a = 위 1번(패키지 리네임)만, 기계적·동작불변·독립 커밋.
  T1b = 위 2~5번(Item·ports·core 추출). 구조 변경과 기계적 변경을 한 diff에 섞지 않는다(Beck).
- **`SourceConfig`를 1급 타입으로 추가(T1b, Codex #3):** `feed_collector/domain.py`(또는 config.py)에
  frozen dataclass — id/slug/name/mechanism/parser_version/channel_id/interval_minutes/url/params/
  list_path/detail_url/empty_result_policy. 어댑터·레지스트리·poll이 전부 이 타입에 의존하므로 여기서 확정.
- **`poll()` 시그니처에 소스별 격리 전제(발견3):** 호출측(T6) 루프가 try/except로 감싸므로 `poll()`은
  단일 소스 실패를 예외로 던지면 됨(루프 중단 책임은 호출측). 첫실행/oldest-first/실패시 비전진은 유지.
