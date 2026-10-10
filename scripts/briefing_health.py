"""Check the complete published issue, not just its archive-index entry."""
import argparse
import hashlib
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from xml.etree import ElementTree


def validate_issue(read, date):
    entries = json.loads(read("archive.json"))
    if not any(e.get("date") == date for e in entries):
        raise ValueError(f"Archive index has no {date}")
    date_cn = datetime.strptime(date, "%Y-%m-%d").strftime("%Y年%m月%d日")
    bodies = []
    for path in ("index.html", f"archive/{date[:7]}/{date}.html"):
        content = read(path).decode("utf-8")
        if not re.search(r'<span class="hero-date">' + re.escape(date_cn) + r'\s', content):
            raise ValueError(f"{path} is missing the current issue date")
        match = re.search(r"<!-- Briefing body -->\s*(.*?)\s*<!-- Archive -->", content, re.S)
        if not match or len(match.group(1).strip()) < 80:
            raise ValueError(f"{path} has no briefing body")
        bodies.append(match.group(1).strip())
    if bodies[0] != bodies[1]:
        raise ValueError("Homepage and archive briefing bodies differ")
    rss = ElementTree.fromstring(read("rss.xml"))
    first = rss.find("./channel/item/link")
    if first is None or not (first.text or "").endswith(f"/{date}.html"):
        raise ValueError("RSS does not point to today's issue")
    description = rss.find("./channel/item/description")
    if description is None or (description.text or "").strip() != bodies[1]:
        raise ValueError("RSS content differs from today's archive")


def should_generate(docs, date):
    try:
        validate_issue(lambda path: (docs / path).read_bytes(), date)
        status = json.loads((docs / "status.json").read_text())
        return status.get("date") != date or status.get("mode") != "editorial"
    except (OSError, ValueError, TypeError, KeyError, ElementTree.ParseError):
        return True


def verify_online(docs, date, base_url, attempts=12, interval=15):
    validate_issue(lambda path: (docs / path).read_bytes(), date)
    paths = ["index.html", "archive.json", "rss.xml", "status.json", f"archive/{date[:7]}/{date}.html"]
    expected = {p: (docs / p).read_bytes() for p in paths}
    revision = hashlib.sha256(expected["index.html"]).hexdigest()[:16]
    for attempt in range(attempts):
        try:
            for path in paths:
                req = Request(base_url.rstrip("/") + "/" + path + "?revision=" + revision,
                              headers={"Cache-Control": "no-cache", "User-Agent": "hiwd-daily-health/1.0"})
                with urlopen(req, timeout=15) as response:
                    actual = response.read()
                if actual != expected[path]:
                    raise ValueError(f"Live {path} differs from the committed issue")
            print(f"Verified homepage, archive, RSS and status online for {date}")
            return
        except (OSError, ValueError) as exc:
            print(f"Publish check {attempt + 1}/{attempts}: {exc}", flush=True)
            if attempt + 1 == attempts:
                raise
            time.sleep(interval)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["needed", "local", "online"])
    parser.add_argument("--docs", type=Path, default=Path("docs"))
    parser.add_argument("--date", default=datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d"))
    parser.add_argument("--url", default="https://daily.hiwd.com")
    args = parser.parse_args()
    if args.command == "needed":
        print("true" if should_generate(args.docs, args.date) else "false")
    elif args.command == "local":
        validate_issue(lambda path: (args.docs / path).read_bytes(), args.date)
    else:
        verify_online(args.docs, args.date, args.url)


if __name__ == "__main__":
    main()
