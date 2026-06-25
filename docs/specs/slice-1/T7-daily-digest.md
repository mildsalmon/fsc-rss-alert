# T7 — daily digest → feed-ops

의존: T6 (+ audit_log T5, state T2) · Effort(CC): ~15분 · 상태: TODO

## Context
편의 도구의 핵심 신뢰성은 "조용히 죽었는지" 아는 것이다. "알림이 없다"가 정상인지 수집기가
죽은 건지 구분하려면 매일 1회 상태 보고(heartbeat)가 필요하다. T7은 전일 통계 + 소스별 마지막
성공 시각 + 죽은 소스 경고를 운영 채널 `feed-ops`로 보낸다.

## Proposed Change
`feed_collector/cli.py`의 `digest` 서브커맨드 (T6에서 골격 추가, T7에서 본체).
- **집계 소스:** `audit_log`(전일 발송 건수/소스) + `sources.last_success_at` + `consecutive_failures`.
- **digest 내용(→ `feed-ops`):**
  - 소스별: 어제 발송 N건, 마지막 성공 시각(상대 표기 "3시간 전"), 연속 실패 수.
  - **죽은 소스 경고:** `now - last_success_at`가 (interval × K) 초과 또는 평소 발송 주기 대비 장기 0건 → ⚠️ 표시.
  - 헤더: 날짜(KST), 전체 소스 상태 한 줄 요약(정상 N / 경고 M).
- **스케줄(슬라이스1, 로컬):** KST 09:00. 로컬 cron 또는 기존 launchd에 `digest` 항목 1개 추가.
  (슬라이스2에서 EC2 cron으로 이전.)
- **audit_log prune:** digest 실행 시 90일 지난 행 정리(T5 정책 집행 지점).

## Acceptance Criteria
1. `python -m feed_collector digest`가 `feed-ops`에 소스별 전일 발송수 + 마지막 성공 시각 + 죽은소스 경고를 1메시지로 게시.
2. 한 소스가 임계 시간 이상 성공 없으면 digest에 ⚠️ 경고로 명시(죽음 탐지).
3. 발송 0건이어도 digest는 온다(= "조용한 정상"과 "조용한 죽음" 구분 가능).
4. KST 09:00 로컬 스케줄(cron/launchd) 등록 문서화.
5. digest 실행 시 audit_log 90일 prune 동작.

## Testing
| Layer | What | Count |
|-------|------|-------|
| Unit | 집계 쿼리(전일 카운트), 죽은소스 판정 임계, 상대시간 포맷 | +3 |
| Integration | audit_log+sources 채워진 sqlite → digest 메시지 생성(notifier mock) | +1 |

## Files Reference
| File | Change |
|------|--------|
| `feed_collector/cli.py` | `digest` 본체 |
| `feed_collector/digest.py` | 신규: 집계 + 죽은소스 판정 + 메시지 포맷 |
| `launchd/*.plist` 또는 `scripts/` | KST 09:00 digest 스케줄(로컬) |

## Out of Scope
- 실시간 `/health` 엔드포인트(슬라이스2 B워커 시).
- EC2 cron 이전(슬라이스2).

## Rollback
- digest는 읽기 전용 집계 + 1발송. 문제 시 스케줄 항목만 제거(수집 본체에 영향 없음).

## 리뷰 반영 (2026-06-20)
- **로컬 스케줄 구체화(Codex #12):** poll과 digest는 별개 스케줄. poll = launchd `StartInterval`(예 1200s,
  v1 계승) 또는 cron `*/20`. digest = **KST 09:00 = launchd `StartCalendarInterval`{Hour:9,Minute:0}**
  (또는 cron `0 9 * * *`, 단 머신 TZ가 KST인지 확인). 둘 다 `.env` 로딩 + 로그 경로 명시(기존 launchd 패턴 재사용).
  poll/digest가 같은 sqlite를 쓰므로 flock으로 동시 실행 보호(T2).
- **죽음 판정 = `last_success_at` 기준(Codex #5):** `now - last_success_at > interval × K` → ⚠️.
  `last_attempt_at`가 아니라 success 기준이어야 "시도는 하는데 계속 실패"를 죽음으로 잡는다.
- **digest는 dry-run 영향 없음**(읽기 집계). audit_log 90일 prune는 digest 시 1회.
