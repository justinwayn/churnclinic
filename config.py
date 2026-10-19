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

# Clinic Pro checkout: Stripe Payment Link (Justin creates it in the Stripe
# Dashboard -> Payment Links for "Clinic Pro" $12/mo and pastes the URL here).
# No secret keys are needed in code for a Payment Link; the secret key is only
# used for the webhook + pause-collection Stripe API calls above.
CLINIC_PRO_PAYMENT_LINK = _env("CLINIC_PRO_PAYMENT_LINK", "")


def clinic_pro_configured() -> bool:
    return CLINIC_PRO_PAYMENT_LINK.startswith("https://buy.stripe.com/")


# Resend (stubbed until set)
RESEND_API_KEY = _env("RESEND_API_KEY")
# Sender for all outbound email. IMPORTANT: Resend only delivers from
# addresses on a domain VERIFIED in Resend. The default below uses
# example.com, which can never be verified, so with the default every
# live send fails with Resend "403 Forbidden". To make email actually
# deliver, set RESEND_FROM (or EMAIL_FROM) to an address on a domain
# you own and have verified in Resend, e.g. "ChurnClinic <hi@yourdomain>".
# RESEND_FROM takes precedence; EMAIL_FROM is kept as the alias the rest
# of the code reads.
EMAIL_FROM = (
    _env("RESEND_FROM")
    or _env("EMAIL_FROM", "ChurnClinic <noreply@example.com>")
)
RESEND_FROM = EMAIL_FROM

# App
# BASE_URL builds the survey/pause links inside emails. The default is
# the production deploy so links are correct even if the env var is
# forgotten; set BASE_URL=http://localhost:8000 for local dev.
BASE_URL = _env("BASE_URL", "https://churnclinic.onrender.com").rstrip("/")
DB_PATH = _env("DB_PATH", os.path.join(os.path.dirname(__file__), "churnclinic.db"))
FOUNDER_NAME = _env("FOUNDER_NAME", "the ChurnClinic team")
PRODUCT_NAME = _env("PRODUCT_NAME", "ChurnClinic")

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
