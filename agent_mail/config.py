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
UNSUBSCRIBE_URL_BASE = "https://mail.hiwd.com/unsubscribe"
UNSUBSCRIBE_HINT = f"取消订阅：访问 {UNSUBSCRIBE_URL_BASE} 输入你的邮箱"


def _env(name: str, default: str | None = None, *, required: bool = False) -> str:
    v = os.environ.get(name, default)
    if required and not v:
        raise RuntimeError(f"missing env: {name}")
    return v or ""


@dataclass(frozen=True)
class AgentlyConf:
    from_addr: str
    from_name: str
    cli_bin: str
    send_timeout: int


@dataclass(frozen=True)
class RuntimeConf:
    agently: AgentlyConf
    dry_run: bool


def load() -> RuntimeConf:
    return RuntimeConf(
        agently=AgentlyConf(
            from_addr=_env("MAIL_FROM", "iworld@agent.qq.com"),
            from_name=_env("MAIL_FROM_NAME", "hiwd daily"),
            cli_bin=_env("AGENTLY_CLI_BIN", "agently-cli"),
            send_timeout=int(_env("AGENTLY_SEND_TIMEOUT", "60")),
        ),
        dry_run=_env("DRY_RUN", "0") == "1",
    )
