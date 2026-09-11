# SCMAGLEV Render 배포 안내

[metro-inout.onrender.com](https://metro-inout.onrender.com/) 과 같은 **Render 무료 Web Service** 로 올리는 방법입니다.

> metro-inout 은 **Streamlit** 앱이고, SCMAGLEV 은 **Flask + Socket.IO** 앱이라 시작 명령이 다릅니다.  
> Render 사용 방식( GitHub 연결 → 자동 배포 → `*.onrender.com` URL )은 동일합니다.

---

## 1. 사전 준비

1. GitHub 저장소: [TwoFool](https://github.com/t01040588614-source/TwoFool)
2. [Render](https://render.com) 가입 (GitHub 계정 연동)
3. `.env` 는 GitHub에 없음 → Render 대시보드에서 환경변수로 직접 입력

---

## 2. Blueprint로 배포 (권장)

1. Render 대시보드 → **New +** → **Blueprint**
2. GitHub `TwoFool` 저장소 선택
3. `render.yaml` 자동 인식 → **Apply**
4. 배포 완료 후 URL 예시:
   - `https://scmaglev.onrender.com/`
   - `https://scmaglev.onrender.com/dashboard`

---

## 3. 수동 Web Service 생성 (Blueprint 안 될 때)

| 항목 | 값 |
|------|-----|
| Root Directory | `백엔드 프로젝트 파일` |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `gunicorn --worker-class eventlet -w 1 --bind 0.0.0.0:$PORT --timeout 180 app:app` |
| Health Check | `/api/health` |

---

## 4. Render 환경변수 (Environment)

| 변수 | 필수 | 설명 |
|------|------|------|
| `JWT_SECRET_KEY` | ✅ | Render에서 Generate Secret 사용 |
| `OPENAI_API_KEY` | 선택 | GPT AI 사용 시 |
| `OPENAI_MODEL` | 선택 | 기본 `gpt-4o-mini` |
| `TOSS_PAYMENTS_CLIENT_KEY` | 선택 | 토스 결제창 |
| `TOSS_PAYMENTS_SECRET_KEY` | 선택 | 토스 결제창 |
| `TOSS_PAYMENTS_MOCK_ONLY` | 선택 | `1`=모의결제 (키 없을 때) |
| `SCMAGLEV_MAX_TRACKED_TRAINS` | 선택 | 무료 플랜은 `200` 권장 (부팅 빠름) |

---

## 5. 접속 URL

| 페이지 | 경로 |
|--------|------|
| 승객 메인 (AI 검색) | `/` |
| 관제 대시보드 | `/dashboard` |
| 헬스체크 | `/api/health` |

관제 로그인: `gygs1010` / `zxc123123`

---

## 6. metro-inout 과 같은 점 / 다른 점

| | metro-inout | SCMAGLEV |
|---|-------------|----------|
| 프레임워크 | Streamlit | Flask + Socket.IO |
| URL | `*.onrender.com` | `*.onrender.com` |
| 무료 플랜 | 15분 미사용 시 sleep | 동일 |
| 첫 접속 | cold start 느림 | DB 시드로 더 느릴 수 있음 (1~3분) |

---

## 7. 자주 나는 문제

| 증상 | 해결 |
|------|------|
| Deploy failed · JWT | `JWT_SECRET_KEY` 환경변수 추가 |
| 502 / 시작 타임아웃 | `SCMAGLEV_MAX_TRACKED_TRAINS=100` 으로 줄이기 |
| AI 안 됨 | `OPENAI_API_KEY` Render에 추가 후 재배포 |
| DB 초기화됨 | 무료 SQLite는 재배포 시 리셋 → 데모용 OK, 운영은 PostgreSQL |

---

## 8. 배포 후 팀원 공유

```
승객: https://<your-app>.onrender.com/
관제: https://<your-app>.onrender.com/dashboard
```

ZIP 대신 이 URL만 공유하면 됩니다.
