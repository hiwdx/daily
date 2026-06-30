"""Send today's daily digest. Idempotent on date — re-runs same day are no-ops
unless --force is passed."""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_mail import config, content, render, send, state, subscribers

CST = ZoneInfo("Asia/Shanghai")


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
        # Override without mutating the dataclass: replace it
        conf = type(conf)(**{**conf.__dict__, "dry_run": True})

    st = state.load()
    if not args.force and st.get("last_digest_date") == target:
        print(f"[digest] already sent for {target}; skip (use --force to override)")
        return 0

    body = content.fetch_for(target)
    if not body:
        print(f"[digest] no content found for {target} (rss/archive both empty); abort", file=sys.stderr)
        return 2

    subs = subscribers.active(subscribers.load())
    if args.limit > 0:
        subs = subs[: args.limit]
    if not subs:
        print("[digest] no active subscribers; nothing to do")
        return 0

    subject = render.render_subject(body)
    sent: list[str] = []
    failed: list[tuple[str, str]] = []
    for s in subs:
        html = render.render_html(body, s)
        text = render.render_text(body, s)
        try:
            res = send.send(to=s["email"], subject=subject, html=html, text=text, conf=conf)
            print(f"[digest] {res['status']} → {s['email']}  id={res.get('id')}")
            sent.append(s["email"])
        except Exception as e:
            print(f"[digest] FAIL → {s['email']}: {e}", file=sys.stderr)
            failed.append((s["email"], str(e)))

    if not conf.dry_run and sent:
        st["last_digest_date"] = target
        st["last_digest_recipients"] = sent
        state.save(st)

    if failed:
        print(f"[digest] {len(failed)} failures; exit 1", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
