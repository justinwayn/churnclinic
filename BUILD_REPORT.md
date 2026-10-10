# ChurnClinic MVP — Build Report

**Blueprint:** `prod_1791573679` | **Built:** 2026-10-09 | **Status:** Working MVP, tested end-to-end

## What was built

A working ChurnClinic MVP at `~/workspace/agent-army/builds/prod_1791573679/app/`:

| Component | Status | Notes |
|---|---|---|
| Stripe webhook listener | ✅ | `POST /webhooks/stripe` — `subscription.deleted` → cancellation record + survey invite; `subscription.created` → marks prior cancellation as **saved** |
| Hosted exit-survey page | ✅ | `GET/POST /s/{token}` — 6-reason taxonomy (too expensive, missing feature, not using it, switched tool, technical issue, other) + free text |
| 3-email win-back drip | ✅ | Day 0 acknowledge → Day 3 help/pause offer → Day 10 discount/founder call. Templates editable from dashboard |
| Pause-instead-of-cancel | ✅ | `GET/POST /pause/{token}` — 1–3 month pause, marks record `paused` (excluded from further drip) |
| Dashboard | ✅ | `GET /dashboard` — cancellations, save rate, recovered/lost MRR, survey response rate, top-reason bar chart, recent table, template editor |
| Landing page | ✅ | `GET /` — copy from the product brief (honest math, pricing) |
| Weekly digest | ✅ | `POST /admin/digest/run` — cancellations, saves, top reason, MRR |
| Drip runner | ✅ | `POST /admin/drip/run` — hit via cron; sends only due emails to still-canceled customers |

**Stack:** FastAPI + SQLite + Jinja2 + vanilla JS. $0 — no paid APIs, no cloud services.

## How to run

```bash
cd ~/workspace/agent-army/builds/prod_1791573679/app
./venv/bin/uvicorn main:app --port 8000
# open http://localhost:8000  (landing)  /dashboard  (dashboard)
```

Optional env vars (all stubbed when unset):
- `STRIPE_API_KEY`, `STRIPE_WEBHOOK_SECRET` — real Stripe calls + signature verification
- `RESEND_API_KEY` — real email sending via Resend
- `EMAIL_FROM`, `BASE_URL`, `FOUNDER_NAME`, `PRODUCT_NAME`, `DB_PATH`

Cron (example):
```
*/15 * * * * curl -s -X POST http://localhost:8000/admin/drip/run
0 9 * * 1 curl -s -X POST http://localhost:8000/admin/digest/run
```

Dev helper: `POST /api/test/cancel` with `{"email","name","plan","mrr_cents"}` simulates a cancellation and returns survey/pause URLs.

## What's stubbed

- **Resend** — without `RESEND_API_KEY`, emails are logged to `email_outbox.log` (JSON lines), not sent
- **Stripe API** — without `STRIPE_API_KEY`, the pause-collection call is a no-op; webhook signature verification accepts unsigned payloads (dev mode)
- **Stripe OAuth connect flow** — landing page CTA is informational; connect is via env-var keys for the MVP
- **Auth** — dashboard and admin endpoints have no login yet (single-founder deploy assumption)

## Test results (all passing)

1. Cancellation via test endpoint → record created, survey invite logged ✅
2. Survey page loads (200), submit records reason ✅
3. Drip run → day-0 `acknowledge` email sent ✅
4. Simulated day 4 → `help_pause` sent; day 11 → `discount_call` sent; day 12 → nothing (no dupes) ✅
5. Pause submit → status `paused`, excluded from further drip ✅
6. `subscription.created` webhook → prior cancellation marked `saved`, recovered MRR tracked ✅
7. Template PUT → custom copy persisted and used ✅
8. Stats: save rate, survey rate, reason breakdown all correct ✅
9. Invalid survey token → 404 ✅
10. Digest → correct summary logged ✅

## Blockers / next steps for production

1. **Resend API key** — needed to actually send emails (free tier, 3,000/mo)
2. **Stripe webhook secret** — needed for signature verification in production
3. **Dashboard auth** — add a simple token/password before exposing publicly
4. **Hosting** — Render/Fly.io free tier + cron for `/admin/drip/run`
5. **Dogfood** — install on an Army product's Stripe funnel, post teardown (per brief checklist)
