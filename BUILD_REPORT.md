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

## Monetization lane (2026-10-12, Builder Bravo)

**Task:** Revenue tracker BUILDER QUEUE #2 — pricing page + Stripe checkout for Clinic Pro $12/mo.

**What was found:** The pricing/checkout code had been written in a previous cycle but never committed or deployed — the live Render site served the old MVP build; `/pricing` returned 404.

**What was done this cycle:**
- Committed 9 files (pricing page, `/pricing` + `/pricing/notify` routes, `CLINIC_PRO_PAYMENT_LINK` env config, render.yaml deploy-time env vars, `.env.example`).
- Pushed to `justinwayn/churnclinic` → Render auto-deployed; verified `/pricing` live (HTTP 200).

**How checkout works (zero keys in code, per Stripe key rule):**
- `config.py` reads `CLINIC_PRO_PAYMENT_LINK`; only treated as live if it starts with `https://buy.stripe.com/`.
- Until Justin pastes a real $12/mo Payment Link into the Render env var, the pricing page shows a graceful "notify me" waitlist form (emails saved to DB via `save_pro_interest`).
- render.yaml declares STRIPE_SECRET_KEY / STRIPE_WEBHOOK_SECRET / CLINIC_PRO_PAYMENT_LINK / RESEND_API_KEY / EMAIL_FROM / BASE_URL with `sync: false` — Render prompts for values in the dashboard, nothing real committed.

**Remaining (needs Justin):**
1. Stripe Dashboard → Payment Links → $12/mo recurring link for "Clinic Pro" → paste URL into Render env var `CLINIC_PRO_PAYMENT_LINK`.
2. Free Resend key → Render env var `RESEND_API_KEY` (emails currently log to `email_outbox.log`).
3. Stripe webhook secret → Render env var `STRIPE_WEBHOOK_SECRET` (signature verification currently accepts unsigned in dev mode).

**Spend:** $0.

## Monetization lane — drip delivery reliability (2026-10-19, Builder Alpha)

**Task:** Revenue tracker BUILDER QUEUE #1 (ChurnClinic delivery fix) — residual code defect found while re-checking the fix series from commit 54c30f3.

**Bug (reproduced before fixing):** `email_service.run_drip()` called `db.mark_drip_sent()` unconditionally. With a live Resend key and an unverified sender domain (the exact production state the fleet verification found — every send 403s), the failed send was still recorded as sent: the drip step was permanently burned, never retried, and the customer never got the email. Baseline test: send returned `error: HTTP Error 403: Forbidden`, yet `drip_sends.sent_at` was set and the due count dropped to 0.

**Fix (`email_service.py`):**
- `send()` now returns an explicit `ok` flag (True on live success and in stub mode; False on any live-send exception). Live failures are also appended to `email_outbox.log` so they leave a durable trace.
- `run_drip()` only marks a drip sent when `ok` is True. A failed send stays due, is retried on the next run, and is reported as `delivery: "failed_will_retry"` in the run results.

**Verified (stdlib-only harness, temp DBs):** (a) simulated Resend 403 → `ok=False`, `sent_at` stays NULL, step still due, second run retries; (b) simulated Resend 200 → marked sent once; (c) stub mode unchanged (logged to outbox + marked sent). `py_compile` clean on all four modules. FastAPI itself is not installed on this VM, so the full app import was not re-run here (email_service/db/config are stdlib-only and exercised directly).

**Still ops-side (unchanged, blocks the inbox acceptance test):** verify a sender domain in Resend and set `RESEND_API_KEY` + `RESEND_FROM` in Render; set `STRIPE_API_KEY` + `STRIPE_WEBHOOK_SECRET` in Render. Until then production sends still fail — but failed steps now retry instead of vanishing once the sender is fixed.

**Spend:** $0.
