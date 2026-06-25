# T5 — SlackBotNotifier (채널 자동생성) + AuditLog

의존: T1, T2 · Effort(CC): ~25분 · 상태: TODO

## Context
v1은 단일 `SLACK_WEBHOOK_URL`로 한 채널에만 쏜다. v2는 소스별 채널 1:1이 요구사항이라
webhook-per-source를 폐기하고 **bot token + `chat.postMessage`**로 간다. 채널은 봇이 `feed-{slug}`
규칙으로 **자동 생성**한다(소스 확장 시 Slack 수동 단계 최소화). 발송 로그(AuditPort)도 여기서 구현
(운영용 경량, 90일 prune — 컴플라이언스 등급 보존 아님).

## Proposed Change
`feed_collector/adapters/slack_bot.py` — `SlackBotNotifier(NotifierPort)`.
- **발송:** `chat.postMessage`로 `channel_id`에 전송. 메시지 포맷은 v1 `format_entry_message` 계승
  (제목/날짜/링크). bot token은 env(`SLACK_BOT_TOKEN`).
- **채널 자동생성(멱등):** 소스에 `channel_id`가 없으면 `conversations.create(name="feed-{slug}")` →
  받은 id를 `sources.channel_id`에 저장(T2). 이미 있으면(`name_taken`) `conversations.list`로 조회해 재사용.
  봇은 자기가 만든 public 채널에 자동 입장. **사람 초대는 사람이** 함(봇은 생성+입장까지만).
- **스코프:** `chat:write` + `channels:manage` + `channels:read`.
- **발송 실패 처리:** 4xx/5xx 시 예외 → 코어가 seen 비전진(누락 방지). `not_in_channel`/`channel_not_found`은
  채널 재확인 후 1회 재시도.

`feed_collector/adapters/audit_sqlite.py` — `SqliteAuditLog(AuditPort)`.
- 발송 성공 시 `audit_log`에 (source_id, item_id, title, channel_id, slack_ts, sent_at, status) 기록.
  `slack_ts`는 `chat.postMessage` 응답의 `ts`(재발송 dedup·향후 스레드/편집용).
- 90일 지난 행 prune(digest 또는 poll 시 가벼운 정리).

### 슬라이스1 채널
| 소스 | 채널명 |
|------|--------|
| MOFA 독자제재 | `feed-mofa-sanctions` |
| 금융위 법령해석 | `feed-fsc-lawreq` |
| 운영(digest+실패알림) | `feed-ops` |

## Acceptance Criteria
1. `SlackBotNotifier.send(channel_id, item)`가 해당 채널에 메시지 게시, 응답 `ts` 반환.
2. 채널 자동생성 멱등: 없으면 생성, 있으면 기존 id 재사용(`name_taken` 처리). channel_id가 sqlite에 저장됨.
3. 발송 실패 시 예외 전파 → 코어가 seen 비전진(재시도 시 누락 없음).
4. `SqliteAuditLog`가 성공 발송마다 `ts` 포함 1행 기록. 90일 prune 동작.
5. 봇 미초대 채널(`not_in_channel`) 시 재입장 후 재시도, 그래도 실패면 명확한 에러.

## Testing
| Layer | What | Count |
|-------|------|-------|
| Unit | 메시지 포맷, 채널명 규칙, audit 기록/prune (Slack API mock) | +4 |
| Integration | 실제 워크스페이스에 채널 생성+발송(샌드박스 채널, 마킹) | +1 |

## Files Reference
| File | Change |
|------|--------|
| `feed_collector/adapters/slack_bot.py` | 신규: `SlackBotNotifier` |
| `feed_collector/adapters/audit_sqlite.py` | 신규: `SqliteAuditLog` |
| `feed_collector/notify.py` (v1) | `Notifier` ABC는 T1에서 `NotifierPort`로 대체됨 — 제거/이관 |

## Out of Scope
- Telegram은 옵션 폴백으로 유지(이관만, 슬라이스1 필수 아님).
- 채널 아카이브/권한 정책, 자가등록 webhook 수신(슬라이스2+).

## Rollback
- bot token 미설정 시 ConsoleNotifier로 폴백(로컬 테스트). 채널 자동생성 실패는 수동 channel_id 입력으로 우회.

## 리뷰 반영 (2026-06-20)
- **channel_id 우선순위(Codex #9):** `sources.yaml`은 channel_id를 비워두고, 봇이 `conversations.create`
  후 sqlite `sources.channel_id`에 저장. **로더는 sqlite 값 우선** 병합(yaml은 slug만 권위). 부팅 시
  sqlite에 있으면 생성 skip, 없으면 생성.
- **`feed-ops`도 관리 채널(Codex #10):** digest/실패알림용 `feed-ops`를 특수 처리하지 말고 동일 채널
  리졸버(없으면 생성→sqlite 저장)로 다룬다. 운영 채널 id도 sqlite에.
- **at-least-once 인정(Codex #1):** 전송→audit→mark_seen 순서라 전송 성공 후 mark_seen 전 크래시 시
  드물게 재전송. 편의 도구이므로 허용하고 명시. "완전 중복 없음"이라 쓰지 않는다(아웃박스 미도입).
- **Slack 호출은 직접 HTTP**(slack-sdk 미도입, v1 방식 계승). `not_in_channel`/`channel_not_found` 재시도 유지.
