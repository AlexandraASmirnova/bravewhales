#!/usr/bin/env python3
"""
Brave Whales lane-schedule monitor.

Checks the DaySmart Recreation calendar for Juanita Aquatics Center, for
today through the next 6 days (a full week ahead), and compares the lanes
booked under "Brave Whales" against the expected schedule in
schedule_rules.json. If anything doesn't match (missing lanes, wrong lane
count, wrong pool, or wrong time) -- or if the check itself fails -- a
single summary email is sent via Gmail SMTP.

(Web3Forms -- used by this repo's flyer contact form -- was tried first
since it's already set up here, but its free tier only accepts requests
from a browser; a server-to-server POST from GitHub Actions gets a 403
"Pro plan is required". Gmail SMTP has no such restriction.)

No login/password is needed: the calendar page at
  https://apps.daysmartrecreation.com/dash/x/waveaquatics/calendar
is a web app that reads from a public, unauthenticated JSON API
(api.daysmartrecreation.com). This script talks to that API directly.

DaySmart sometimes books several adjacent lanes as a single event on a
"combined" resource area (e.g. "Main Pool Lanes 2-4" is one event worth 3
lanes), rather than one event per lane -- so lane counts are computed from
the resource-area's name, not just by counting matching events.

Every run also updates history.json (one entry per calendar date, upserted
each time that date is re-checked) which schedule-dashboard/ reads to show
pass/fail history over time.

Environment variables:
  GMAIL_USER             Gmail address to send FROM (required to actually email)
  GMAIL_APP_PASSWORD     Gmail App Password for that address (required to actually email)
  NOTIFY_EMAIL           Who to send the alert to (default: smirnovaae@gmail.com)
  CHECK_DATE             Override "today" with an explicit YYYY-MM-DD (for testing)
"""

from __future__ import annotations

import json
import os
import re
import smtplib
import ssl
import sys
from datetime import date, datetime, timedelta
from email.mime.text import MIMEText
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

# ---- Configuration --------------------------------------------------------

EVENTS_API = "https://api.daysmartrecreation.com/v1/events"
RESOURCE_AREAS_API = "https://api.daysmartrecreation.com/v1/resource-areas"
COMPANY = "waveaquatics"
FACILITY_ID = 1  # Juanita Aquatics Center ("location=1" in the calendar URL)
RENTAL_RESOURCE_ID = 8  # "Juanita Rentals" -- the resource that lane bookings live under
POOL_TIMEZONE = "America/Los_Angeles"
LOOKAHEAD_DAYS = 7  # today + the next 6 days

RULES_FILE = Path(__file__).parent / "schedule_rules.json"
HISTORY_FILE = Path(__file__).parent / "history.json"
HISTORY_RETENTION_DAYS = 400  # a bit over a year of history

# `or` (not a dict default) because GitHub Actions sets env vars to "" for
# unset secrets rather than leaving them unset.
GMAIL_USER = os.environ.get("GMAIL_USER")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")
NOTIFY_EMAIL = os.environ.get("NOTIFY_EMAIL") or "smirnovaae@gmail.com"

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

SINGLE_LANE_RE = re.compile(r"^(Main|Small) Pool Lane (\d+)$")
LANE_RANGE_RE = re.compile(r"^(Main|Small) Pool Lanes (\d+)-(\d+)$")
FULL_POOL_RE = re.compile(r"^Full (Main|Small) Pool$")


# ---- DaySmart API ----------------------------------------------------------

