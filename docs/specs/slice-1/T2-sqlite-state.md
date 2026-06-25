# T2 — SqliteStateRepo + 스키마 + dedup

의존: T1 · Effort(CC): ~25분 · 상태: TODO

## Context
v1은 단일 `state.json`에 seen_ids 50개를 캡 리스트로 저장한다. 7소스로 가면 소스별 격리 +
"마지막 성공 시각" + 발송 로그가 필요해 sqlite로 옮긴다. cron이 매 tick `docker run`(슬라이스2)
또는 로컬 반복 실행하므로 **동시성**(flock+WAL)도 여기서 처리한다.

## Proposed Change
`feed_collector/adapters/state_sqlite.py` — `StatePort` 구현.

### 스키마
```sql
CREATE TABLE sources (
  id TEXT PRIMARY KEY, name TEXT, mechanism TEXT, parser_version INTEGER,
  channel_id TEXT, interval_minutes INTEGER,
  last_run_at TEXT, last_success_at TEXT,
  consecutive_failures INTEGER DEFAULT 0, failure_alert_sent INTEGER DEFAULT 0
);
CREATE TABLE seen_items (
  source_id TEXT, item_id TEXT, first_seen_at TEXT,
  PRIMARY KEY (source_id, item_id)
);
CREATE TABLE audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id TEXT, item_id TEXT, title TEXT, channel_id TEXT,
  slack_ts TEXT, sent_at TEXT, status TEXT
);
```
- **단일 seen 모델**: `seen_items` 테이블이 진실원(v1 캡 리스트 아님). 소스당 최근 N개로 prune.
- **dedup 키 전략**(`core/dedup.py`와 연동): 안정키 우선(RSS guid/link, JSON idx) →
  없으면 `content://{source_id}/<sha256(title|body|published)>`. `UNIQUE(source_id,item_id)`로 DB 강제.
- **동시성**: `PRAGMA journal_mode=WAL`, `PRAGMA busy_timeout=5000`. poll 진입 시 전역 `flock`
  (예: `/tmp/feed_collector.lock`) 획득 실패하면 즉시 종료(직전 tick 진행 중).
- **마이그레이션**: 기존 `state.json`의 seen_ids → `seen_items`(source_id는 기존 FSC 소스 id로). 1회성 스크립트.
- sqlite 파일 경로는 config/env로(기본 `./feed.db`, 슬라이스2에서 호스트 `/data/feed.db` bind-mount).

## Acceptance Criteria
1. `SqliteStateRepo`가 `StatePort`를 만족(mypy 통과). 첫 호출 시 스키마 자동 생성.
2. 소스별 seen 격리: source A의 item_id가 source B 신규감지에 영향 없음.
3. content-hash 폴백: link/guid 없는 Item도 안정적으로 같은 키 생성(동일 입력 → 동일 해시).
4. `UNIQUE(source_id,item_id)` 위반 insert가 중복 발송을 막음(이미 seen이면 skip).
5. flock 동시 실행: 두 번째 프로세스가 락 못 잡으면 0건 처리 후 정상 종료(에러 아님).
6. `state.json` → sqlite 마이그레이션 후 기존 seen이 보존됨.

## Testing
| Layer | What | Count |
|-------|------|-------|
| Unit | dedup 안정키/content-hash, 소스 격리, UNIQUE 멱등 | +4 |
| Integration | flock 동시 실행, state.json 마이그레이션 | +2 |

## Files Reference
| File | Change |
|------|--------|
| `feed_collector/adapters/state_sqlite.py` | 신규: `SqliteStateRepo` |
| `feed_collector/core/dedup.py` | item_id 결정 로직(안정키+content-hash) |
| `scripts/migrate_state_json_to_sqlite.py` | 신규: 1회성 마이그레이션 |

## Out of Scope
- audit_log **쓰기**는 T5(AuditPort 구현). T2는 테이블 정의만.
- 호스트 `/data` bind-mount·EBS는 슬라이스2.

## Rollback
- sqlite는 신규 파일. 문제 시 sqlite 삭제 후 재시작(첫실행이 기준선 재구축).

## 리뷰 반영 (2026-06-20)
- **state.json 마이그레이션 제거(Codex #13):** 슬라이스1 소스는 MOFA+법령해석이지 옛 FSC press RSS가
  아니다. 옛 seen을 "legacy FSC source"로 옮기는 건 슬라이스1 동작을 보호하지 않으니 **하지 않는다.**
  옛 state.json은 손대지 않고 그대로 둔다. (`scripts/migrate_*` 삭제.)
- **`last_attempt_at` vs `last_success_at` 분리(Codex #5):** `sources`에서 `last_run_at` 폐기 →
  `last_attempt_at`(매 시도 갱신, due 게이트 기준), `last_success_at`(성공만 갱신, 죽음탐지 기준).
  실패는 success를 전진 안 시킴 → 망가진 소스가 매 tick 두들기지 않게 attempt 기준 backoff 가능.
- **`StatePort` 메서드를 구체화(Codex #4):** `is_first_run(source_id)`, `seen_contains(source_id, item_id)`
  /`filter_new(source_id, items)`, `mark_seen(source_id, item_id, slack_ts)`, `record_attempt(source_id)`,
  `record_success(source_id)`, `record_failure(source_id, reason)`, `get_channel_id`/`set_channel_id`,
  `audit(...)`(T5), `digest_counts(since)`(T7). 트랜잭션 경계 1회 commit. dry-run은 쓰기 전부 skip.
- **dedup 안정성:** content-hash는 `published`가 None일 수 있으니 `title|body|published_or_empty`로 정규화(결정적).
