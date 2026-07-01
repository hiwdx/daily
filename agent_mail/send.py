"""Sending layer — Agent Mail via `agently-cli`.

Two-phase confirmation flow (mandatory, not skippable):

  1. `agently-cli message +send --to X --subject S --body-file B`
     → JSON with `data.confirmation_required=true` and `data.confirmation_token`
  2. `agently-cli message +send --confirmation-token <ctk>`
     → JSON with `queued=true`

The CLI honours `AGENTLY_ACCESS_TOKEN` env var (bypasses keychain) and reads
`AGENTLY_CLI_CONFIG_DIR` for the encrypted refresh-token bundle. On CI we
restore both the encrypted bundle and the keychain master key ahead of the
call, then let the CLI mint a fresh access token itself via `auth refresh`.
"""
from __future__ import annotations

import json
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
    return _send_agently(to=to, subject=subject, html=html, conf=conf)


def save_draft(*, to: str, subject: str, html: str, text: str,
               conf: config.RuntimeConf, reason: str) -> dict:
    return _save_eml(to=to, subject=subject, html=html, text=text, conf=conf,
                     tag=f"draft__{reason}",
                     extra_headers={"X-AgentMail-Draft-Reason": reason})


# ─── agently CLI backend ───────────────────────────────────────────────────

def _send_agently(*, to: str, subject: str, html: str,
                  conf: config.RuntimeConf) -> dict:
    body_file = config.DRAFTS_DIR / f"_agently_{uuid.uuid4().hex}.html"
    body_file.parent.mkdir(parents=True, exist_ok=True)
    body_file.write_text(html, encoding="utf-8")
    try:
        # `--body-file` is required to be relative to cwd. Run from repo root
        # and pass the relative path.
        rel = body_file.relative_to(config.REPO_ROOT)
        phase1 = _run_cli([
            conf.agently.cli_bin, "message", "+send",
            "--to", to,
            "--subject", subject,
            "--body-file", str(rel),
        ], timeout=conf.agently.send_timeout)
        ctk = _dig(phase1, "data", "confirmation_token")
        if not ctk:
            raise RuntimeError(
                f"agently: no confirmation_token in first-phase response: "
                f"{json.dumps(phase1, ensure_ascii=False)[:400]}"
            )
        phase2 = _run_cli([
            conf.agently.cli_bin, "message", "+send",
            "--confirmation-token", ctk,
        ], timeout=conf.agently.send_timeout)
        queued = _dig(phase2, "data", "queued") or phase2.get("queued")
        if not queued:
            raise RuntimeError(
                f"agently: send not queued: {json.dumps(phase2, ensure_ascii=False)[:400]}"
            )
        return {"status": "sent", "id": ctk, "backend": "agently"}
    finally:
        body_file.unlink(missing_ok=True)


def _run_cli(cmd: list[str], *, timeout: int) -> dict:
    """Run agently-cli, parse JSON stdout. Raises if exit != 0 or bad JSON."""
    r = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(config.REPO_ROOT),
    )
    if r.returncode != 0:
        raise RuntimeError(
            f"agently-cli exit {r.returncode}: "
            f"stderr={r.stderr.strip()[:400]!r} stdout={r.stdout.strip()[:200]!r}"
        )
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"agently-cli non-JSON stdout: {e}: {r.stdout[:400]!r}")


def _dig(d: dict, *path: str):
    cur = d
    for k in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


# ─── eml writer (drafts + dry_run share this) ──────────────────────────────

def _save_eml(*, to: str, subject: str, html: str, text: str,
              conf: config.RuntimeConf, tag: str,
              extra_headers: dict[str, str] | None) -> dict:
    msg = EmailMessage()
    msg["From"] = render.from_header(conf)
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    msg["Message-ID"] = make_msgid(domain=conf.agently.from_addr.split("@")[-1])
    if extra_headers:
        for k, v in extra_headers.items():
            msg[k] = v
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    short = uuid.uuid4().hex[:8]
    out: Path = config.DRAFTS_DIR / f"{ts}__{tag}__{short}.eml"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(bytes(msg))
    return {"status": "dry_run" if tag == "dry_run" else "draft",
            "id": msg["Message-ID"], "path": str(out)}
