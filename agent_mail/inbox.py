"""Inbox handling — narrowly scoped, refuses to touch personal mail.

Rules:
  * Only messages whose Subject begins with one of [AGENT] [SEND_EMAIL] [DAILY]
    [UNSUBSCRIBE] are considered "tasks". Every other message is left UNTOUCHED.
  * [AGENT] subscribe / unsubscribe — driven by the website's mailto: button.
    Adds/removes the From: address from subscribers.json automatically.
  * [SEND_EMAIL] auto-sends only if the subject contains the literal token
    CONFIRM_SEND, regardless of AUTO_SEND_PERSONAL — the token is the anti-relay
    guard so a stranger can't make this system send mail on their behalf just
    by emailing iworld@.
  * [UNSUBSCRIBE] from a current subscriber removes them.
  * IMAP UNSEEN UIDs are tracked in state.unread_first_seen. If any has been
    sitting >= forward_idle_days, send a digest of subjects (no bodies) to
    PERSONAL_EMAIL once, then mark forwarded so we don't repeat.
"""
from __future__ import annotations

import email
import imaplib
import json
import re
from datetime import datetime, timedelta, timezone
from email.header import decode_header
from email.message import Message
from typing import Iterable

from . import config, render, send, state, subscribers

TASK_PREFIXES = ("[AGENT]", "[SEND_EMAIL]", "[DAILY]", "[UNSUBSCRIBE]")
CONFIRM_TOKEN = "CONFIRM_SEND"


# ─── small helpers ─────────────────────────────────────────────────────────

def _decode(s: str | None) -> str:
    if not s:
        return ""
    parts = decode_header(s)
    out = []
    for chunk, enc in parts:
        if isinstance(chunk, bytes):
            try:
                out.append(chunk.decode(enc or "utf-8", errors="replace"))
            except LookupError:
                out.append(chunk.decode("utf-8", errors="replace"))
        else:
            out.append(chunk)
    return "".join(out).strip()


def _subject_prefix(subject: str) -> str | None:
    for p in TASK_PREFIXES:
        if subject.upper().startswith(p):
            return p
    return None


