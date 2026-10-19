"""ChurnClinic MVP — FastAPI app.

Routes:
  GET  /                  landing page
  POST /webhooks/stripe   Stripe webhook (subscription.deleted -> cancellation)
  GET  /s/{token}         exit survey page
  POST /s/{token}         submit survey
  GET  /pause/{token}     pause offer page
  POST /pause/{token}     accept pause
  GET  /dashboard         founder dashboard
  GET  /api/stats         dashboard JSON
  GET  /api/templates     list email templates
  PUT  /api/templates/{key}  update a template
  POST /admin/drip/run    send due win-back emails (cron hits this)
  POST /admin/digest/run  send weekly digest (cron hits this)
  POST /api/test/cancel   dev helper: simulate a cancellation
"""
import hashlib
import hmac
import time

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

import config
import db
import email_service

app = FastAPI(title="ChurnClinic")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

db.init_db()
db.seed_templates()


# ---------- pages ----------

@app.get("/", response_class=HTMLResponse)
def landing(request: Request):
    return templates.TemplateResponse(request, "landing.html", {"request": request})


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse(request, "dashboard.html", {"request": request})


@app.get("/pricing", response_class=HTMLResponse)
def pricing(request: Request):
    return templates.TemplateResponse(request, "pricing.html", {
        "request": request,
        "pro_configured": config.clinic_pro_configured(),
        "payment_link": config.CLINIC_PRO_PAYMENT_LINK,
    })


@app.post("/pricing/notify", response_class=HTMLResponse)
def pricing_notify(request: Request, email: str = Form("")):
    ok = db.save_pro_interest(email)
    return templates.TemplateResponse(request, "pricing.html", {
        "request": request,
        "pro_configured": config.clinic_pro_configured(),
        "payment_link": config.CLINIC_PRO_PAYMENT_LINK,
        "notify_ok": ok,
        "notify_email": email if ok else "",
    })


@app.get("/s/{token}", response_class=HTMLResponse)
def survey_page(request: Request, token: str):
    cancel = db.get_by_token(token)
    if not cancel:
        return HTMLResponse("Invalid or expired link.", status_code=404)
    return templates.TemplateResponse(request, "survey.html", {
        "request": request, "token": token,
        "name": cancel.get("customer_name") or "there",
        "product": config.PRODUCT_NAME, "reasons": db.REASONS,
    })


@app.post("/s/{token}", response_class=HTMLResponse)
def survey_submit(request: Request, token: str,
                  reason: str = Form(...), detail: str = Form("")):
    ok = db.record_survey(token, reason, detail[:2000])
    if not ok:
        return HTMLResponse("Invalid or expired link.", status_code=404)
    return templates.TemplateResponse(request, "survey_done.html", {
        "request": request, "token": token, "product": config.PRODUCT_NAME,
    })


@app.get("/pause/{token}", response_class=HTMLResponse)
def pause_page(request: Request, token: str):
    cancel = db.get_by_token(token)
    if not cancel or cancel["status"] != "canceled":
        return HTMLResponse("Invalid or expired link.", status_code=404)
    return templates.TemplateResponse(request, "pause.html", {
        "request": request, "token": token, "product": config.PRODUCT_NAME,
    })


@app.post("/pause/{token}", response_class=HTMLResponse)
def pause_submit(request: Request, token: str, months: int = Form(...)):
    months = max(1, min(3, months))
    cancel = db.get_by_token(token)
    if not cancel or not db.mark_paused(token, months):
        return HTMLResponse("Invalid or expired link.", status_code=404)
    # Real Stripe pause attempt when the operator has connected Stripe.
    # The outcome is recorded, and the confirmation page only claims a
    # billing pause when Stripe itself confirms one.
    stripe_paused = False
    if config.stripe_configured():
        result = _stripe_pause(cancel["stripe_subscription_id"], months)
        stripe_paused = bool(result["ok"])
        db.mark_pause_result(
            token,
            "stripe_paused" if stripe_paused
            else f"recorded_only: {result['detail']}",
        )
    else:
        db.mark_pause_result(token, "recorded_only: Stripe not connected")
    return templates.TemplateResponse(request, "pause_done.html", {
        "request": request, "product": config.PRODUCT_NAME,
        "stripe_paused": stripe_paused, "months": months,
    })


def _resumes_at(months: int) -> int:
    """Unix timestamp `months` calendar months from now (UTC)."""
    import calendar as _calendar

    t = time.gmtime()
    month_index = t.tm_mon - 1 + months
    year = t.tm_year + month_index // 12
    month = month_index % 12 + 1
    day = min(t.tm_mday, _calendar.monthrange(year, month)[1])
    return _calendar.timegm(
        (year, month, day, t.tm_hour, t.tm_min, t.tm_sec, 0, 0, 0))


