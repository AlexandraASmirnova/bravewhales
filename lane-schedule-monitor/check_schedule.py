#!/usr/bin/env python3
"""
Brave Whales lane-schedule monitor.

Checks the DaySmart Recreation calendar for "today" at Juanita Aquatics
Center and compares the lanes booked under "Brave Whales" against the
expected schedule in schedule_rules.json. If anything doesn't match
(missing lanes, wrong lane count, or wrong time) -- or if the check itself
fails -- an email is sent to NOTIFY_EMAIL.

No login/password is needed: the calendar page at
  https://apps.daysmartrecreation.com/dash/x/waveaquatics/calendar
is a web app that reads from a public, unauthenticated JSON API
(api.daysmartrecreation.com). This script talks to that API directly.

Environment variables:
  GMAIL_USER            Gmail address to send FROM (required to actually email)
  GMAIL_APP_PASSWORD    Gmail App Password for that address (required to actually email)
  NOTIFY_EMAIL           Who to notify (default: smirnovaae@gmail.com)
  CHECK_DATE             Override "today" with an explicit YYYY-MM-DD (for testing)
  FORCE_RUN               If "true", skip the "only run near 5:30am local" time guard
"""

from __future__ import annotations

import json
import os
import smtplib
import ssl
import sys
from datetime import date, datetime
from email.mime.text import MIMEText
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

# ---- Configuration --------------------------------------------------------

API_BASE = "https://api.daysmartrecreation.com/v1/events"
COMPANY = "waveaquatics"
FACILITY_ID = 1  # Juanita Aquatics Center ("location=1" in the calendar URL)
POOL_TIMEZONE = "America/Los_Angeles"

RULES_FILE = Path(__file__).parent / "schedule_rules.json"

NOTIFY_EMAIL = os.environ.get("NOTIFY_EMAIL", "smirnovaae@gmail.com")
GMAIL_USER = os.environ.get("GMAIL_USER")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


# ---- DaySmart API ----------------------------------------------------------

def fetch_events_for_date(target_date: date) -> list[dict]:
    """Fetch every calendar event on target_date (all pages)."""
    events: list[dict] = []
    page = 1
    while True:
        params = {
            "company": COMPANY,
            "sort": "start",
            "page[size]": 100,
            "page[number]": page,
            "filter[start__gte]": f"{target_date} 00:00:00",
            "filter[start__lte]": f"{target_date} 23:59:59",
            "filter[resource.facility.my_sam_visible]": "true",
            "filter[eventType.code__not]": "L",
            "filter[resource.facility.id]": FACILITY_ID,
        }
        resp = requests.get(API_BASE, params=params, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
        events.extend(payload["data"])
        last_page = payload["meta"]["page"]["last-page"]
        if page >= last_page:
            break
        page += 1
    return events


# ---- Rule checking ----------------------------------------------------------

def load_rules() -> list[dict]:
    with open(RULES_FILE) as f:
        return json.load(f)


def rules_for_weekday(rules: list[dict], weekday_name: str) -> list[dict]:
    return [r for r in rules if r["weekday"] == weekday_name]


def matching_lanes(events: list[dict], match_text: str, start_time: str, end_time: str) -> list[dict]:
    """Distinct lanes whose description matches match_text and whose start/end
    exactly equal the expected window (HH:MM, 24h, facility-local time)."""
    lanes = []
    for ev in events:
        a = ev["attributes"]
        desc = a.get("desc") or ""
        if match_text.lower() not in desc.lower():
            continue
        if a["start"][11:16] == start_time and a["end"][11:16] == end_time:
            lanes.append(
                {
                    "resource_area_id": a["resource_area_id"],
                    "desc": desc,
                    "start": a["start"],
                    "end": a["end"],
                }
            )
    return lanes


def check_rule(events: list[dict], rule: dict) -> dict:
    lanes = matching_lanes(
        events,
        rule.get("match_text", "Brave Whales"),
        rule["start_time"],
        rule["end_time"],
    )
    expected = rule["lane_count"]
    actual = len(lanes)
    return {
        "rule": rule,
        "lanes_found": lanes,
        "expected_count": expected,
        "actual_count": actual,
        "ok": actual == expected,
    }


# ---- Notification -----------------------------------------------------------

def send_email(subject: str, body: str) -> None:
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        print("GMAIL_USER / GMAIL_APP_PASSWORD not set -- cannot send email.", file=sys.stderr)
        print("---- Email that would have been sent ----", file=sys.stderr)
        print(f"Subject: {subject}\n\n{body}", file=sys.stderr)
        return

    msg = MIMEText(body)
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

    if os.environ.get("CHECK_DATE"):
        today = date.fromisoformat(os.environ["CHECK_DATE"])
    else:
        today = now_local.date()
    weekday_name = WEEKDAYS[today.weekday()]

    rules = load_rules()
    todays_rules = rules_for_weekday(rules, weekday_name)

    if not todays_rules:
        print(f"No rules configured for {weekday_name} ({today}). Nothing to check.")
        return

    try:
        events = fetch_events_for_date(today)
    except Exception as exc:  # noqa: BLE001 - we want to email on any failure
        send_email(
            f"[Brave Whales] Lane schedule check FAILED for {today}",
            "The automated lane-schedule check could not reach the DaySmart "
            f"calendar API for {today} ({weekday_name}).\n\nError: {exc}\n\n"
            f"Please check manually: {calendar_url(today)}",
        )
        raise

    results = [check_rule(events, rule) for rule in todays_rules]
    problems = [r for r in results if not r["ok"]]

    print(f"Checked {len(todays_rules)} rule(s) for {weekday_name} {today}:")
    for r in results:
        status = "OK" if r["ok"] else "MISMATCH"
        print(
            f"  [{status}] {r['rule']['start_time']}-{r['rule']['end_time']}: "
            f"expected {r['expected_count']} lane(s), found {r['actual_count']}"
        )

    if not problems:
        print("All rules matched. No email sent.")
        return

    lines = [f"The Brave Whales lane schedule for {weekday_name}, {today} does not match what's expected.", ""]
    for r in problems:
        rule = r["rule"]
        label = f" ({rule['label']})" if rule.get("label") else ""
        lines.append(
            f"- {rule['start_time']}-{rule['end_time']}{label}: expected "
            f"{r['expected_count']} lane(s), found {r['actual_count']}."
        )
        if r["lanes_found"]:
            lines.append("  Lanes currently booked:")
            for lane in r["lanes_found"]:
                lines.append(f"    - resource area {lane['resource_area_id']}: {lane['desc']}")
        else:
            lines.append("  No matching booking was found at all for this time slot.")
        lines.append("")
    lines.append(f"Check/fix it here: {calendar_url(today)}")

    send_email(
        f"[Brave Whales] Lane schedule MISMATCH for {today} ({weekday_name})",
        "\n".join(lines),
    )


if __name__ == "__main__":
    main()
