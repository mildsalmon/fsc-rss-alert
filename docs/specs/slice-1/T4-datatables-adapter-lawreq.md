# T4 — DataTablesAdapter (금융위 법령해석)

의존: T1 · Effort(CC): ~20분 · 상태: TODO

## Context
금융위 법령해석(better.fsc) 목록은 **DataTables 라이브러리 기반 JSON API**다. HTML엔 행이 없고
JS가 `selectReplyCaseLawreqList.do`에 DataTables 파라미터(`draw/start/length` + 사이트 고유
`stNo/muNo/muGpNo`)로 POST해 JSON을 받아 그린다. T4는 그 엔드포인트를 **직접 POST**해서 먹는다.
RSS도 HTML 스크래핑도 아니다(가장 깨끗한 소스). 비조치의견서도 같은 패턴이라 슬라이스2에서 이 어댑터를 재사용한다.

## Verified Source (조사 확정)
- `POST https://better.fsc.go.kr/fsc_new/replyCase/selectReplyCaseLawreqList.do`
  params: `draw, start, length, stNo=11, muNo=85, muGpNo=75`.
- 응답: `{recordsTotal: 2576, data: [{lawreqIdx, title, lawreqNumber, status, dpNm, ...}]}`.
- 상세: `LawreqDetail.do?lawreqIdx=<idx>`. 로그인 버튼은 있으나 **목록 API는 인증 불필요**.
- captcha/Cloudflare 없음.

## Proposed Change
`feed_collector/adapters/datatables.py` — `DataTablesAdapter(SourcePort)`, Template Method:
- **`build_request(cfg)`** (메커니즘 공유): DataTables 파라미터 구성 `{draw:1, start:0, length:N, **cfg.params}`.
- **`map_row(row, cfg)`** (소스별, ~10줄): `Item(item_id=str(row["lawreqIdx"]), title=row["title"],
  link=cfg.detail_url.format(id=row["lawreqIdx"]), published=parse_kst(row.get("regDt")))`.
- `list_path`(응답 JSON에서 리스트 경로, 예 `data`)는 config.
- item_id=lawreqIdx(안정키) → content-hash 폴백 불필요.
- 첫 페이지(length=20~30)면 신규감지 충분. 전체 페이징은 불필요(최신만 본다).

### Example config (sources.yaml, T6에서 사용)
```yaml
- id: lawreq
  slug: fsc-lawreq
  name: 금융위 법령해석
  mechanism: datatables
  parser_version: 1
  channel_id:            # 봇 자동생성 후 채워짐
  interval_minutes: 30
  url: https://better.fsc.go.kr/fsc_new/replyCase/selectReplyCaseLawreqList.do
  params: { stNo: 11, muNo: 85, muGpNo: 75 }
  list_path: data
  detail_url: https://better.fsc.go.kr/fsc_new/replyCase/LawreqDetail.do?lawreqIdx={id}
```

## Acceptance Criteria
1. `DataTablesAdapter.fetch()`가 법령해석 엔드포인트에서 `list[Item]`(≥1) 반환, item_id=lawreqIdx, link=상세URL.
2. `build_request`/`map_row` 분리 — 비조치의견서 config만 바꿔도 재사용 가능(설계 검증).
3. 인증 헤더 없이 데이터 수신 확인.
4. 응답 구조 변경(필드 누락 등) 시 명확한 예외 → T6 FetchFailureReason=`STRUCTURE_CHANGED`로 분류 가능.

## Testing
| Layer | What | Count |
|-------|------|-------|
| Unit | build_request 파라미터, map_row→Item, KST 날짜 파싱 | +3 |
| Integration | 실제 엔드포인트 POST(네트워크, 마킹) | +1 |

## Files Reference
| File | Change |
|------|--------|
| `feed_collector/adapters/datatables.py` | 신규: `DataTablesAdapter` |

## Out of Scope
- 비조치의견서(슬라이스2 — 이 어댑터 + config만 추가). FIU eGov board는 별도 어댑터(슬라이스2).

## Rollback
- 신규 어댑터. 레지스트리에서 lawreq 제거 시 비활성.

## 리뷰 반영 (2026-06-20)
- **정렬 가정 검증(Codex #7):** "첫 페이지면 충분"은 응답이 **최신순**이고 폴링 간격 사이 신규가 length
  이내라는 가정에 의존. 가정이 깨지면 dedup이 조용히 누락. 대응: (a) 엔드포인트가 정렬 파라미터를
  지원하면 최신순 명시, 아니면 (b) 응답 `regDt` 내림차순을 assert + 첫 페이지 length를 평소 신규량의
  수 배(예: 30)로. 폴링 간격 사이 length 초과 버스트가 의심되면 interval을 줄인다.
- **0건 정책(Codex #6):** lawreq는 `empty_result_policy: error`(0건=구조변경 의심). SourceConfig로 받음.
- **`build_request`/`map_row` 분리 유지** — 비조치의견서(슬라이스2)가 config만으로 재사용.