def _stripe_pause(subscription_id: str, months: int) -> dict:
    """Pause collection on a Stripe subscription — a real API call.

    Uses the operator's STRIPE_API_KEY (the Stripe credentials model this
    single-tenant deploy has). Returns {"ok": bool, "detail": str}.

    Honest catch: ChurnClinic learns about a cancellation from Stripe's
    customer.subscription.deleted webhook — usually AFTER the
    subscription is already gone. A deleted subscription cannot be
    paused; Stripe answers with an error and there is nothing left to
    bill anyway. So in the standard flow this returns ok=False and the
    pause stands as a ChurnClinic record only. If a still-active
    subscription is ever passed here, the pause is genuinely applied.
    """
    import json as _json
    import urllib.error
    import urllib.parse
    import urllib.request

    if not subscription_id:
        return {"ok": False, "detail": "no subscription id on record"}
    data = urllib.parse.urlencode({
        "pause_collection[behavior]": "void",
        "pause_collection[resumes_at]": _resumes_at(months),
    }).encode()
    req = urllib.request.Request(
        f"https://api.stripe.com/v1/subscriptions/{subscription_id}",
        data=data, method="POST",
        headers={"Authorization": f"Bearer {config.STRIPE_API_KEY}",
                 "Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            body = _json.loads(r.read().decode())
        if body.get("pause_collection"):
            return {"ok": True, "detail": "pause_collection set in Stripe"}
        return {"ok": False,
                "detail": "Stripe response had no pause_collection"}
    except urllib.error.HTTPError as e:
        try:
            err = _json.loads(e.read().decode())
            msg = (err.get("error") or {}).get("message") or str(e)
        except Exception:  # noqa: BLE001
            msg = str(e)
        return {"ok": False, "detail": f"Stripe: {msg}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "detail": str(e)}


# ---------- API ----------

@app.get("/api/stats")
def api_stats():
    return db.stats()


@app.get("/api/templates")
def api_templates():
    return db.all_templates()


class TemplateUpdate(BaseModel):
    subject: str
    body: str


@app.put("/api/templates/{key}")
def api_template_update(key: str, t: TemplateUpdate):
    if key not in db.DEFAULT_TEMPLATES:
        return JSONResponse({"error": "unknown template"}, status_code=404)
    db.set_template(key, t.subject, t.body[:5000])
    return {"ok": True}


# ---------- Stripe webhook ----------

def _verify_sig(payload: bytes, sig: str) -> bool:
    if not config.STRIPE_WEBHOOK_SECRET:
        return True  # dev mode: accept unsigned (logged)
    try:
        ts, _, sigpart = sig.partition(",")
        timestamp = ts.split("=")[1]
        signature = sigpart.split("=")[1]
        signed = f"{timestamp}.".encode() + payload
        expected = hmac.new(config.STRIPE_WEBHOOK_SECRET.encode(),
                            signed, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)
    except Exception:  # noqa: BLE001
        return False


@app.post("/webhooks/stripe")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    if not _verify_sig(payload, sig):
        return JSONResponse({"error": "bad signature"}, status_code=400)
    try:
        event = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"error": "bad json"}, status_code=400)

    etype = event.get("type", "")
    obj = (event.get("data") or {}).get("object") or {}

    if etype == "customer.subscription.deleted":
        customer = obj.get("customer", "")
        email = (obj.get("customer_email") or
                 ((obj.get("customer_details") or {}).get("email")) or "")
        items = ((obj.get("items") or {}).get("data") or [{}])
        price = (items[0].get("price") or {})
        mrr_cents = price.get("unit_amount") or 0
        cancel = db.create_cancellation({
            "stripe_subscription_id": obj.get("id"),
            "stripe_customer_id": customer if isinstance(customer, str) else "",
            "customer_email": email,
            "customer_name": obj.get("customer_name") or "",
            "plan_name": (price.get("nickname")
                          or (price.get("product") or "")),
            "mrr_cents": mrr_cents,
            "canceled_at": obj.get("canceled_at") or int(time.time()),
        })
        db.schedule_drip(cancel["id"])
        if cancel.get("customer_email"):
            email_service.send_survey_invite(cancel)
        return {"ok": True, "cancellation_id": cancel["id"]}

    if etype == "customer.subscription.created":
        # Resubscribe -> previously canceled customer is a save.
        customer = obj.get("customer", "")
        price = (((obj.get("items") or {}).get("data") or [{}])[0].get("price")
                 or {})
        if isinstance(customer, str) and customer:
            db.mark_saved(customer, price.get("unit_amount") or 0)
        return {"ok": True, "saved": True}

    return {"ok": True, "ignored": etype}


# ---------- admin / cron ----------

@app.post("/admin/drip/run")
def admin_drip_run():
    return {"sent": email_service.run_drip()}


@app.post("/admin/digest/run")
def admin_digest_run():
    return email_service.weekly_digest()


@app.post("/api/test/cancel")
async def test_cancel(request: Request):
    """Dev helper: simulate a cancellation without Stripe. Body: {email, name, plan, mrr_cents}."""
    body = await request.json()
    cancel = db.create_cancellation({
        "stripe_subscription_id": f"sub_test_{int(time.time())}",
        "stripe_customer_id": f"cus_test_{int(time.time())}",
        "customer_email": body.get("email", "test@example.com"),
        "customer_name": body.get("name", "Test User"),
        "plan_name": body.get("plan", "Pro Monthly"),
        "mrr_cents": body.get("mrr_cents", 1200),
        "canceled_at": int(time.time()),
    })
    db.schedule_drip(cancel["id"])
    email_service.send_survey_invite(cancel)
    return {"ok": True, "survey_url": f"{config.BASE_URL}/s/{cancel['survey_token']}",
            "pause_url": f"{config.BASE_URL}/pause/{cancel['survey_token']}"}
