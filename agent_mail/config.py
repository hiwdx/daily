"""Runtime config — reads env (GitHub Secrets) and exposes typed values."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = REPO_ROOT / "docs"
RSS_PATH = DOCS_DIR / "rss.xml"
ARCHIVE_DIR = DOCS_DIR / "archive"
ARCHIVE_INDEX = DOCS_DIR / "archive.json"

PKG_DIR = REPO_ROOT / "agent_mail"
SUBSCRIBERS_PATH = PKG_DIR / "subscribers.json"
STATE_PATH = PKG_DIR / "state" / "state.json"
DRAFTS_DIR = PKG_DIR / "drafts"

SITE_URL = "https://daily.hiwd.com"
SITE_NAME = "hiwd daily · AI 行业每日简报"
UNSUBSCRIBE_HINT = (
    "如需退订，请回复本邮件，标题以 [UNSUBSCRIBE] 开头。"
)


def _env(name: str, default: str | None = None, *, required: bool = False) -> str:
    v = os.environ.get(name, default)
    if required and not v:
        raise RuntimeError(f"missing env: {name}")
    return v or ""


@dataclass(frozen=True)
class SmtpConf:
    host: str
    port: int
    user: str
    password: str
    from_addr: str
    from_name: str


@dataclass(frozen=True)
class ImapConf:
    host: str
    port: int
    user: str
    password: str


@dataclass(frozen=True)
class RuntimeConf:
    smtp: SmtpConf
    imap: ImapConf | None
    personal_email: str            # where idle-summary is forwarded
    auto_send_personal: bool       # default False — personal mail stays as drafts
    forward_idle_days: int         # default 3
    dry_run: bool                  # default False; --dry-run flips it on
    sender_backend: str            # "smtp" | "agently" — agently is a TODO stub


def load() -> RuntimeConf:
    smtp = SmtpConf(
        host=_env("SMTP_HOST", "smtp.qq.com"),
        port=int(_env("SMTP_PORT", "465")),
        user=_env("SMTP_USER", required=True),
        password=_env("SMTP_PASS", required=True),
        from_addr=_env("SMTP_FROM", _env("SMTP_USER", required=True)),
        from_name=_env("SMTP_FROM_NAME", "hiwd daily"),
    )
    imap_user = _env("IMAP_USER", _env("SMTP_USER", ""))
    imap_pass = _env("IMAP_PASS", _env("SMTP_PASS", ""))
    imap = None
    if imap_user and imap_pass:
        imap = ImapConf(
            host=_env("IMAP_HOST", "imap.qq.com"),
            port=int(_env("IMAP_PORT", "993")),
            user=imap_user,
            password=imap_pass,
        )
    return RuntimeConf(
        smtp=smtp,
        imap=imap,
        personal_email=_env("PERSONAL_EMAIL", ""),
        auto_send_personal=_env("AUTO_SEND_PERSONAL", "1") != "0",
        forward_idle_days=int(_env("FORWARD_IDLE_DAYS", "3")),
        dry_run=_env("DRY_RUN", "0") == "1",
        sender_backend=_env("SENDER_BACKEND", "smtp"),
    )
