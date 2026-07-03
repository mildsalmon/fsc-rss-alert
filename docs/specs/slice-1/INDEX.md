# Slice-1 Epic — 다중소스 수집기 수직 슬라이스 (로컬)

> **이 문서는 에이전트/LLM용 인덱스다.** 작업을 시작하기 전에 이 INDEX를 먼저 읽고,
> 의존성 순서대로 task 파일을 하나씩 열어 구현하라. 각 task 문서는 독립 실행 가능한
> 단위로, context·구현상세·인수조건·테스트·파일참조를 담는다.

- **Source of truth (설계):** `docs/DESIGN-v2.md` (Status: APPROVED). 충돌 시 설계 문서 우선.
- **관련 결정 로그:** `gstack-decision-search` 또는 office-hours/plan-ceo-review 세션 기록.
- **패키지:** `fsc_rss_alert` → **`feed_collector`** 로 리네임 (T1에서 수행).

## 이 슬라이스의 목표 (왜)
7개 규제/제재 소스를 한곳에서 감시하는 수집기를, **우선 2개 소스로 끝→끝 로컬 동작**시켜
헥사고날 아키텍처·dedup·Slack 라우팅·digest 파이프라인을 검증한다. 검증되면 슬라이스2~에서
나머지 5소스는 "config + 어댑터 한 벌" 반복으로 추가된다. 컴플라이언스 팀의 **편의 도구**
(규제 강제 SLA 아님, 수동 모니터링이 백스톱) — 따라서 "조용히 죽는 것" 탐지가 핵심 신뢰성 투자.

## 슬라이스1 범위 (로컬, EC2/Terraform 없음)
- 대상 소스 2개: **MOFA 독자제재(RSS)** + **금융위 법령해석(JSON DataTables)**.
- 헥사고날 코어 + sqlite 상태 + Slack bot 채널 라우팅 + CLI poll/digest + daily digest.
- 실행: **로컬**(수동 실행 또는 기존 launchd). AWS 배포는 슬라이스2.

## 슬라이스2~ (미룸 — 여기서 구현하지 말 것)
- Docker + ECR + EC2 + cron pull(SSM 없음).
- **Terraform IaC.** ※ 코멘트: Terraform을 별도 레포로 뺄지 현 레포(`fsc-rss-alert`)에서 할지는
  슬라이스2 착수 시 결정한다. 지금은 결정하지 않는다.
- 나머지 5소스: FSS 보도자료(HTML), FSC 입법예고(HTML), FIU 제재공시(JSON board),
  비조치의견서(DataTables — T4 어댑터 재사용), OFAC Recent Actions(HTML).

## Task 목록
| ID | 제목 | 파일 | 의존 | 상태 | Effort(CC) |
|----|------|------|------|------|-----------|
| T1a | 패키지 리네임 (기계적) | `T1-hexagonal-core.md` | — | DONE | ~15분 |
| T1b | 헥사고날 골격 + Item + SourceConfig + ports | `T1-hexagonal-core.md` | T1a | DONE | ~30분 |
| T2 | SqliteStateRepo + 스키마 + dedup | `T2-sqlite-state.md` | T1b | DONE | ~40분 |
| T3 | RSS 어댑터 + MOFA fetch profile | `T3-rss-adapter-mofa.md` | T1b | DONE | ~25분 |
| T4 | DataTablesAdapter (법령해석) | `T4-datatables-adapter-lawreq.md` | T1b | DONE | ~25분 |
| T5 | SlackBotNotifier + AuditLog | `T5-slack-bot-notifier.md` | T1b, T2 | DONE | ~40분 |
| T6 | 소스 레지스트리 + CLI poll + 실패분류 | `T6-cli-config-poll.md` | T1b–T5 | DONE | ~40분 |
| T7 | daily digest → feed-ops | `T7-daily-digest.md` | T6 | DONE | ~20분 |

> Effort는 거친 추정. 리뷰에서 "비현실적"으로 지적돼 상향 조정함(특히 T2/T5). 실제 시간 = 미지수 검증 비용.

## 의존성 그래프
```
T1a (리네임, 기계적)
 └─> T1b (골격·Item·SourceConfig·ports)
      └─> T2 (sqlite/dedup)
           ├─> T3 (MOFA RSS)      ┐
           ├─> T4 (법령해석)       ├─> T6 (레지스트리+CLI+poll 통합) ─> T7 (digest)
           └─> T5 (Slack+audit)   ┘
```
T3·T4·T5는 T1b의 port 인터페이스를 대상으로 병렬 구현 가능. dedup 테스트는 T2 완료 후.
**리네임 시점:** A-2 결정 = 리네임 먼저(T1a). (Codex는 "E2E 증명 후 마지막 리네임"을 권했으나
사용자가 깨끗한 이름으로 시작을 택함 — cross-model 이견, 현행 유지.)

## 공통 규약 (전 task 공통 — 설계 문서 §Architecture/§Constraints 요약)
- **헥사고날을 얇게:** ports=`typing.Protocol`, adapters=클래스. 서비스레이어·DI프레임워크 금지.
- **코어 순수성:** 도메인 코어(`domain.py`/`core/`)는 httpx·bs4·feedparser·slack-sdk를 import하지 않는다.
- **공통 타입 `Item`**(item_id/title/link/published:datetime|None)으로 모든 어댑터가 정규화.
- **dedup:** 안정키 우선(guid/idx/OFAC recent-action path id) → 없으면 `content://{source}/<sha256(title|body|published)>`.
  `UNIQUE(source_id,item_id)`로 DB 강제.
