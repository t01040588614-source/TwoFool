# Flask 게시판 백엔드

## 실행

Windows에서는 `run_server.bat`를 더블 클릭하세요. 필요한 패키지를 설치하고 서버를 시작한 뒤, 브라우저에서 상태 확인 페이지를 자동으로 엽니다.

서버 주소: http://127.0.0.1:5000  
상태 확인: http://127.0.0.1:5000/api/health

기본값으로 프로젝트 폴더에 `app.db` SQLite 데이터베이스와 테이블을 자동 생성합니다.

## 환경 변수

배포하거나 별도 DB를 사용하려면 `.env.example`을 복사해 `.env` 파일을 만들고 값을 설정하세요. `.env` 파일은 비밀값이므로 저장소에 올리지 마세요.

---

# SCMAGLEV 실행 가이드

## 1) 압축 해제 후 폴더 이동
- 압축을 해제한 뒤 `백엔드 프로젝트 파일` 폴더로 이동합니다.

## 2) 필수 환경변수 파일(.env) 생성
- 같은 폴더에 `.env` 파일을 만들고 아래 항목을 입력합니다.

```env
JWT_SECRET_KEY=change-this-to-a-long-random-secret

# 선택: 토스 테스트 결제창 사용 시
TOSS_PAYMENTS_CLIENT_KEY=test_ck_your_real_value
TOSS_PAYMENTS_SECRET_KEY=test_sk_your_real_value
TOSS_PAYMENTS_MOCK_ONLY=0
```

## 3) 의존성 설치(최초 1회)
- 터미널에서 아래 명령을 실행합니다.

```bash
pip install -r requirements.txt
```

## 4) 서버 실행
- `run_server.bat` 파일을 실행합니다.

## 5) 접속 주소
- 승객 페이지: `http://127.0.0.1:5001/`
- 관제 대시보드: `http://127.0.0.1:5001/dashboard`

## 6) 문제 해결
- 페이지가 안 뜨면 서버 콘솔에 에러가 있는지 확인합니다.
- 결제창이 안 뜨면 `.env`의 토스 키가 실제 테스트 키인지 확인합니다.
- 실행 중 포트 충돌이 있으면 기존 5001 포트 사용 프로세스를 종료 후 재실행합니다.
