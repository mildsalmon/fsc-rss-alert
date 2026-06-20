# Feed Collector

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

## macOS 스케줄 실행

GitHub-hosted Actions에서 FSC 서버 `443` 포트 연결이 timeout될 수 있어, 기본 운영 방식은 로컬 macOS `launchd`입니다.

먼저 `.env` 파일에 알림 채널을 설정합니다.

```bash
printf 'SLACK_WEBHOOK_URL=%s\n' 'https://hooks.slack.com/services/...' > .env
chmod 600 .env
```

스크립트가 단독으로 동작하는지 확인합니다.

```bash
chmod +x scripts/run_poll.sh
./scripts/run_poll.sh
```

첫 실행은 현재 RSS 항목을 기준선으로 `state.json`에 저장하고 알림을 보내지 않습니다.

`launchd`에 등록합니다.

```bash
PLIST="$HOME/Library/LaunchAgents/com.mildsalmon.feed-collector.plist"
REPO_DIR="$(pwd)"

mkdir -p logs
cp launchd/com.mildsalmon.feed-collector.plist "$PLIST"
plutil -remove ProgramArguments "$PLIST" 2>/dev/null || true
plutil -insert ProgramArguments -xml "<array><string>${REPO_DIR}/scripts/run_poll.sh</string></array>" "$PLIST"
plutil -replace WorkingDirectory -string "$REPO_DIR" "$PLIST"
plutil -replace StandardOutPath -string "$REPO_DIR/logs/launchd.out.log" "$PLIST"
plutil -replace StandardErrorPath -string "$REPO_DIR/logs/launchd.err.log" "$PLIST"
launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl enable "gui/$(id -u)/com.mildsalmon.feed-collector"
launchctl kickstart -k "gui/$(id -u)/com.mildsalmon.feed-collector"
```

상태 확인:

```bash
launchctl print "gui/$(id -u)/com.mildsalmon.feed-collector"
tail -f logs/launchd.out.log logs/launchd.err.log
```

해제:

```bash
launchctl bootout "gui/$(id -u)" ~/Library/LaunchAgents/com.mildsalmon.feed-collector.plist
```

`launchd`는 20분마다 `scripts/run_poll.sh`를 실행합니다. 실행할 때마다 금융위 보도자료 RSS를 가져와 `guid`를 기준으로 새 글을 찾고, `guid`가 없으면 `link`를 중복 제거 키로 씁니다. `state.json`에는 최근 ID 약 50개를 저장합니다. 첫 실행은 기준선만 저장하고, 이후 새 항목이 여러 개 있으면 오래된 항목부터 알림을 보냅니다. fetch, 파싱, 알림 전송 중 실패하면 본문 ID 상태를 전진하지 않고, 연속 실패가 임계값에 도달하면 한 번 자가 알림을 보냅니다.

## 설정값

- `FAILURE_ALERT_THRESHOLD`: 연속 실패 자가 알림 임계값, 기본값 `3`
- `FETCH_TIMEOUT_SECONDS`: RSS 및 알림 HTTP 요청 timeout, 기본값 `20`
- `FETCH_RETRIES`: RSS fetch 재시도 횟수, 기본값 `3`
- `FETCH_RETRY_DELAY_SECONDS`: RSS fetch 재시도 사이 대기 시간, 기본값 `10`
- `SEEN_ID_LIMIT`: 저장할 최근 ID 개수, 기본값 `50`
- `NOTIFY_THROTTLE_SECONDS`: 여러 알림 사이 대기 시간, 기본값 `1`
