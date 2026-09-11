# SCMAGLEV 기차예매 프로젝트 분석 요약

## 1) 프로젝트 목적
- 승객용 예매 서비스와 운영 관제 대시보드를 통합한 철도 플랫폼 구현
- 서울권 시범운영 구조에서 전국 확장 가능한 데이터/기능 구조 적용
- 예매/결제/재시도/취소/운영로그/ACK까지 실무형 워크플로우 제공

## 2) 기술 스택
- Backend: Flask, SQLAlchemy, Flask-JWT-Extended, Flask-SocketIO
- Frontend: Jinja2 템플릿 + HTML/CSS/Vanilla JavaScript
- DB: SQLite(개발용), 트랜잭션/락 예외 처리 보강
- Test: Pytest

## 3) 주요 구성
- `백엔드 프로젝트 파일/app.py`
  - 핵심 API 엔드포인트, 인증/인가, 예매/결제 상태 처리, 관제/AI API
- `백엔드 프로젝트 파일/models.py`
  - User, Train, Station, Route, Schedule, Seat, Reservation, TrainLocation, OperationEventLog 모델
- `templates/index.html`
  - 승객 서비스(검색, 좌석 선택, 예매, 결제 재시도, 예약 조회)
- `templates/dashboard.html`
  - 관제 대시보드(실시간 위치, 이벤트 로그 필터, ACK, 상태 그래프)
- `백엔드 프로젝트 파일/tests/test_scmaglev.py`
  - 핵심 시나리오 검증 테스트

## 4) 핵심 기능 분석
- 인증/권한
  - JWT 기반 로그인, 사용자 역할(role) 기반 관제 기능 제어(controller/admin)
- 예매/결제 상태
  - `pending_payment -> paid/booked`, 실패(`failed`), 만료(`expired`) 처리
  - 결제 실패 건 `retry-payment` API로 재시도 가능
- 좌석 무결성
  - 활성 예약 유니크 인덱스로 동일 좌석 중복 예매 방지
- 실시간 운행
  - 가상 위치 업데이트 + 대시보드 시각화 + 상태 통계/추이 그래프
- 운영 로그
  - 이벤트 유형/심각도/ACK 상태 필터링
  - 단건 ACK 및 일괄 ACK 지원
- AI 기능
  - 혼잡도 예측(ML/DL) 비교 및 Ensemble 기반 보조 정보 제공

## 5) 결제 연동 분석(테스트 환경)
- 토스 테스트 결제창 연동 API
  - `POST /api/scmaglev/reservations/<id>/toss/prepare`
  - `POST /api/scmaglev/reservations/<id>/toss/confirm`
- 환경변수 기반 모드 제어
  - `TOSS_PAYMENTS_CLIENT_KEY`, `TOSS_PAYMENTS_SECRET_KEY`, `TOSS_PAYMENTS_MOCK_ONLY`
- 키 검증 로직 보강
  - 예시값/축약값(`...`) 차단, 접두사/길이 조건 검증

## 6) 안정성/예외 처리
- SQLite `database is locked` 상황 대응(commit retry)
- 타임존 문자열 비교 이슈 보정(UTC 기준 정규화)
- 결제 호출 실패 시 사용자 메시지 및 재시도 경로 확보

## 7) 테스트 결과
- 핵심 테스트 시나리오 통과: `15 passed`
- 검증 영역
  - 예약/결제/재시도
  - 이벤트 로그 필터/ACK
  - 전국 데이터 시드 및 조회
  - 대시보드 응답 필드 일관성

## 8) 실행 가이드(요약)
1. `백엔드 프로젝트 파일`에서 의존성 설치: `pip install -r requirements.txt`
2. `.env` 구성(JWT 필수, 결제 테스트키 선택)
3. `run_server.bat` 실행
4. 접속
   - 승객: `http://127.0.0.1:5001/`
   - 관제: `http://127.0.0.1:5001/dashboard`

## 9) 제출/공유 시 주의
- `.env`, `app.db`는 민감정보/로컬 데이터이므로 배포 ZIP에서 제외
- 캐시/가상환경 폴더(`__pycache__`, `.venv`, `.pytest_cache`) 제외 권장
