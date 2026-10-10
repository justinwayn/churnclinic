"""Email delivery. Resend when RESEND_API_KEY is set, otherwise stub mode.

Stub mode logs every email to email_outbox.log (JSON lines) so the drip
can be tested end-to-end with $0 spent and no credentials.
"""
import json
import os
import time
import urllib.request

import config
import db

OUTBOX = os.path.join(os.path.dirname(__file__), "email_outbox.log")


def render(template: dict, ctx: dict) -> tuple[str, str]:
    subject = template["subject"].format(**ctx)
    body = template["body"].format(**ctx)
    return subject, body


def build_ctx(cancel: dict) -> dict:
    token = cancel.get("survey_token", "")
    return {
        "name": cancel.get("customer_name") or "there",
        "email": cancel.get("customer_email") or "",
        "product": config.PRODUCT_NAME,
        "founder": config.FOUNDER_NAME,
        "plan": cancel.get("plan_name") or "your plan",
        "survey_url": f"{config.BASE_URL}/s/{token}",
        "pause_url": f"{config.BASE_URL}/pause/{token}",
    }


def send(to: str, subject: str, body: str, tag: str = "") -> dict:
    record = {
        "ts": int(time.time()),
        "to": to,
        "subject": subject,
        "tag": tag,
        "via": "resend" if config.resend_configured() else "stub",
    }
    if config.resend_configured():
        req = urllib.request.Request(
            "https://api.resend.com/emails",
            data=json.dumps(
                {"from": config.EMAIL_FROM, "to": [to],
                 "subject": subject, "text": body}
            ).encode(),
            headers={"Authorization": f"Bearer {config.RESEND_API_KEY}",
                     "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                record["resend"] = json.loads(r.read().decode())
        except Exception as e:  # noqa: BLE001
            record["error"] = str(e)
    else:
        record["body"] = body
        with open(OUTBOX, "a") as f:
            f.write(json.dumps(record) + "\n")
    return record


def run_drip(now: int | None = None) -> list[dict]:
    """Send every due win-back email. Returns list of send records."""
    results = []
    for drip in db.due_drips(now):
        cancel = {
            "customer_email": drip["customer_email"],
            "customer_name": drip["customer_name"],
            "plan_name": drip["plan_name"],
            "mrr_cents": drip["mrr_cents"],
            "survey_token": drip["survey_token"],
        }
        template = db.get_template(drip["email_key"])
        subject, body = render(template, build_ctx(cancel))
        rec = send(drip["customer_email"], subject, body, tag=drip["email_key"])
        db.mark_drip_sent(drip["id"])
        results.append({"drip_id": drip["id"], "email_key": drip["email_key"],
                        "to": drip["customer_email"], **rec})
    return results


def send_survey_invite(cancel: dict) -> dict:
    """Immediate 'why did you cancel' email on subscription.deleted."""
    template = db.get_template("acknowledge")
    # Reuse acknowledge template but send instantly (not via drip scheduler).
    subject, body = render(template, build_ctx(cancel))
    return send(cancel["customer_email"], subject, body, tag="survey_invite")


def weekly_digest() -> dict:
    """Build the weekly churn digest. Stub-sends to EMAIL_FROM's owner.

    In a real deploy this goes to the founder's email; here we log it.
    """
    s = db.stats()
    lines = [
        f"ChurnClinic weekly digest — {config.PRODUCT_NAME}",
        "",
        f"Cancellations (all time): {s['total_cancellations']}",
        f"Saved: {s['saved']}  (save rate {s['save_rate']}%)",
        f"Paused: {s['paused']}",
        f"Recovered MRR: ${s['recovered_mrr_cents']/100:.2f}",
        f"Lost MRR: ${s['lost_mrr_cents']/100:.2f}",
        f"Survey response rate: {s['survey_response_rate']}%",
        "",
        "Top cancellation reasons:",
    ]
    for r in s["reasons"][:5]:
        lines.append(f"  - {r['label']}: {r['count']}")
    body = "\n".join(lines)
    return send(config.EMAIL_FROM, f"Weekly churn digest — {config.PRODUCT_NAME}",
                body, tag="weekly_digest")
