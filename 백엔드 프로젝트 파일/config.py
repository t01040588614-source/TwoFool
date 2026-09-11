import os

from dotenv import load_dotenv

BASE_DIR = os.path.abspath(
    os.path.dirname(__file__)
)

load_dotenv(os.path.join(BASE_DIR, ".env"))


class Config:

    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL") or (
        f"sqlite:///{os.path.join(BASE_DIR, 'app.db')}"
    )

    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "connect_args": {
            "timeout": 60,
            "check_same_thread": False,
        },
    }

    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")

    SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USERNAME = os.getenv("SMTP_USERNAME")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
    SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL")
    TOSS_PAYMENTS_CLIENT_KEY = os.getenv("TOSS_PAYMENTS_CLIENT_KEY")
    TOSS_PAYMENTS_SECRET_KEY = os.getenv("TOSS_PAYMENTS_SECRET_KEY")
    TOSS_PAYMENTS_MOCK_ONLY = os.getenv("TOSS_PAYMENTS_MOCK_ONLY", "auto")

    # OpenAI AI 추천 — .env에 OPENAI_API_KEY만 넣으면 자동 활성화
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    # auto: 키 있으면 OpenAI, 없으면 규칙 기반 / 1: 강제 OpenAI / 0: 강제 규칙 기반
    OPENAI_ENABLED = os.getenv("OPENAI_ENABLED", "auto")


if not Config.JWT_SECRET_KEY:
    raise RuntimeError("JWT_SECRET_KEY 환경변수를 설정해주세요.")