def _get_all_pages(url: str, params: dict) -> list[dict]:
    items: list[dict] = []
    page = 1
    while True:
        resp = requests.get(url, params={**params, "page[number]": page, "page[size]": 100}, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
        items.extend(payload["data"])
        if page >= payload["meta"]["page"]["last-page"]:
            break
        page += 1
    return items


def fetch_events_for_range(start_date: date, end_date: date) -> list[dict]:
    """Fetch every calendar event between start_date and end_date (inclusive)."""
    return _get_all_pages(
        EVENTS_API,
        {
            "company": COMPANY,
            "sort": "start",
            "filter[start__gte]": f"{start_date} 00:00:00",
            "filter[start__lte]": f"{end_date} 23:59:59",
            "filter[resource.facility.my_sam_visible]": "true",
            "filter[eventType.code__not]": "L",
            "filter[resource.facility.id]": FACILITY_ID,
        },
    )


def fetch_resource_area_index() -> dict[str, dict]:
    """Map resource-area id -> {name, pool ('main'/'small'/None), lanes (int/None)}."""
    areas = _get_all_pages(RESOURCE_AREAS_API, {"company": COMPANY, "filter[resource_id]": RENTAL_RESOURCE_ID})

    single_lane_count = {"main": 0, "small": 0}
    for area in areas:
        m = SINGLE_LANE_RE.match(area["attributes"]["name"])
        if m:
            single_lane_count[m.group(1).lower()] += 1

    index: dict[str, dict] = {}
    for area in areas:
        name = area["attributes"]["name"]
        pool = None
        lanes = None
        if m := SINGLE_LANE_RE.match(name):
            pool, lanes = m.group(1).lower(), 1
        elif m := LANE_RANGE_RE.match(name):
            pool, lanes = m.group(1).lower(), int(m.group(3)) - int(m.group(2)) + 1
        elif m := FULL_POOL_RE.match(name):
            pool = m.group(1).lower()
            lanes = single_lane_count[pool]
        index[area["id"]] = {"name": name, "pool": pool, "lanes": lanes}
    return index


# ---- Rule checking ----------------------------------------------------------

def load_rules() -> list[dict]:
    with open(RULES_FILE) as f:
        return json.load(f)


# ---- History (for the dashboard) --------------------------------------------

def load_history() -> dict:
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE) as f:
            return json.load(f)
    return {}


def save_history(history: dict) -> None:
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2, sort_keys=True)
        f.write("\n")


def update_history(history: dict, results: list[dict], checked_at: str) -> None:
    """Upsert each checked date's latest result into history, keyed by ISO
    date. A date gets overwritten every time it's re-checked (it's in the
    7-day lookahead up to 7 times before it arrives), so each entry always
    reflects the most recent, most-informed check for that day."""
    by_date: dict[str, list[dict]] = {}
    for r in results:
        by_date.setdefault(r["date"].isoformat(), []).append(r)

    for date_str, day_results in by_date.items():
        history[date_str] = {
            "checked_at": checked_at,
            "weekday": day_results[0]["weekday"],
            "rules": [
                {
                    "start_time": r["rule"]["start_time"],
                    "end_time": r["rule"]["end_time"],
                    "pool": r["rule"].get("pool", "main"),
                    "label": r["rule"].get("label"),
                    "expected": r["expected_count"],
                    "actual": r["actual_count"],
                    "ok": r["ok"],
                }
                for r in day_results
            ],
        }

    cutoff = (date.today() - timedelta(days=HISTORY_RETENTION_DAYS)).isoformat()
    for stale in [d for d in history if d < cutoff]:
        del history[stale]


def matching_lanes(events: list[dict], area_index: dict, rule: dict) -> list[dict]:
    """Events matching rule's text/pool, and whose time *covers* the rule's
    window, each annotated with its lane count.

    A booking doesn't have to start/end exactly on the rule's times: e.g. a
    single 11:00-13:00 booking fully covers both an "11:00-12:00" rule and a
    "12:00-13:00" rule, and should satisfy both.
    """
    match_text = rule.get("match_text", "Brave Whales").lower()
    pool = rule.get("pool", "main")
    matches = []
    for ev in events:
        a = ev["attributes"]
        desc = a.get("desc") or ""
        if match_text not in desc.lower():
            continue
        if a["start"][11:16] > rule["start_time"] or a["end"][11:16] < rule["end_time"]:
            continue
        area = area_index.get(str(a["resource_area_id"]))
        if not area or area["pool"] != pool or not area["lanes"]:
            continue
        matches.append(
            {
                "resource_area_id": a["resource_area_id"],
                "area_name": area["name"],
                "lanes": area["lanes"],
                "desc": desc,
                "start": a["start"],
                "end": a["end"],
            }
        )
    return matches


def check_rule(events_on_date: list[dict], area_index: dict, rule: dict, target_date: date) -> dict:
    matches = matching_lanes(events_on_date, area_index, rule)
    actual = sum(m["lanes"] for m in matches)
    expected = rule["lane_count"]
    return {
        "date": target_date,
        "weekday": WEEKDAYS[target_date.weekday()],
        "rule": rule,
        "matches": matches,
        "expected_count": expected,
        "actual_count": actual,
        "ok": actual == expected,
    }


# ---- Notification -----------------------------------------------------------

