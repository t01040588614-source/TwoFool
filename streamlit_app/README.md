# SCMAGLEV — Streamlit 데모 버전

원본 Flask(`book/`) 프로젝트는 SocketIO 실시간 통신 + JWT 로그인 + 토스결제 연동을
쓰기 때문에 Streamlit Community Cloud(파이썬 스크립트 하나만 돌리는 구조)에는
그대로 올라가지 않습니다. 이 폴더는 **같은 데이터/검색/요금계산/좌석추천/실시간
위치계산 로직을 `book/app.py`에서 그대로 가져다 쓰면서**, 다음만 단순화한
버전입니다.

| 원본 | 이 버전 |
|---|---|
| SocketIO 실시간 갱신 | 새로고침(수동 또는 5초 자동) 시 재계산 |
| JWT 회원가입/로그인 | 브라우저 세션마다 임시 게스트 계정 자동 생성 |
| 토스페이먼츠 결제 | 예매 즉시 모의결제 완료 처리 |
| 관제 대시보드 전체 기능 | 현황 요약 + 지도 + 지연/장애 목록만 (ACK, 인수인계 등은 제외) |

## share.streamlit.io에 배포하는 방법

1. 이 저장소를 GitHub에 올린 상태여야 합니다(이미 되어 있음).
2. [share.streamlit.io](https://share.streamlit.io) 접속 → GitHub 계정으로 로그인
   (계정 연결은 본인 GitHub 인증이 필요해서 직접 진행해주셔야 합니다).
3. **New app** 클릭 → 아래처럼 입력:
   - Repository: `t01040588614-source/TwoFool`
   - Branch: `main`
   - Main file path: `streamlit_app/streamlit_app.py`
4. **Deploy** 클릭 — 처음 실행 시 전국 노선/열차 데이터를 시드하느라
   10~20초 정도 걸릴 수 있습니다.

## 로컬에서 미리 확인하기

```bash
cd streamlit_app
pip install -r requirements.txt
streamlit run streamlit_app.py
```

## 참고

- 데이터는 시스템 임시 디렉터리(`scmaglev_streamlit_demo.db`)의 **별도의** SQLite
  파일에 저장되며(원본 `book/app.db`와 완전히 분리되어 있어 서로 영향을 주지
  않습니다), Streamlit Cloud는 디스크가 휘발성이라 앱이 재시작되면 예약 내역이
  초기화됩니다 — 데모용으로 의도된 동작입니다. (앱 소스 디렉터리에 SQLite 파일을
  두면 Streamlit Cloud에서 쓰기 권한 오류가 날 수 있어 임시 디렉터리를 씁니다.)
- 실제 서비스처럼 실시간 관제 대시보드, 회원가입/로그인, 결제까지 필요하면
  저장소 루트의 `render.yaml`로 Render에 배포하는 원본 버전을 사용해주세요
  (`RENDER_배포안내.md` 참고).
