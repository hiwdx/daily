"""Bootstrap the D-Bus SecretService "login" collection for agently-cli in CI.

Why: zalando/go-keyring hardcodes `/org/freedesktop/secrets/collection/login`
as the collection to read/write. `secret-tool store` writes to whatever the
"default" alias points to — usually not "login" on a fresh gnome-keyring.
This script guarantees the master.key lands where the CLI actually reads.

Reads master key from env AGENTLY_MASTER_KEY (must include go-keyring-base64:
prefix, exactly as exported from macOS Keychain).
"""
from __future__ import annotations

import os
import sys

try:
    import secretstorage  # type: ignore[import-not-found]
except ImportError:
    print("secretstorage not installed. Run: pip install secretstorage", file=sys.stderr)
    sys.exit(2)


def main() -> int:
    master = os.environ.get("AGENTLY_MASTER_KEY")
    if not master:
        print("AGENTLY_MASTER_KEY env var not set", file=sys.stderr)
        return 2

    conn = secretstorage.dbus_init()

    # Get or create the "login" collection (alias-based lookup works even
    # if the canonical path is not /collection/login yet — the CLI reads
    # via the same alias resolution the D-Bus service provides).
    try:
        login = secretstorage.get_collection_by_alias(conn, "login")
    except secretstorage.exceptions.ItemNotFoundException:
        print("no 'login' alias; creating collection")
        login = secretstorage.create_collection(conn, "Login", alias="login")

    if login.is_locked():
        login.unlock()

    attrs = {"service": "agently-cli", "username": "master.key"}

    # Purge stale entries with the same attributes (idempotent re-runs)
    for item in login.search_items(attrs):
        item.delete()

    item = login.create_item(
        label="Password for 'master.key' on 'agently-cli'",
        attributes=attrs,
        secret=master.encode("utf-8"),
        replace=True,
    )

    print(f"stored: label={item.get_label()!r} path={item.item_path}")
    print(f"collection path: {login.collection_path}")

    # Read-back sanity check via the default keyring API surface — proves
    # the item is discoverable using {service, username} attributes.
    found = list(login.search_items(attrs))
    if not found:
        print("read-back FAILED: item not found by attributes", file=sys.stderr)
        return 1
    print(f"read-back ok: {len(found)} item(s) match")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