def _plain_body(msg: Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                try:
                    return part.get_content().strip()
                except Exception:
                    payload = part.get_payload(decode=True) or b""
                    return payload.decode(errors="replace").strip()
        return ""
    try:
        return msg.get_content().strip()
    except Exception:
        payload = msg.get_payload(decode=True) or b""
        return payload.decode(errors="replace").strip()


# ─── IMAP session ──────────────────────────────────────────────────────────

class Inbox:
    def __init__(self, conf: config.RuntimeConf):
        if not conf.imap:
            raise RuntimeError("IMAP not configured (set IMAP_USER/IMAP_PASS)")
        self.conf = conf
        self.M = imaplib.IMAP4_SSL(conf.imap.host, conf.imap.port, timeout=30)
        self.M.login(conf.imap.user, conf.imap.password)
        self.M.select("INBOX")

    def close(self):
        try:
            self.M.close()
        finally:
            self.M.logout()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def search_uids(self, criterion: str) -> list[str]:
        typ, data = self.M.uid("search", None, criterion)
        if typ != "OK" or not data or not data[0]:
            return []
        return data[0].decode().split()

    def fetch(self, uid: str) -> Message | None:
        typ, data = self.M.uid("fetch", uid, "(RFC822)")
        if typ != "OK" or not data or not isinstance(data[0], tuple):
            return None
        return email.message_from_bytes(data[0][1])

    def peek_headers(self, uid: str) -> tuple[str, str] | None:
        # BODY.PEEK avoids setting \Seen — important: we must not silently
        # mark personal mail as read just by listing the inbox
        typ, data = self.M.uid("fetch", uid, "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM)])")
        if typ != "OK" or not data or not isinstance(data[0], tuple):
            return None
        hdr = email.message_from_bytes(data[0][1])
        return _decode(hdr.get("Subject")), _decode(hdr.get("From"))


# ─── handlers ──────────────────────────────────────────────────────────────

def _addr(from_header: str) -> str:
    m = re.search(r"[\w.\-+]+@[\w.\-]+", from_header or "")
    return (m.group(0) if m else from_header or "").strip().lower()


def _handle_send_email(msg: Message, conf: config.RuntimeConf) -> dict:
    """Subject form: [SEND_EMAIL] to:foo@bar.com [CONFIRM_SEND] <free text subject>"""
    raw_subject = _decode(msg.get("Subject"))
    body = _plain_body(msg)

    # parse target
    m = re.search(r"to:\s*([\w.\-+]+@[\w.\-]+)", raw_subject, re.IGNORECASE) \
        or re.search(r"^to:\s*([\w.\-+]+@[\w.\-]+)\s*$", body, re.IGNORECASE | re.MULTILINE)
    if not m:
        return {"action": "ignored", "reason": "no recipient parsed"}
    target = m.group(1)

    cleaned_subject = re.sub(r"\[SEND_EMAIL\]|to:\s*[\w.\-+]+@[\w.\-]+|" + CONFIRM_TOKEN,
                             "", raw_subject, flags=re.IGNORECASE).strip()
    cleaned_subject = cleaned_subject or "(no subject)"

    html = f"<pre style='white-space:pre-wrap;font-family:inherit;'>{body}</pre>"
    text = body

    if CONFIRM_TOKEN in raw_subject and conf.auto_send_personal:
        return send.send(to=target, subject=cleaned_subject, html=html, text=text, conf=conf,
                         extra_headers={"X-AgentMail-Origin": "send_email-confirmed"})
    reason = "no_CONFIRM_SEND" if CONFIRM_TOKEN not in raw_subject else "auto_send_personal_off"
    return send.save_draft(to=target, subject=cleaned_subject, html=html, text=text,
                           conf=conf, reason=reason)


def _handle_unsubscribe(msg: Message) -> dict:
    addr = _addr(_decode(msg.get("From")))
    removed = subscribers.remove(addr)
    return {"action": "unsubscribe", "email": addr, "removed": removed}


def _handle_agent(msg: Message) -> dict:
    """[AGENT] subscribe / unsubscribe / <other> — driven by mailto: links on the site."""
    raw_subject = _decode(msg.get("Subject")) or ""
    body = _plain_body(msg) or ""
    addr = _addr(_decode(msg.get("From")))

    rest = re.sub(r"^\s*\[AGENT\]\s*", "", raw_subject, flags=re.IGNORECASE).strip().lower()
    verb = rest.split()[0] if rest else ""

    if verb in ("subscribe", "sub", "订阅"):
        return _do_subscribe(addr, body)
    if verb in ("unsubscribe", "unsub", "退订"):
        removed = subscribers.remove(addr)
        return {"action": "unsubscribe", "email": addr, "removed": removed}
    return {"action": "logged", "subject": raw_subject, "from": addr}


def _do_subscribe(from_addr: str, body: str) -> dict:
    # Body may contain "email: foo@bar.com" if the user changed the prefilled
    # address; otherwise we trust the From: header (most reliable, can't spoof
    # past SPF/DKIM on QQ Mail's inbound checks).
    target = from_addr
    m = re.search(r"email\s*[:=]\s*([\w.\-+]+@[\w.\-]+)", body, re.IGNORECASE)
    if m:
        target = m.group(1).lower()
    if not target or "@" not in target:
        return {"action": "subscribe", "ok": False, "reason": "no valid email"}

    subs = subscribers.load()
    existing = next((s for s in subs if s["email"].lower() == target), None)
    if existing:
        if existing.get("paused"):
            existing["paused"] = False
            existing.setdefault("resubscribed_at",
                                datetime.now(timezone.utc).date().isoformat())
            _write_subs(subs)
            return {"action": "subscribe", "ok": True, "email": target, "status": "resumed"}
        return {"action": "subscribe", "ok": True, "email": target, "status": "already"}

    subs.append({
        "email": target,
        "name": target.split("@")[0],
        "subscribed_at": datetime.now(timezone.utc).date().isoformat(),
    })
    _write_subs(subs)
    return {"action": "subscribe", "ok": True, "email": target, "status": "added"}


def _write_subs(subs: list[dict]) -> None:
    config.SUBSCRIBERS_PATH.write_text(
        json.dumps(subs, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


# ─── orchestration ─────────────────────────────────────────────────────────

def process_tasks(conf: config.RuntimeConf, st: dict) -> list[dict]:
    """Walk recent UNSEEN, handle ONLY task-prefixed messages, return audit log."""
    log: list[dict] = []
    if not conf.imap:
        return log
    processed: set[str] = set(st.get("processed_message_ids", []))

    with Inbox(conf) as ix:
        # last 7 days, unseen, with bracketed prefix in subject
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%d-%b-%Y")
        uids = ix.search_uids(f"(UNSEEN SINCE {cutoff})")
        for uid in uids:
            peek = ix.peek_headers(uid)
            if not peek:
                continue
            subject, _from = peek
            prefix = _subject_prefix(subject)
            if not prefix:
                # personal mail — leave untouched, do not mark seen
                continue
            full = ix.fetch(uid)
            if full is None:
                continue
            mid = full.get("Message-ID") or f"uid:{uid}"
            if mid in processed:
                continue

            try:
                if prefix == "[SEND_EMAIL]":
                    res = _handle_send_email(full, conf)
                elif prefix == "[UNSUBSCRIBE]":
                    res = _handle_unsubscribe(full)
                elif prefix == "[AGENT]":
                    res = _handle_agent(full)
                else:
                    # [DAILY] reserved for future use; just log
                    res = {"action": "logged", "subject": subject}
            except Exception as e:
                res = {"action": "error", "error": str(e)}
            res["uid"] = uid
            res["message_id"] = mid
            res["subject"] = subject
            log.append(res)
            processed.add(mid)
            # Only mark task messages as seen — never personal ones.
            if res.get("action") not in {"error", "ignored"}:
                ix.M.uid("store", uid, "+FLAGS", r"(\Seen)")

    st["processed_message_ids"] = list(processed)[-500:]  # bounded
    return log


def maybe_forward_idle_summary(conf: config.RuntimeConf, st: dict) -> dict | None:
    """If any UNSEEN UID has been sitting >= forward_idle_days, forward subjects."""
    if not conf.imap or not conf.personal_email or conf.forward_idle_days <= 0:
        return None
    now = datetime.now(timezone.utc)
    seen_map: dict[str, str] = dict(st.get("unread_first_seen", {}))
    forwarded: set[str] = set(st.get("forwarded_unread_ids", []))

    pending: list[tuple[str, str, str]] = []  # (uid, subject, first_seen_iso)
    with Inbox(conf) as ix:
        uids = ix.search_uids("UNSEEN")
        # refresh first-seen map: new UIDs get stamped now, gone UIDs evicted
        live = set(uids)
        for u in uids:
            seen_map.setdefault(u, now.isoformat())
        seen_map = {u: ts for u, ts in seen_map.items() if u in live}

        for u in uids:
            if u in forwarded:
                continue
            first = datetime.fromisoformat(seen_map[u])
            if now - first < timedelta(days=conf.forward_idle_days):
                continue
            peek = ix.peek_headers(u)
            if not peek:
                continue
            subj, _ = peek
            if _subject_prefix(subj):
                continue  # task mail, not personal — don't include
            pending.append((u, subj, seen_map[u]))

    st["unread_first_seen"] = seen_map
    if not pending:
        return None

    bullets = "".join(
        f"<li>[{first[:10]}] <code>{_html_safe(subj or '(no subject)')}</code></li>"
        for _u, subj, first in pending
    )
    html = (
        f"<p>你的 Agent Mail 收件箱有 {len(pending)} 封未读邮件已超过 "
        f"{conf.forward_idle_days} 天未查看。仅转发主题，不含正文：</p>"
        f"<ul>{bullets}</ul>"
        f"<p style='font-size:13px;color:#86868b;'>"
        f"如需开启正文转发，将 Secret <code>FORWARD_INCLUDE_BODY=1</code> 设为开启。</p>"
    )
    text = "你有 {} 封超过 {} 天未读的邮件：\n".format(len(pending), conf.forward_idle_days) + \
           "\n".join(f"  [{f[:10]}] {s or '(no subject)'}" for _u, s, f in pending)

    res = send.send(
        to=conf.personal_email,
        subject=f"[hiwd] {len(pending)} 封 Agent Mail 未读邮件提醒",
        html=html,
        text=text,
        conf=conf,
        extra_headers={"X-AgentMail-Kind": "idle-summary"},
    )
    forwarded.update(u for u, _, _ in pending)
    st["forwarded_unread_ids"] = list(forwarded)[-1000:]
    st["last_idle_forward_at"] = now.isoformat()
    return {"forwarded": len(pending), **res}


def _html_safe(s: str) -> str:
    from html import escape
    return escape(s)
