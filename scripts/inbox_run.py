"""Run the inbox poller: process [AGENT]/[SEND_EMAIL]/[DAILY]/[UNSUBSCRIBE]
tasks, and send the idle-summary if applicable.

Personal mail is never read or modified."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_mail import config, inbox, state


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--no-forward", action="store_true",
                   help="skip the 3-day idle-summary forward")
    args = p.parse_args(argv)

    conf = config.load()
    if args.dry_run:
        conf = type(conf)(**{**conf.__dict__, "dry_run": True})
    if not conf.imap:
        print("[inbox] IMAP not configured; nothing to do")
        return 0

    st = state.load()
    log = inbox.process_tasks(conf, st)
    for entry in log:
        print(f"[inbox] {json.dumps(entry, ensure_ascii=False)}")

    if not args.no_forward:
        fwd = inbox.maybe_forward_idle_summary(conf, st)
        if fwd:
            print(f"[inbox] idle-summary: {json.dumps(fwd, ensure_ascii=False)}")

    if not conf.dry_run:
        state.save(st)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
