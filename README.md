# Feed Collector

설정된 피드 소스를 주기적으로 확인하고 새 글을 Slack 채널로 보냅니다.
현재 slice-1은 MOFA 독자제재 RSS와 금융위 법령해석 DataTables API를 `sources.yaml`에서 읽어 폴링합니다.
첫 실행은 소스별 현재 항목을 기준선으로 저장하고 알림을 보내지 않습니다.

## 로컬 실행

`uv`가 필요합니다.

```bash
uv sync --locked
```

파싱이 되는지만 확인하려면 dry-run을 실행합니다. 이 모드는 알림을 보내지 않고 sqlite 상태 DB도 쓰지 않습니다.

```bash
uv run python -m feed_collector poll --dry-run
```

특정 소스만 확인할 수도 있습니다.

```bash
uv run python -m feed_collector poll --dry-run --source mofa
```

실제 실행은 Slack bot token을 환경변수로 설정한 뒤 실행합니다. 채널 ID가 비어 있는 소스는 봇이 `feed-{slug}` 채널을 만들거나 재사용합니다.

```bash
SLACK_BOT_TOKEN="xoxb-..." uv run python -m feed_collector poll
```

## macOS 스케줄 실행

GitHub-hosted Actions에서 FSC 서버 `443` 포트 연결이 timeout될 수 있어, 기본 운영 방식은 로컬 macOS `launchd`입니다.

먼저 `.env` 파일에 알림 채널을 설정합니다.

```bash
printf 'SLACK_BOT_TOKEN=%s\n' 'xoxb-...' > .env
chmod 600 .env
```

스크립트가 단독으로 동작하는지 확인합니다.

```bash
chmod +x scripts/run_poll.sh
./scripts/run_poll.sh
```

첫 실행은 현재 항목을 기준선으로 `feed.db`에 저장하고 알림을 보내지 않습니다.

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

`launchd`는 20분마다 `scripts/run_poll.sh`를 실행합니다. 실행할 때마다 `sources.yaml`의 due 소스만 가져오고, 소스별 sqlite seen state로 새 글을 찾습니다. 첫 실행은 기준선만 저장하고, 이후 새 항목이 여러 개 있으면 오래된 항목부터 알림을 보냅니다. fetch, 파싱, 알림 전송 중 실패하면 해당 소스의 seen 상태를 전진하지 않고, 실패 유형을 기록합니다. 구조 변경, 차단, 로그인 필요, 404는 즉시 `feed-ops`에 알리고, 일시적 timeout/network 실패는 임계값 도달 시 한 번 알립니다.

## 설정값

- `FAILURE_ALERT_THRESHOLD`: 연속 실패 자가 알림 임계값, 기본값 `3`
- `SLACK_BOT_TOKEN`: Slack Web API bot token
- `SLACK_TIMEOUT_SECONDS`: Slack API timeout, 기본값 `20`
- `FETCH_TIMEOUT_SECONDS`: RSS 및 알림 HTTP 요청 timeout, 기본값 `20`
- `FETCH_RETRIES`: RSS fetch 재시도 횟수, 기본값 `3`
- `FETCH_RETRY_DELAY_SECONDS`: RSS fetch 재시도 사이 대기 시간, 기본값 `10`
