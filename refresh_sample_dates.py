#!/usr/bin/env python3
"""
Keeps the sample data in Plotted's Notion templates current.

Each template has one reference row whose date should always sit a fixed number
of days from today. The script measures how far that row has drifted, then moves
every date in the template's sample databases by the same number of days. Every
verdict (Ready, Stale, Scope gap, Deciding on 2 of 4...) comes back exactly as built.

Self-correcting: a missed run is caught up automatically, and running twice in
a day does nothing the second time.

Usage:
    NOTION_TOKEN=secret_xxx NOTION_TOKEN_2=secret_yyy python3 refresh_sample_dates.py --dry-run
    NOTION_TOKEN=secret_xxx python3 refresh_sample_dates.py
    ... --only "Executive OS"      one template
    ... --today 2026-11-13         pretend it's another day
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
TZ = ZoneInfo("America/Los_Angeles")
MAX_SHIFT_DAYS = 400          # anything bigger means the reference row is wrong
EXCLUDE_PROPERTIES = {"published"}   # real publication dates of real articles

# ref_offset: where the reference row's date belongs, in days from today.
TEMPLATES = [
    {"name": "Executive OS",
     "ref": {"db": "Action Items", "row": "Approve marketing budget", "prop": "Due Date", "offset": -5},
     "targets": ["Action Items", "People & Stakeholders", "Meetings", "Decisions Log",
                 "Strategic Priorities"]},
    {"name": "1:1 Meeting Tracker",
     "ref": {"db": "Open Items", "row": "Check the comp plan question with finance", "prop": "Raised",
             "offset": 0},
     "targets": ["Open Items", "People"]},
    {"name": "Performance & Feedback Tracker",
     "ref": {"db": "Feedback Log", "row": "Caught the race condition before it shipped", "prop": "Date",
             "offset": -4},
     "targets": ["Feedback Log"]},
    {"name": "Career Growth Tracker",
     "ref": {"db": "Evidence", "row": "Presented quarterly results to the exec staff", "prop": "Date",
             "offset": -21},
     "targets": ["Evidence", "Conversations"]},
    {"name": "Interview Loop & Scorecards",
     "ref": {"db": "Candidates", "row": "C. Ndiaye", "prop": "Date Applied", "offset": -28},
     "targets": ["Candidates", "Scorecards"]},
    {"name": "Research Repository OS",
     "ref": {"db": "Participants", "row": "P-101", "prop": "Last Contacted", "offset": -13},
     "targets": ["Insights", "Studies", "Participants", "Requests"]},
    {"name": "Competitor Signal Tracker",
     "ref": {"db": "Signals", "row": "Gridline raises $18M Series A", "prop": "Captured", "offset": -12},
     "targets": ["Signals", "Competitors"]},
    {"name": "The Scholar",
     "ref": {"db": "Assignments & Tasks", "row": "Calculus Problem Set 4", "prop": "Due Date",
             "offset": 6},
     "targets": ["Assignments & Tasks"]},
    {"name": "Assignment Tracker",
     "ref": {"db": "Assignments", "row": "Calculus Problem Set 4", "prop": "Due Date", "offset": 1},
     "targets": ["Assignments"]},
]


class TemplateError(Exception):
    pass


class NotFound(TemplateError):
    pass


# --------------------------------------------------------------------------- API
class NotionClient:
    def __init__(self, token, pace=0.35, retries=4):
        self.token, self.pace, self.retries = token, pace, retries

    def request(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(API + path, data=data, method=method, headers={
                "Authorization": f"Bearer {self.token}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            })
            try:
                time.sleep(self.pace)
                with urllib.request.urlopen(req, timeout=60) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as e:
                retryable = e.code == 429 or e.code >= 500
                if retryable and attempt < self.retries:
                    wait = float(e.headers.get("Retry-After") or 2 ** (attempt + 1))
                    time.sleep(wait)
                    continue
                detail = e.read().decode(errors="replace")[:300]
                raise RuntimeError(f"{method} {path} -> {e.code}: {detail}") from None
            except urllib.error.URLError as e:
                if attempt < self.retries:
                    time.sleep(2 ** (attempt + 1))
                    continue
                raise RuntimeError(f"{method} {path} -> {e.reason}") from None


# ------------------------------------------------------------------- helpers
def norm(s):
    s = re.sub(r"[^a-z0-9&:\- ]", "", (s or "").lower())
    return re.sub(r"\s+", " ", s).strip()


def db_title(db):
    return "".join(t.get("plain_text", "") for t in db.get("title", []))


def title_property(db):
    for name, p in db.get("properties", {}).items():
        if p.get("type") == "title":
            return name
    raise TemplateError(f"database '{db_title(db)}' has no title property")


def paginate(client, method, path, body):
    out, cursor = [], None
    while True:
        b = dict(body)
        if cursor:
            b["start_cursor"] = cursor
        res = client.request(method, path, b)
        out.extend(res.get("results", []))
        if not res.get("has_more"):
            return out
        cursor = res.get("next_cursor")


def find_databases(client, title):
    found = paginate(client, "POST", "/search",
                     {"query": title, "filter": {"property": "object", "value": "database"},
                      "page_size": 100})
    return [d for d in found if d.get("object") == "database" and norm(db_title(d)) == norm(title)]


def root_page(client, obj, cache):
    """The page a database ultimately lives on, walking up through columns and toggles."""
    parent = obj.get("parent", {})
    for _ in range(12):
        kind = parent.get("type")
        if kind == "page_id":
            return parent["page_id"]
        if kind == "workspace":
            return "workspace"
        if kind == "database_id":
            key = parent["database_id"]
            if key not in cache:
                cache[key] = client.request("GET", f"/databases/{key}")
            parent = cache[key].get("parent", {})
        elif kind == "block_id":
            key = parent["block_id"]
            if key not in cache:
                cache[key] = client.request("GET", f"/blocks/{key}")
            parent = cache[key].get("parent", {})
        else:
            break
    return None


def local_date(start):
    if len(start) == 10:
        return date.fromisoformat(start)
    dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
    return (dt.astimezone(TZ) if dt.tzinfo else dt).date()


def shift_value(value, days):
    """Move a Notion date string by whole days, keeping its format and local clock time."""
    if value is None:
        return None
    if len(value) == 10:
        return (date.fromisoformat(value) + timedelta(days=days)).isoformat()
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:          # paired with an explicit time_zone field
        return (dt + timedelta(days=days)).isoformat(timespec="milliseconds")
    local = dt.astimezone(TZ).replace(tzinfo=None) + timedelta(days=days)
    return local.replace(tzinfo=TZ).isoformat(timespec="milliseconds")


def planned_changes(page, days):
    changes = {}
    for name, prop in page.get("properties", {}).items():
        if prop.get("type") != "date" or norm(name) in EXCLUDE_PROPERTIES:
            continue
        d = prop.get("date") or {}
        if not d.get("start"):
            continue
        new = {"start": shift_value(d["start"], days), "end": shift_value(d.get("end"), days)}
        if d.get("time_zone"):
            new["time_zone"] = d["time_zone"]
        changes[name] = {"date": new}
    return changes


def page_title(page):
    for p in page.get("properties", {}).values():
        if p.get("type") == "title":
            return "".join(t.get("plain_text", "") for t in p.get("title", []))
    return page.get("id", "?")


# ------------------------------------------------------------------- core
def find_reference(client, tpl):
    ref = tpl["ref"]
    matches = []
    for db in find_databases(client, ref["db"]):
        tprop = title_property(db)
        rows = paginate(client, "POST", f"/databases/{db['id']}/query",
                        {"filter": {"property": tprop, "title": {"equals": ref["row"]}}, "page_size": 100})
        for row in rows:
            prop = row.get("properties", {}).get(ref["prop"])
            if prop and prop.get("type") == "date" and (prop.get("date") or {}).get("start"):
                matches.append((db, row, prop["date"]["start"]))
    if not matches:
        raise NotFound(f"reference row '{ref['row']}' with a {ref['prop']} date not found in "
                            f"'{ref['db']}' (is the integration connected to this template?)")
    if len(matches) > 1:
        raise TemplateError(f"reference row '{ref['row']}' found {len(matches)} times; connect the "
                            f"integration to the published template page only, not to duplicates")
    return matches[0]


def locate(clients, tpl):
    """Find which workspace holds the template. clients is a list of (label, client)."""
    found = []
    for label, client in clients:
        try:
            found.append((label, client, find_reference(client, tpl)))
        except NotFound:
            continue
    if not found:
        raise TemplateError(f"reference row '{tpl['ref']['row']}' not found in any workspace "
                            f"(is the integration connected to this template?)")
    if len(found) > 1:
        names = ", ".join(f[0] for f in found)
        raise TemplateError(f"this template's reference row is in more than one workspace ({names}); "
                            f"disconnect the copy that isn't the published one")
    return found[0]


def run_template(client, tpl, today, dry_run, log, ref=None):
    ref_db, ref_row, ref_start = ref or find_reference(client, tpl)
    target = today + timedelta(days=tpl["ref"]["offset"])
    days = (target - local_date(ref_start)).days
    if days == 0:
        log(f"  already current (reference row due {target})")
        return 0, 0
    if abs(days) > MAX_SHIFT_DAYS:
        raise TemplateError(f"shift of {days} days is implausible; check the reference row")
    log(f"  shifting by {days:+d} days (reference row {local_date(ref_start)} -> {target})")

    cache = {}
    home = root_page(client, ref_db, cache)
    plan = []
    for title in tpl["targets"]:
        dbs = [d for d in find_databases(client, title) if root_page(client, d, cache) == home]
        if not dbs:
            log(f"  ! no '{title}' database found next to '{tpl['ref']['db']}', skipped")
            continue
        for db in dbs:
            rows = paginate(client, "POST", f"/databases/{db['id']}/query", {"page_size": 100})
            for row in rows:
                changes = planned_changes(row, days)
                if changes:
                    plan.append((title, row, changes))

    # Reference row first: if it can't be written, nothing else moves, so the
    # next run recomputes the same shift instead of applying it twice.
    plan.sort(key=lambda item: item[1]["id"] != ref_row["id"])
    written = failed = 0
    for title, row, changes in plan:
        label = f"{title} / {page_title(row)}: " + ", ".join(
            f"{k} {v['date']['start'][:10]}" for k, v in changes.items())
        if dry_run:
            log(f"    would set {label}")
            written += 1
            continue
        try:
            client.request("PATCH", f"/pages/{row['id']}", {"properties": changes})
            written += 1
        except RuntimeError as e:
            if row["id"] == ref_row["id"]:
                raise TemplateError(f"could not update the reference row, nothing changed: {e}")
            failed += 1
            log(f"    ! failed {label}: {e}")
    return written, failed


def tokens_from_env(env=os.environ):
    """NOTION_TOKEN, NOTION_TOKEN_2, NOTION_TOKEN_3... one per workspace."""
    names = sorted(k for k in env if re.fullmatch(r"NOTION_TOKEN(_\d+)?", k) and env[k].strip())
    return [(n, env[n].strip()) for n in names]


def main(argv=None, clients=None, out=print):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="show what would change, write nothing")
    ap.add_argument("--only", help="run one template by name")
    ap.add_argument("--today", help="YYYY-MM-DD, default is today in Pacific time")
    args = ap.parse_args(argv)

    if clients is None:
        tokens = tokens_from_env()
        if not tokens:
            out("No NOTION_TOKEN secrets are set")
            return 2
        clients = [(name, NotionClient(tok)) for name, tok in tokens]
    today = date.fromisoformat(args.today) if args.today else datetime.now(TZ).date()
    templates = [t for t in TEMPLATES if not args.only or norm(t["name"]) == norm(args.only)]
    if not templates:
        out(f"no template named '{args.only}'")
        return 2

    out(f"Sample-data refresh for {today}{' (dry run)' if args.dry_run else ''}")
    errors = 0
    for tpl in templates:
        out(f"\n{tpl['name']}")
        try:
            label, client, ref = locate(clients, tpl)
            if len(clients) > 1:
                out(f"  found via {label}")
            written, failed = run_template(client, tpl, today, args.dry_run, out, ref=ref)
            if written or failed:
                verb = "would update" if args.dry_run else "updated"
                out(f"  {verb} {written} pages" + (f", {failed} failed" if failed else ""))
            errors += failed > 0
        except (TemplateError, RuntimeError) as e:
            errors += 1
            out(f"  ERROR: {e}")
    out(f"\nDone with {errors} problem(s)." if errors else "\nDone.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
