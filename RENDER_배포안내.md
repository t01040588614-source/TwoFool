# SCMAGLEV Render 배포 — 가구 사이트(Mood Code) 와 동일 방식

가구 사이트 [mood-code-latest.onrender.com](https://mood-code-latest.onrender.com/) 처럼  
**Docker Hub → Render Existing Image** 로 올립니다.

---

## 한 번에 배포 (가장 쉬움)

1. **Docker Desktop** 실행 (Engine running)
2. `book\실행_Render배포.bat` 더블클릭
3. Docker Hub 로그인 (gygs1090) — 가구 사이트 때 썼던 계정
4. Render API Key 입력 (`rnd_...`) — 가구 사이트 때 썼던 키 재사용 가능
5. 2~5분 후 URL 확인:
   - 승객: `https://scmaglev-latest.onrender.com/`
   - 관제: `https://scmaglev-latest.onrender.com/dashboard`

---

## 단계별 (수동)

### 1) Docker 빌드 + Hub push
```powershell
cd "book"
.\scripts\deploy-docker.ps1 -Push
```
→ `docker.io/gygs1090/scmaglev:latest`

### 2) Render 배포
```powershell
.\scripts\deploy-render.ps1
```
또는 [Render Dashboard](https://dashboard.render.com/) → **New Web Service** → **Existing Image**
- Image: `docker.io/gygs1090/scmaglev:latest`
- Region: Singapore
- Health Check: `/api/health`

### 3) Render 환경 변수
| 변수 | 값 |
|------|-----|
| `JWT_SECRET_KEY` | Generate Secret |
| `SCMAGLEV_MAX_TRACKED_TRAINS` | `200` |
| `TOSS_PAYMENTS_MOCK_ONLY` | `1` |
| `OPENAI_API_KEY` | (선택) |

---

## GitHub Blueprint (대안)

1. [Render](https://render.com) → New → Blueprint
2. [TwoFool](https://github.com/t01040588614-source/TwoFool) 저장소 선택
3. `render.yaml` 적용

---

## 관제 로그인
- `gygs1010` / `zxc123123`

## 참고
- 무료 플랜: 15분 미사용 시 sleep → 첫 접속 30초~3분
- `.env`는 GitHub/이미지에 없음 → Render 환경변수로 설정
