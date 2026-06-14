# FSC RSS Alert

금융위원회 보도자료 RSS를 주기적으로 확인하고 새 글을 Slack 또는 Telegram으로 보냅니다.
v1은 FSC 피드 하나만 폴링하며, 첫 실행은 현재 피드를 기준선으로 저장하고 알림을 보내지 않습니다.

## 로컬 실행

`uv`가 필요합니다.

```bash
uv sync --locked
```

파싱이 되는지만 확인하려면 dry-run을 실행합니다. 이 모드는 알림을 보내지 않고 `state.json`도 쓰지 않습니다.

```bash
uv run python main.py --dry-run
```

실제 실행은 아래 둘 중 하나의 알림 채널을 환경변수로 설정한 뒤 실행합니다.

```bash
SLACK_WEBHOOK_URL="https://hooks.slack.com/services/..." uv run python main.py
```

```bash
TELEGRAM_BOT_TOKEN="123:abc" TELEGRAM_CHAT_ID="123456789" uv run python main.py
```

## GitHub Secrets

GitHub 저장소의 `Settings` -> `Secrets and variables` -> `Actions`에서 아래 중 하나를 등록합니다.

- Slack: `SLACK_WEBHOOK_URL`
- Telegram: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`

워크플로는 `.github/workflows/poll.yml`에 있으며 20분마다 실행됩니다. 실행할 때마다 FSC RSS를 가져와 `guid`를 기준으로 새 글을 찾고, `guid`가 없으면 `link`를 중복 제거 키로 씁니다. `state.json`에는 최근 ID 약 50개를 저장합니다. 첫 실행은 기준선만 저장하고, 이후 새 항목이 여러 개 있으면 오래된 항목부터 알림을 보냅니다. fetch, 파싱, 알림 전송 중 실패하면 본문 ID 상태를 전진하지 않고, 연속 실패가 임계값에 도달하면 한 번 자가 알림을 보냅니다. `state.json`이 실제로 바뀐 경우에만 GitHub Actions가 커밋합니다.

## 설정값

- `FAILURE_ALERT_THRESHOLD`: 연속 실패 자가 알림 임계값, 기본값 `3`
- `FETCH_TIMEOUT_SECONDS`: RSS 및 알림 HTTP 요청 timeout, 기본값 `20`
- `FETCH_RETRIES`: RSS fetch 재시도 횟수, 기본값 `3`
- `FETCH_RETRY_DELAY_SECONDS`: RSS fetch 재시도 사이 대기 시간, 기본값 `10`
- `SEEN_ID_LIMIT`: 저장할 최근 ID 개수, 기본값 `50`
- `NOTIFY_THROTTLE_SECONDS`: 여러 알림 사이 대기 시간, 기본값 `1`
