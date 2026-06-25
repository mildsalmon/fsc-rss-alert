# T6 — 소스 레지스트리 + CLI poll + 실패분류

의존: T1, T2, T3, T4, T5 (전부 배선) · Effort(CC): ~30분 · 상태: TODO

## Context
T1~T5가 코어·상태·어댑터·알림을 만들었다. T6은 이걸 **하나로 배선**한다: 소스를 config로
선언하고, CLI `poll`이 레지스트리를 순회하며 due 소스만 폴링→코어→알림→감사→상태전진.
"조용한 실패"를 actionable하게 만드는 `FetchFailureReason`도 여기서.

## Proposed Change
1. **소스 레지스트리** `sources.yaml` — 소스별 한 블록(id/slug/name/mechanism/parser_version/
   channel_id/interval_minutes/url/params/list_path/detail_url). 슬라이스1엔 mofa·lawreq 2개.
   로더가 `mechanism`으로 어댑터를 dispatch(rss→RssAdapter, datatables→DataTablesAdapter)하고,
   RSS의 사이트별 HTTP 우회는 `params.fetch_profile`로 fetcher를 선택한다.
2. **CLI 진입점** `feed_collector/cli.py` (또는 `main.py`): `poll`, `digest` 서브커맨드.
   - `poll`: 레지스트리 순회 → **due 게이트**(`now - last_run >= interval_minutes`, sqlite `last_run` 기준) →
     due 소스만 `core.poll()` 호출 → `last_run`/`last_success` 갱신. flock으로 단일 실행(T2).
   - `--dry-run` 유지(발송·상태쓰기 없음, v1 계승).
3. **`feed_collector/errors.py`에 `FetchFailureReason` enum + `infer_from_error(exc)`** (contents-hub 차용):
   `STRUCTURE_CHANGED / BLOCKED / LOGIN_REQUIRED / NOT_FOUND / TIMEOUT / NETWORK / UNKNOWN`.
   예외 텍스트/상태코드를 enum으로 매핑.
4. **즉시 실패 알림(v1 계승 + 분류 연동):** 소스별 `consecutive_failures` 임계 도달 시 `feed-ops`에 1회
   알림(`failure_alert_sent` 중복방지). 알림 강도 차등: `STRUCTURE_CHANGED/BLOCKED/NOT_FOUND`(놓침 위험)는
   즉시, `TIMEOUT/NETWORK`(일시)는 재시도 후 임계에서만.

## Acceptance Criteria
1. `python -m feed_collector poll`이 sources.yaml의 mofa·lawreq를 due일 때만 폴링하고, 신규 항목을
   각 채널에 oldest-first 발송, sqlite seen/last_run/last_success 갱신.
2. due 게이트: interval 미도달 소스는 skip(불필요 fetch 0).
3. 첫 실행은 기준선만 저장·발송0(코어 보존 확인).
4. fetch/파싱/발송 실패 시 해당 소스 seen 비전진 + `FetchFailureReason` 분류 로깅. 임계 도달 시 `feed-ops` 1회 알림.
5. `--dry-run`은 발송·상태쓰기 없이 신규 후보만 출력.
6. 어댑터 dispatch: `mechanism: rss`→RssAdapter, `datatables`→DataTablesAdapter.
   RSS fetcher dispatch: `params.fetch_profile: mofa_cookie_gate`→MofaCookieGateFetcher (config만으로 배선).

## Testing
| Layer | What | Count |
|-------|------|-------|
| Unit | due 게이트, mechanism dispatch, infer_from_error 매핑 | +4 |
| Integration | 2소스 end-to-end poll(어댑터 mock + sqlite + notifier mock) | +2 |
| E2E | 실제 2소스 → 샌드박스 채널 발송(네트워크, 마킹) | +1 |

## Files Reference
| File | Change |
|------|--------|
| `sources.yaml` | 신규: 소스 레지스트리(mofa, lawreq) |
| `feed_collector/registry.py` | 신규: config 로더 + mechanism→adapter dispatch |
| `feed_collector/cli.py` (또는 `main.py`) | poll/digest 서브커맨드, due 게이트 |
| `feed_collector/errors.py` | `FetchFailureReason` + `infer_from_error` |

## Out of Scope
- daily digest 본체는 T7. T6은 즉시 실패알림까지.
- 나머지 5소스·어댑터(슬라이스2).

## Rollback
- CLI는 신규 진입점. 기존 launchd가 가리키는 명령만 갱신(T7 후). 문제 시 v1 명령으로 복귀(단 sqlite 사용).

## 리뷰 반영 (2026-06-20)
- **소스별 격리(발견3=A) — 핵심:** `poll` 루프는 `for src in registry: try: core.poll(src,...) except Exception as e:
  reason = infer_from_error(e); state.record_failure(src, reason); 알림판단; continue`. 한 소스 예외가
  나머지를 막지 않고, 각 소스 last_attempt/last_success 독립 갱신. 죽은 소스는 즉시(임계)+digest(T7)로 통지.
- **due 게이트는 `last_attempt_at` 기준(Codex #5):** `now - last_attempt_at >= interval`. 실패해도 attempt는
  갱신되므로 망가진 소스가 매 tick 두들기지 않음. 죽음 판정은 last_success 기준(T7).
- **`SourceConfig` 로딩(Codex #3):** yaml → `SourceConfig` 인스턴스. `mechanism`으로 어댑터 dispatch,
  `params.fetch_profile`로 fetcher dispatch. channel_id는 sqlite 우선 병합(T5).
  `empty_result_policy`(error|valid) 포함.
- **`--dry-run` 우회 경로 명시(Codex #11):** notifier·audit·채널생성·seen·실패카운터·last_* 쓰기를 전부
  bypass, 신규 후보만 출력. (현재 spec의 "발송·상태쓰기 없음"을 이 수준으로 구체화.)
- **의존성 확정(Codex #2):** `requests`/`python-dateutil`/`PyYAML`/`pytest`/`pyright`, Slack은 직접 HTTP.
  `pyproject.toml`에 추가(T1a 또는 T6).
