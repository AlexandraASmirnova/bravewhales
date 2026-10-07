#!/usr/bin/env python3
"""
Brave Whales lane-schedule monitor.

Checks the DaySmart Recreation calendar for Juanita Aquatics Center, for
today through the next 6 days (a full week ahead), and compares the lanes
booked under "Brave Whales" against the expected schedule in
schedule_rules.json. If anything doesn't match (missing lanes, wrong lane
count, wrong pool, or wrong time) -- or if the check itself fails -- a
single summary email is sent via Web3Forms.

No login/password is needed: the calendar page at
  https://apps.daysmartrecreation.com/dash/x/waveaquatics/calendar
is a web app that reads from a public, unauthenticated JSON API
(api.daysmartrecreation.com). This script talks to that API directly.

DaySmart sometimes books several adjacent lanes as a single event on a
"combined" resource area (e.g. "Main Pool Lanes 2-4" is one event worth 3
lanes), rather than one event per lane -- so lane counts are computed from
the resource-area's name, not just by counting matching events.

Environment variables:
  WEB3FORMS_ACCESS_KEY  Web3Forms access key to send mail with (defaults to
                         the same public key already used by this repo's
                         contact form in 2026-2027/index.html, which
                         delivers to the email that key is registered to)
  NOTIFY_EMAIL           Who the alert is addressed to, for display purposes
                         only (default: smirnovaae@gmail.com) -- actual
                         delivery address is controlled by the Web3Forms key
  CHECK_DATE             Override "today" with an explicit YYYY-MM-DD (for testing)
  FORCE_RUN               If "true", skip the "only run near 5:30am local" time guard
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import date, datetime, timedelta
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

# `or` (not a dict default) because GitHub Actions sets env vars to "" for
# unset secrets rather than leaving them unset.
WEB3FORMS_ACCESS_KEY = os.environ.get("WEB3FORMS_ACCESS_KEY") or "aa8fd2da-7204-4819-a418-5bbe5706fe62"
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


def matching_lanes(events: list[dict], area_index: dict, rule: dict) -> list[dict]:
    """Events matching rule's text/time/pool, each annotated with its lane count."""
    match_text = rule.get("match_text", "Brave Whales").lower()
    pool = rule.get("pool", "main")
    matches = []
    for ev in events:
        a = ev["attributes"]
        desc = a.get("desc") or ""
        if match_text not in desc.lower():
            continue
        if a["start"][11:16] != rule["start_time"] or a["end"][11:16] != rule["end_time"]:
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
    payload = {
        "access_key": WEB3FORMS_ACCESS_KEY,
        "subject": subject,
        "message": message,
        "email": NOTIFY_EMAIL,
        "from_name": "Lane Schedule Monitor",
    }
    resp = requests.post("https://api.web3forms.com/submit", json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"Web3Forms reported failure: {data}")
    print("Notification email sent via Web3Forms.")


def calendar_url(target_date: date) -> str:
    return (
        "https://apps.daysmartrecreation.com/dash/x/waveaquatics/calendar"
        f"?start={target_date}&end={target_date}&location={FACILITY_ID}"
    )


# ---- Main --------------------------------------------------------------------

def main() -> None:
    tz = ZoneInfo(POOL_TIMEZONE)
    now_local = datetime.now(tz)

    force_run = os.environ.get("FORCE_RUN", "").lower() == "true"
    if not force_run and now_local.hour != 5:
        # The workflow fires at two different UTC times to cover both sides of
        # DST; only the one that currently lands at ~5:xxam Pacific should do
        # the real check, so the other near-miss run exits quietly.
        print(
            f"Current time in {POOL_TIMEZONE} is {now_local:%Y-%m-%d %H:%M} "
            "-- not the scheduled ~5:30am run, skipping."
        )
        return

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
        for rule in rules_by_weekday.get(WEEKDAYS[d.weekday()], []):
            results.append(check_rule(events_by_date[d], area_index, rule, d))

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
