"""Sending layer.

Two backends behind one `send()` API:

  * smtp     — production-ready, what the workflow uses by default
  * agently  — STUB. The agent.qq.com CLI docs do not yet specify the send
               command syntax, the two-phase confirmation flow, or how to
               authenticate without an interactive browser. Code below sketches
               the shape so you only need to fill in two clearly-marked lines
               once the real CLI is available.
"""
from __future__ import annotations

import json
import smtplib
import ssl
import subprocess
import uuid
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path

from . import config, render


# ─── public API ────────────────────────────────────────────────────────────

def send(
    *,
    to: str,
    subject: str,
    html: str,
    text: str,
    conf: config.RuntimeConf,
    extra_headers: dict[str, str] | None = None,
) -> dict:
    """Returns {"status": "sent"|"dry_run"|"draft", "id": str, ...}."""
    if conf.dry_run:
        return _save_eml(to=to, subject=subject, html=html, text=text, conf=conf,
                         tag="dry_run", extra_headers=extra_headers)
    if conf.sender_backend == "agently":
        return _send_agently(to=to, subject=subject, html=html, text=text, conf=conf,
                             extra_headers=extra_headers)
    return _send_smtp(to=to, subject=subject, html=html, text=text, conf=conf,
                      extra_headers=extra_headers)


def save_draft(*, to: str, subject: str, html: str, text: str,
               conf: config.RuntimeConf, reason: str) -> dict:
    return _save_eml(to=to, subject=subject, html=html, text=text, conf=conf,
                     tag=f"draft__{reason}", extra_headers={"X-AgentMail-Draft-Reason": reason})


# ─── shared message builder ────────────────────────────────────────────────

def _build_message(*, to: str, subject: str, html: str, text: str,
                   conf: config.RuntimeConf, extra_headers: dict[str, str] | None) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = render.from_header(conf)
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    msg["Message-ID"] = make_msgid(domain=conf.smtp.from_addr.split("@")[-1])
    if extra_headers:
        for k, v in extra_headers.items():
            msg[k] = v
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    return msg


# ─── SMTP backend ──────────────────────────────────────────────────────────

def _send_smtp(*, to, subject, html, text, conf, extra_headers) -> dict:
    msg = _build_message(to=to, subject=subject, html=html, text=text,
                         conf=conf, extra_headers=extra_headers)
    ctx = ssl.create_default_context()
    if conf.smtp.port == 465:
        with smtplib.SMTP_SSL(conf.smtp.host, conf.smtp.port, context=ctx, timeout=30) as s:
            s.login(conf.smtp.user, conf.smtp.password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(conf.smtp.host, conf.smtp.port, timeout=30) as s:
            s.starttls(context=ctx)
            s.login(conf.smtp.user, conf.smtp.password)
            s.send_message(msg)
    return {"status": "sent", "id": msg["Message-ID"], "backend": "smtp"}


# ─── agently CLI backend (STUB — fill in when CLI semantics are confirmed) ─

def _send_agently(*, to, subject, html, text, conf, extra_headers) -> dict:
    """
    Two-phase confirmation flow as described:

      1. agently-cli send-mail --to <to> --subject <s> --body-file <f>
         → JSON containing a `confirmation_token` (a.k.a. `ctk`)
      2. agently-cli send-mail --confirmation-token <ctk>
         → JSON with final status

    The exact flag names below are PLACEHOLDERS. agent.qq.com/doc/cli-setup.md
    does not yet document the send command; replace the two `cmd1` / `cmd2`
    lines once you have the real spec, and unset the guard at the top.
    """
    raise NotImplementedError(
        "agently backend not wired yet — agent.qq.com docs do not specify the "
        "send command. Set SENDER_BACKEND=smtp, or fill in the two cmd lines "
        "in agent_mail/send.py:_send_agently and remove this guard."
    )

    # --- reference scaffold (kept but unreachable) ---------------------------
    body_file = config.PKG_DIR / "drafts" / f"_agently_{uuid.uuid4().hex}.html"
    body_file.write_text(html, encoding="utf-8")
    try:
        cmd1 = ["agently-cli", "send-mail", "--to", to, "--subject", subject,
                "--body-file", str(body_file), "--format", "json"]
        r1 = subprocess.run(cmd1, capture_output=True, text=True, timeout=60, check=True)
        ctk = json.loads(r1.stdout).get("confirmation_token") or json.loads(r1.stdout).get("ctk")
        if not ctk:
            raise RuntimeError(f"agently: no confirmation_token in: {r1.stdout!r}")
        cmd2 = ["agently-cli", "send-mail", "--confirmation-token", ctk, "--format", "json"]
        r2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=60, check=True)
        return {"status": "sent", "id": ctk, "backend": "agently",
                "raw": r2.stdout.strip()}
    finally:
        body_file.unlink(missing_ok=True)


# ─── eml writer (drafts + dry_run share this) ──────────────────────────────

def _save_eml(*, to, subject, html, text, conf, tag, extra_headers) -> dict:
    msg = _build_message(to=to, subject=subject, html=html, text=text,
                         conf=conf, extra_headers=extra_headers)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    short = uuid.uuid4().hex[:8]
    out: Path = config.DRAFTS_DIR / f"{ts}__{tag}__{short}.eml"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(bytes(msg))
    return {"status": "dry_run" if tag == "dry_run" else "draft",
            "id": msg["Message-ID"], "path": str(out)}
