"""Configuration — everything secret comes from environment variables.

Nothing is hardcoded. Missing keys = stub mode (emails logged, Stripe
calls skipped) so the MVP runs at $0 with zero credentials.
"""
import os


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


# Stripe (stubbed until set)
STRIPE_API_KEY = _env("STRIPE_API_KEY")
STRIPE_WEBHOOK_SECRET = _env("STRIPE_WEBHOOK_SECRET")

# Resend (stubbed until set)
RESEND_API_KEY = _env("RESEND_API_KEY")
EMAIL_FROM = _env("EMAIL_FROM", "ChurnClinic <noreply@example.com>")

# App
BASE_URL = _env("BASE_URL", "http://localhost:8000").rstrip("/")
DB_PATH = _env("DB_PATH", os.path.join(os.path.dirname(__file__), "churnclinic.db"))
FOUNDER_NAME = _env("FOUNDER_NAME", "Founder")
PRODUCT_NAME = _env("PRODUCT_NAME", "Your SaaS")

# Win-back drip schedule: (email_key, days_after_cancellation)
DRIP_SCHEDULE = [
    ("acknowledge", 0),
    ("help_pause", 3),
    ("discount_call", 10),
]


def stripe_configured() -> bool:
    return bool(STRIPE_API_KEY)


def resend_configured() -> bool:
    return bool(RESEND_API_KEY)