def send_email(subject: str, message: str) -> None:
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        print("GMAIL_USER / GMAIL_APP_PASSWORD not set -- cannot send email.", file=sys.stderr)
        print("---- Email that would have been sent ----", file=sys.stderr)
        print(f"Subject: {subject}\n\n{message}", file=sys.stderr)
        return

    msg = MIMEText(message)
    msg["Subject"] = subject
    msg["From"] = GMAIL_USER
    msg["To"] = NOTIFY_EMAIL

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_USER, [NOTIFY_EMAIL], msg.as_string())
    print(f"Notification email sent to {NOTIFY_EMAIL}.")


def calendar_url(target_date: date) -> str:
    return (
        "https://apps.daysmartrecreation.com/dash/x/waveaquatics/calendar"
        f"?start={target_date}&end={target_date}&location={FACILITY_ID}"
    )


# ---- Main --------------------------------------------------------------------

def main() -> None:
    tz = ZoneInfo(POOL_TIMEZONE)
    now_local = datetime.now(tz)

    start_date = date.fromisoformat(os.environ["CHECK_DATE"]) if os.environ.get("CHECK_DATE") else now_local.date()
    end_date = start_date + timedelta(days=LOOKAHEAD_DAYS - 1)
    week_dates = [start_date + timedelta(days=i) for i in range(LOOKAHEAD_DAYS)]

    rules = load_rules()
    rules_by_weekday: dict[str, list[dict]] = {}
    for r in rules:
        rules_by_weekday.setdefault(r["weekday"], []).append(r)

    if not any(rules_by_weekday.get(WEEKDAYS[d.weekday()]) for d in week_dates):
        print(f"No rules configured for {start_date}..{end_date}. Nothing to check.")
        return

    try:
        events = fetch_events_for_range(start_date, end_date)
        area_index = fetch_resource_area_index()
    except Exception as exc:  # noqa: BLE001 - we want to email on any failure
        send_email(
            f"[Brave Whales] Lane schedule check FAILED ({start_date} - {end_date})",
            "The automated lane-schedule check could not reach the DaySmart "
            f"calendar API for {start_date} through {end_date}.\n\nError: {exc}\n\n"
            f"Please check manually: {calendar_url(start_date)}",
        )
        raise

    events_by_date: dict[date, list[dict]] = {d: [] for d in week_dates}
    for ev in events:
        ev_date = date.fromisoformat(ev["attributes"]["start"][:10])
        if ev_date in events_by_date:
            events_by_date[ev_date].append(ev)

    results = []
    for d in week_dates:
        if not events_by_date[d]:
            # DaySmart hasn't published *anything* for this date yet (facilities
            # only publish a few months out) -- that's "not checked yet", not a
            # mismatch, so skip it rather than falsely flag every rule as missing.
            continue
        for rule in rules_by_weekday.get(WEEKDAYS[d.weekday()], []):
            results.append(check_rule(events_by_date[d], area_index, rule, d))

    history = load_history()
    update_history(history, results, now_local.isoformat())
    save_history(history)

    problems = [r for r in results if not r["ok"]]

    print(f"Checked {len(results)} rule-day(s) from {start_date} to {end_date}:")
    for r in results:
        status = "OK" if r["ok"] else "MISMATCH"
        rule = r["rule"]
        print(
            f"  [{status}] {r['date']} ({r['weekday']}) {rule['start_time']}-{rule['end_time']} "
            f"{rule.get('pool', 'main')} pool: expected {r['expected_count']} lane(s), "
            f"found {r['actual_count']}"
        )

    if not problems:
        print("All rules matched for the week ahead. No email sent.")
        return

    lines = [
        f"The Brave Whales lane schedule for {start_date} through {end_date} "
        "has one or more mismatches:",
        "",
    ]
    for r in problems:
        rule = r["rule"]
        label = f" ({rule['label']})" if rule.get("label") else ""
        lines.append(
            f"- {r['date']} ({r['weekday']}) {rule['start_time']}-{rule['end_time']}{label}, "
            f"{rule.get('pool', 'main')} pool: expected {r['expected_count']} lane(s), "
            f"found {r['actual_count']}."
        )
        if r["matches"]:
            lines.append("  Lanes currently booked:")
            for m in r["matches"]:
                lines.append(f"    - {m['area_name']} ({m['lanes']} lane(s)): {m['desc']}")
        else:
            lines.append("  No matching booking was found at all for this time slot.")
        lines.append(f"  Check/fix it here: {calendar_url(r['date'])}")
        lines.append("")

    send_email(
        f"[Brave Whales] Lane schedule MISMATCH -- {len(problems)} issue(s) in the week ahead",
        "\n".join(lines),
    )


if __name__ == "__main__":
    main()
