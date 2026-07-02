"""Send today's daily digest. Idempotent on date — re-runs same day are no-ops
unless --force is passed."""
from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_mail import config, content, render, send, state, subscribers

CST = ZoneInfo("Asia/Shanghai")


def _email_key(subscriber: dict) -> str:
    return subscriber["email"].strip().lower()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--date", help="YYYY-MM-DD (default: today CST)")
    p.add_argument("--dry-run", action="store_true",
                   help="write .eml files into agent_mail/drafts/ instead of sending")
    p.add_argument("--force", action="store_true",
                   help="send even if state.json says today already sent")
    p.add_argument("--limit", type=int, default=0,
                   help="cap recipients (smoke-test); 0 = no cap")
    args = p.parse_args(argv)

    target = args.date or datetime.now(CST).date().isoformat()
    conf = config.load()
    if args.dry_run:
        conf = replace(conf, dry_run=True)

    st = state.load()
    if not args.force and st.get("last_digest_date") == target:
        print(f"[digest] already sent for {target}; skip (use --force to override)")
        return 0

    body = content.fetch_for(target)
    if not body:
        print(f"[digest] no content found for {target} (rss/archive both empty); abort", file=sys.stderr)
        return 2

    all_subs = subscribers.active(subscribers.load())
    selected_subs = all_subs[: args.limit] if args.limit > 0 else all_subs
    if not selected_subs:
        print("[digest] no active subscribers; nothing to do")
        return 0

    sent_by_date = st.setdefault("digest_sent_recipients_by_date", {})
    if not isinstance(sent_by_date, dict):
        sent_by_date = {}
        st["digest_sent_recipients_by_date"] = sent_by_date
    if args.force and not conf.dry_run:
        sent_by_date[target] = []

    already_sent = set() if args.force or conf.dry_run else set(sent_by_date.get(target, []))
    pending_subs: list[dict] = []
    for sub in selected_subs:
        if _email_key(sub) in already_sent:
            print(f"[digest] skip already-sent recipient for {target} → {sub['email']}")
            continue
        pending_subs.append(sub)

    if not pending_subs:
        active_keys = {_email_key(s) for s in all_subs}
        if not conf.dry_run and active_keys.issubset(already_sent):
            st["last_digest_date"] = target
            st["last_digest_recipients"] = sorted(already_sent)
            st["last_digest_completed_at"] = state.now_iso()
            state.save(st)
        print(f"[digest] no pending subscribers for {target}; nothing to do")
        return 0

    subject = render.render_subject(body)
    sent_keys = set(already_sent)
    sent: list[str] = []
    failed: list[tuple[str, str]] = []
    for s in pending_subs:
        html = render.render_html(body, s)
        text = render.render_text(body, s)
        try:
            res = send.send(to=s["email"], subject=subject, html=html, text=text, conf=conf)
            print(f"[digest] {res['status']} → {s['email']}  id={res.get('id')}")
            sent.append(s["email"])
            if not conf.dry_run:
                sent_keys.add(_email_key(s))
                sent_by_date[target] = sorted(sent_keys)
                st["last_digest_attempt_date"] = target
                state.save(st)
        except Exception as e:
            print(f"[digest] FAIL → {s['email']}: {e}", file=sys.stderr)
            failed.append((s["email"], str(e)))

    active_keys = {_email_key(s) for s in all_subs}
    if not conf.dry_run:
        sent_by_date[target] = sorted(sent_keys)
        st["last_digest_attempt_date"] = target
        st["last_digest_attempted_at"] = state.now_iso()
    if not conf.dry_run and not failed and active_keys.issubset(sent_keys):
        st["last_digest_date"] = target
        st["last_digest_recipients"] = sorted(sent_keys)
        st["last_digest_completed_at"] = state.now_iso()
        state.save(st)
    elif not conf.dry_run:
        state.save(st)

    if failed:
        print(f"[digest] {len(failed)} failures; exit 1", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
