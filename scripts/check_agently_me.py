#!/usr/bin/env python3
"""Verify that agently-cli is authenticated as the expected mailbox."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from typing import Any

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _parse_json(stdout: str) -> dict[str, Any]:
    start = stdout.find("{")
    if start < 0:
        raise ValueError("no JSON object in agently-cli output")
    decoder = json.JSONDecoder()
    data, _ = decoder.raw_decode(stdout[start:])
    if not isinstance(data, dict):
        raise ValueError("agently-cli output JSON is not an object")
    return data


def _collect_emails(value: Any, out: set[str]) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(v, str) and (k.lower() in {"email", "from_addr", "address"} or EMAIL_RE.match(v)):
                out.add(v.strip().lower())
            else:
                _collect_emails(v, out)
    elif isinstance(value, list):
        for item in value:
            _collect_emails(item, out)
    elif isinstance(value, str) and EMAIL_RE.match(value):
        out.add(value.strip().lower())


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    expected = (argv[0] if argv else os.environ.get("MAIL_FROM", "")).strip().lower()
    if not expected:
        print("expected email missing; pass it as argv[1] or set MAIL_FROM", file=sys.stderr)
        return 2

    cli_bin = os.environ.get("AGENTLY_CLI_BIN", "agently-cli")
    result = subprocess.run(
        [cli_bin, "+me"],
        capture_output=True,
        text=True,
        timeout=int(os.environ.get("AGENTLY_ME_TIMEOUT", "30")),
    )
    if result.returncode != 0:
        print(result.stdout, end="")
        print(result.stderr, end="", file=sys.stderr)
        return result.returncode

    try:
        data = _parse_json(result.stdout)
    except Exception as exc:
        print(f"failed to parse agently-cli +me output: {exc}", file=sys.stderr)
        print(result.stdout[:1000], file=sys.stderr)
        return 1

    found: set[str] = set()
    _collect_emails(data, found)
    if expected not in found:
        print(
            f"agently-cli is not authenticated as {expected}; found: {sorted(found) or 'none'}",
            file=sys.stderr,
        )
        return 1

    print(f"[agently] authenticated as {expected}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
