"""CI debug: dump keyring state + env token comparison."""
from __future__ import annotations

import os
import sys

import secretstorage  # type: ignore[import-not-found]

conn = secretstorage.dbus_init()
c = secretstorage.get_collection_by_alias(conn, "login")
items = list(c.search_items({"service": "agently-cli", "username": "master.key"}))
print(f"items: {len(items)}")
if not items:
    sys.exit(0)

v = items[0].get_secret().decode("utf-8", errors="replace")
env = os.environ.get("AGENTLY_MASTER_KEY", "")
print(f"master.key length (from keyring): {len(v)}")
print(f"master.key prefix (from keyring): {v[:22]!r}")
print(f"master.key length (from env):     {len(env)}")
print(f"master.key prefix (from env):     {env[:22]!r}")
print(f"exact match: {v == env}")
