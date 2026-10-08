# Telegram 연결 준비

현재 상태: 수집/비교 dry-run 준비 단계. Telegram 연결·발송·자동 일정 없음.
발송 대상은 사용자가 지정한 @daniel45_etf_bot과의 개인 대화입니다.
봇 이름은 수신 chat ID가 아니며, 실제 숫자 chat ID 확인 전에는 발송하지 않습니다.

## 안전한 secret 입력
저장소 대상 확정 후, 사용자가 GitHub 웹사이트에서 직접:
1. 저장소 → Settings → Secrets and variables → Actions → Secrets
2. New repository secret
3. TELEGRAM_BOT_TOKEN: BotFather에서 받은 토큰을 Secret에 직접 입력
4. Add secret
5. 검증된 개인 chat ID 확보 후 TELEGRAM_CHAT_ID도 같은 방법으로 추가

토큰을 채팅, 이슈, 커밋, workflow 입력값, 로그, 스크린샷, 파일로 보내지 마세요.
이 준비용 workflow는 Telegram secrets를 참조하지 않습니다. Secret을 추가해도
알림이 켜지지 않습니다. 아직 사용자에게 입력을 요청하거나 설정하지 않았습니다.

## 수신자 확인과 승인 순서
- 먼저 연결/지속 사용 대상 저장소를 확정하고 승인을 받습니다
- 별도 승인 후 bot identity getMe, webhook 존재 여부 getWebhookInfo 확인
- 봇 DM에 보낸 메시지에서 chat.id를 확인하는 getUpdates는 아직 실행 금지
  기존 webhook가 있으면 제거하지 않고 중단합니다. 첫 arbitrary update 채택 금지
  사용자에게 보낸 일회용 검증문구를 정확히 맞추고 private chat인지 검증합니다
- 확인된 개인대화로 테스트 1회 전송은 별도 승인 후 실시합니다
- 이후 알림 내용/시간/빈도, 초기 과거자료 처리 정책을 확정하고 활성화합니다

## 향후 운영 안전장치
- 최초 연결은 최신 성공 수집본으로 기준선만 저장. 과거 알림 일괄 발송 금지
- 주식 신규/편출/정확 수량 변화를 비교. 현금/선물 별도, CU 다르면 중단
- 원본 수집 실패/미공시/휴일/비교자료 부족은 '변화 없음'으로 처리하지 않음
- GitHub runner에서 KOACT 공식 다운로드가 되는지는 아직 검증하지 않았음
- 같은 수량 상태 재업로드나 가격/비중만 변한 경우 중복 발송하지 않음
- durable outbox 필요: 전송 전 sending 상태를 영구 저장한 뒤에만 sendMessage
- GitHub artifact/cache 만으로 중복방지 원장을 대체하지 않음(만료/삭제 가능)
- 향후 별도 state branch/파일에 좁은 contents:write 권한으로 CAS 저장하는 설계
  전송 job에만 권한 부여하고, concurrency + cancel-in-progress:false 적용
  저장 실패면 발송 금지, 보내던 중 중단되면 uncertain 유지하고 자동 재전송 금지
- 429는 retry_after 대기. Telegram 성공 응답의 message_id만 접수완료로 기록
- 하루 알림 횟수, KST 시간과 재시도 범위는 아직 미정. schedule 이벤트 없음
- Actions는 정각 보장을 하지 않으며 사용량/요금 정책은 실행 전 확인 필요

## 공식 문서
- Secrets: https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets
- 수동 실행: https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow
- Telegram: https://core.telegram.org/bots/api#sendmessage
