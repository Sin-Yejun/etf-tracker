# ETF Tracker

미국 주식에 투자하는 국내 액티브 ETF 두 종목이 **매일 무엇을 사고팔았는지** 추적해 텔레그램으로 알려주는 프로젝트입니다.

- TIME 미국나스닥100액티브 (426030)
- KOACT 미국나스닥성장기업액티브 (0015B0)

운용사는 매일 ETF 구성종목(PDF)을 엑셀로 공시합니다. 이 프로젝트는 평일 아침마다 공시를 받아 전날과 비교하고, 종목별 수량이 바뀌었을 때만 알림을 보냅니다. 서버 없이 GitHub Actions만으로 돌아갑니다.

**대시보드:** https://sin-yejun.github.io/etf-tracker/

## 알림 예시

```
TIME 미국나스닥100액티브 보유수량 변동
공시 기준일: 2026-10-13 → 2026-10-14
변경 주식 23개 · 신규 1 · 제외 0

🆕 Everpure Inc  0 → 22 (+22)
🔺 애플  50 → 70 (+20)
🔺 알파벳  69 → 89 (+20)
🔻 로빈후드  96 → 65 (-31)
🔻 SK하이닉스  119 → 99 (-20)
…
```

수량은 1 CU(설정 단위)당 공시 수량입니다. 실제 체결 내역이 아니라 공시된 보유 비중의 변화입니다.

## 동작 방식

```
GitHub Actions (평일 08:30 KST)
  ├─ 운용사 공식 엑셀 다운로드 → 형식·날짜·CU 검증
  ├─ 직전 공시와 종목별 수량 비교
  ├─ 바뀐 종목이 있으면 텔레그램 발송
  └─ 원본·상태를 runtime 브랜치에 커밋 → 대시보드(GitHub Pages) 재배포
```

## 신경 쓴 부분

- **틀린 알림보다 알림 없음이 낫다.** 다운로드 실패나 아직 올라오지 않은 공시를 "변동 없음"으로 처리하지 않습니다. 공시 날짜와 CU 크기가 확인되지 않으면 비교 자체를 보류합니다.
- **정확한 수량 계산.** 엑셀 숫자를 문자열로 읽고 정수 연산으로 차이를 구합니다. 부동소수점 오차나 반올림 임계값이 없습니다.
- **중복 발송 방지.** 발송 전에 "보내는 중" 상태를 먼저 저장(compare-and-swap)하고, 발송 후 완료를 기록합니다. 중간에 실패하면 다음 실행에서 다시 보내지 않고 확인이 필요한 상태로 둡니다.
- **서버·DB 없는 영속 저장.** 상태와 공시 원본을 GitHub Contents API로 별도 `runtime` 브랜치에 저장합니다. `main`에는 코드 이력만 남습니다.
- **입력을 믿지 않는다.** 고정된 공식 URL만 요청하고, 리다이렉트·과대 응답·수식·외부 링크가 든 엑셀은 거부합니다. 의존성은 해시로 고정했습니다.

## 구조

| 경로 | 역할 |
|---|---|
| `etf_collector/` | 공시 다운로드, 엑셀 파싱·검증, 수량 비교 |
| `cu_validation.py` | 운용사 페이지에서 CU 크기 확인 |
| `daily_engine.py` | 상태 관리, 변동 판단, 알림 메시지 생성 |
| `daily_runner.py` | 매일 실행되는 진입점 |
| `github_history.py` | `runtime` 브랜치에 상태 저장 |
| `dashboard.html`, `stocks.json` | 대시보드, 종목 한글명·테마 |
| `.github/workflows/` | 정기 알림, 대시보드 배포, 수동 점검 |

## 로컬 실행

Python 3.12 기준입니다.

```sh
python -m venv .venv && . .venv/bin/activate
python -m pip install --require-hashes --only-binary=:all: -r requirements.lock
python -m unittest discover -s tests
python -m etf_collector.collect --date 2026-10-08 --output output/run1
```

텔레그램 설정과 운영 세부 사항은 [`docs/`](docs/)에 있습니다.
