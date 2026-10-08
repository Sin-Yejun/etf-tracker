# 개인 수신자 확인 (사용자 수동 실행)

이 단계는 저장된 TELEGRAM_BOT_TOKEN으로 봇과 본인 대화만 확인합니다.
알림 전송, 자동 스케줄, webhook 변경, chat ID 자동 저장은 하지 않습니다.

1. 이번 확인용으로 안내받은 ETF-CHECK-로 시작하는 문구를 @daniel45_etf_bot 개인대화에 그대로 보냅니다
2. 15분 안에 저장소 Actions → Verify Telegram private recipient → Run workflow를 엽니다
3. main을 선택하고 pairing_phrase에 같은 확인문구만 입력합니다. 토큰을 입력하지 마세요
4. 직접 Run workflow를 누릅니다. GitHub runner가 기존 repository secret을 이 실행에만 사용합니다
5. 성공한 실행의 Summary에서 Verified personal TELEGRAM_CHAT_ID 값을 확인합니다
6. 본인이 GitHub Settings → Secrets and variables → Actions → New repository secret에 이름 TELEGRAM_CHAT_ID로 저장합니다

실행 Summary에는 매칭된 본인 숫자 chat ID만 표시합니다. 이름/사용자명/다른 사람의 메시지/원본 업데이트는 출력하지 않습니다. 이 비공개 저장소의 Actions 열람 권한이 있는 사람은 Summary를 볼 수 있습니다. 향후 저장소 공유 시 이 점을 고려하세요.

## 검증과 제한
- getMe로 예상 봇 @daniel45_etf_bot 일치를 확인합니다
- getWebhookInfo에 기존 webhook URL이 있으면 변경하지 않고 중단합니다
- getUpdates는 한 번만, offset과 allowed_updates를 지정하지 않고 timeout=0으로 조회합니다
- 업데이트 확인 소비를 하는 offset 진전·음수 offset·두 번째 확인 호출은 없습니다
- 기존 webhook/poller를 바꾸거나 중단하지 않습니다. 다른 poller가 있다면 실행하지 마세요
- 첫 100개 pending updates에서만 조회합니다. 오래된 backlog 때문에 문구가 안 보이면 중단하며 skip/consume하지 않습니다
- 정확히 같은 확인문구, 15분 이내 직접 보낸 메시지, private chat, sender/chat ID 일치, 비봇 발신자만 허용합니다
- 서로 다른 개인대화가 같은 문구로 매칭되면 중단합니다. 전달 메시지는 허용하지 않습니다
- 토큰은 HTTPS 요청 경로에 필요한 Telegram 인증에만 사용합니다. 요청 URL/응답/예외/traceback는 출력하지 않습니다
- 실패는 일반화된 오류만 출력하고 artifact는 만들지 않습니다
- 이 코드는 아직 실제 봇으로 실행되지 않았습니다. 로컬 테스트는 가짜 응답만 사용합니다

이 연결확인은 알림 발송 승인이 아닙니다. 다음 단계인 테스트 1회 전송과 일별 시간/빈도는 별도 결정합니다.

공식 문서: https://core.telegram.org/bots/api#getupdates
