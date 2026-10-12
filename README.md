# ChurnClinic — MVP (prod_1791573679)

**Your cancellations are trying to tell you something. Listen automatically.**

Exit surveys + win-back emails for one-person SaaS: catch every Stripe cancellation,
ask why, and run the win-back emails you never have time to send.

## Run it ($0)

```bash
cd ~/workspace/agent-army/builds/prod_1791573679/app
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
./venv/bin/uvicorn main:app --port 8000
```

Then open:
- **Landing:** http://localhost:8000/
- **Dashboard:** http://localhost:8000/dashboard

SQLite database is created automatically (`churnclinic.db`, override with `DB_PATH`).

## What's implemented

| Component | Route | Notes |
|---|---|---|
| Stripe webhook listener | `POST /webhooks/stripe` | `subscription.deleted` → cancellation record + survey invite; `subscription.created` → marks prior cancellation **saved** |
| Hosted exit-survey page | `GET/POST /s/{token}` | 6-reason taxonomy (too expensive, missing feature, not using it, switched tool, technical issue, other) + free text |
| 3-email win-back drip | cron → `POST /admin/drip/run` | Day 0 acknowledge → Day 3 help/pause offer → Day 10 discount/founder call; templates editable from dashboard |
| Pause-instead-of-cancel | `GET/POST /pause/{token}` | 1–3 month pause, record marked `paused`, excluded from further drip |
| Churn dashboard | `GET /dashboard` | cancellations, save rate, recovered/lost MRR, survey response rate, top-reason chart, recent table, template editor |
| Landing page | `GET /` | marketing copy from the product brief |
| Weekly digest | cron → `POST /admin/digest/run` | cancellations, saves, top reason, trend line |

## Env vars (all optional — everything is stubbed when unset)

```bash
STRIPE_API_KEY=...        # real Stripe API calls (pause-collection, etc.)
STRIPE_WEBHOOK_SECRET=... # webhook signature verification (dev mode accepts unsigned)
RESEND_API_KEY=...        # real email sending; without it, emails log to email_outbox.log
EMAIL_FROM="ChurnClinic <noreply@example.com>"
BASE_URL=http://localhost:8000
FOUNDER_NAME="Your Name"
PRODUCT_NAME="Your SaaS"
DB_PATH=./churnclinic.db
```

No secrets are hardcoded — everything sensitive comes from the environment.

## Cron (production)

```cron
*/15 * * * * curl -s -X POST https://<your-host>/admin/drip/run
0 9 * * 1    curl -s -X POST https://<your-host>/admin/digest/run
```

## Dev helper

`POST /api/test/cancel` with `{"email","name","plan","mrr_cents"}` simulates a
cancellation and returns the survey + pause URLs — no Stripe account needed.

## Known MVP limitations

- Dashboard and admin endpoints have **no login yet** (single-founder deploy assumption) — add a simple token/password before exposing publicly.
- Stripe OAuth connect flow is informational in the MVP; production connect is via env-var keys.
- Webhook signature verification accepts unsigned payloads until `STRIPE_WEBHOOK_SECRET` is set.

## Deploy

`render.yaml` is included — Render free tier: build `pip install -r requirements.txt`,
start `uvicorn main:app --host 0.0.0.0 --port $PORT`. A Render cron job (or any cron)
hits `/admin/drip/run` every 15 min and `/admin/digest/run` Mondays.