- **누락 방지:** 첫 실행은 기준선만 저장(알림 0). fetch/파싱/발송 실패 시 seen 전진 금지. oldest-first 발송.
- **실패 가시성:** `FetchFailureReason` enum + 즉시 실패알림 + daily digest.

## 리뷰 반영 (plan-eng-review + Codex outside-voice, 2026-06-20) — 전 task 공통
구현 시 각 task 문서와 함께 아래를 반드시 적용:
- **의존성 확정(지금 결정):** `requests`(쿠키 세션), `python-dateutil`(KST 파싱), `PyYAML`(레지스트리),
  Slack은 `slack-sdk` 대신 **직접 HTTP**(v1 방식 계승, 의존성 최소), `pytest`, `pyright`. `pyproject.toml`에 추가.
- **`SourceConfig` 1급 타입(T1b):** id/slug/name/mechanism/parser_version/channel_id/interval_minutes/
  url/params/list_path/detail_url/empty_result_policy. 어댑터·레지스트리·poll이 전부 이 타입에 의존.
- **소스별 격리(발견3=A):** poll 루프는 소스별 try/except. 한 소스 예외가 나머지를 안 막고, 각자
  last_attempt/last_success 독립 갱신. 죽은 소스는 즉시(T6) + 매일 09시(T7) `feed-ops`로 통지.
- **`last_attempt_at` vs `last_success_at` 분리(Codex #5):** 실패는 success를 전진 안 시킴. due 게이트는
  attempt 기준(망가진 소스가 매 tick 두들기지 않게 backoff). last_run_at 단일 필드 폐기.
- **at-least-once 전송 인정(Codex #1):** Slack전송→audit→mark_seen 순서라 전송 후 크래시 시 드물게 재전송
  가능. 편의 도구이므로 **드문 중복 허용**을 명시(아웃박스 미도입). "완전 중복 없음"이라 적지 말 것.
- **channel_id 우선순위(Codex #9):** yaml은 비워두고, 봇 자동생성 후 sqlite에 저장 → **로더는 sqlite 우선**
  병합. `feed-ops`도 동일 채널 리졸버로 자동생성(특수 처리 금지, Codex #10).
- **`--dry-run` 우회 경로(Codex #11):** notifier·audit·채널생성·seen·실패카운터 쓰기를 전부 우회, 신규 후보만 출력.

## 완료 정의 (슬라이스1 DoD)
1. MOFA·법령해석 새 항목이 각자의 Slack 채널(`feed-mofa-sanctions`, `feed-fsc-lawreq`)에 누락/중복 없이 뜬다.
2. 첫 실행은 기준선만 저장하고 알림을 보내지 않는다.
3. 한 소스가 죽으면(구조변경/네트워크) 즉시 알림 또는 digest의 "마지막 성공 시각"으로 드러난다.
4. `feed-ops`에 KST 09:00 daily digest(소스별 발송수+마지막성공시각+죽은소스)가 온다.
5. 로컬에서 `poll`/`digest` CLI로 끝→끝 동작. 전 task 단위테스트 통과.

## NOT in scope (슬라이스1)
- 나머지 5소스(FSS·FSC입법예고·FIU·비조치·OFAC) — 슬라이스2. HTML/JSON/DataTables config 확장.
- EC2·Docker·ECR·Terraform·cron pull — 슬라이스2.
- 자가등록 webhook 수신, 실시간 `/health` 엔드포인트(B 워커) — 슬라이스2+.
- 아웃박스(정확히-한번 전송) — 편의 도구라 at-least-once 허용으로 대체.
- 공통 HTTP 베이스 추출 — 어댑터 3종째(슬라이스2) 나올 때까지 보류(조기추상화 회피).

## What already exists (재사용)
- v1 `app.py:run()`(첫실행/oldest-first/실패시 비전진) → `core/poll.py`. `feed.py`(fetch/parse 분리) → RssAdapter.
  `notify.py:Notifier` ABC → `NotifierPort`. `state.py`(seen) → SqliteStateRepo. launchd 패턴 → 스케줄.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 1 | CLEAR | HOLD SCOPE, 편의도구 재캘리브레이션, 수직슬라이스 |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR | 3 findings (arch×2, test×1), 전부 반영 |
| Outside Voice | `/codex` | Independent 2nd opinion | 1 | issues_found | 15 findings: 14 반영, 1 cross-model 이견 |
| Design Review | `/plan-design-review` | UI/UX | 0 | — | UI 없음, 해당없음 |
| DX Review | `/plan-devex-review` | DX | 0 | — | 해당없음 |

- **CODEX:** 15건 중 14건을 T2~T7+INDEX에 반영(의존성·SourceConfig·StatePort·last_attempt/success·MOFA 2-hit·정렬·channel_id 우선순위·feed-ops·dry-run·launchd·마이그레이션 제거·at-least-once·empty_result_policy).
- **CROSS-MODEL:** 리뷰(rename 먼저)와 Codex(rename 마지막)가 1건 이견 → 사용자가 현행(먼저) 유지 선택. 정상 종료.
- **VERDICT:** CEO + ENG CLEARED — 구현 착수 가능. 슬라이스1 = T1a→T1b→T2→[T3·T4·T5]→T6→T7.

NO UNRESOLVED DECISIONS
