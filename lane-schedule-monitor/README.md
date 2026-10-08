# Lane Schedule Monitor

Checks, every morning, that Brave Whales' pool lanes are booked correctly for
**today through the next 6 days** on the
[Juanita Aquatics Center calendar](https://apps.daysmartrecreation.com/dash/x/waveaquatics/calendar),
and emails an alert if they aren't.

**Manage the schedule with a form instead of editing JSON by hand:**
👉 https://alexandraasmirnova.github.io/bravewhales/schedule-admin/

**See pass/fail history over time, a calendar view, and open issues:**
👉 https://alexandraasmirnova.github.io/bravewhales/schedule-dashboard/

## How it works

- **Schedule**: a GitHub Actions workflow (`.github/workflows/lane-schedule-check.yml`)
  runs daily at ~5am/6am Pacific (one cron entry, 13:00 UTC; it drifts an
  hour across daylight saving but that doesn't matter here). The script runs
  unconditionally whenever triggered rather than checking the clock, since
  GitHub's scheduled runs are "best effort" and can be delayed by hours.
- **Data source**: the DaySmart calendar page is a web app that calls a
  public, unauthenticated JSON API
  (`https://api.daysmartrecreation.com/v1/events`) to list events. No login
  is required to *read* the calendar, so the script calls that API directly
  — fast, reliable, and nothing to log in with or keep secret on that side.
- **Rules**: `schedule_rules.json` lists the lanes Brave Whales should have,
  per weekday/time slot/pool. For each of the next 7 days the script fetches
  that day's events, finds the ones whose description contains `"Brave
  Whales"` at the expected start/end time and pool, and sums up the lanes
  they cover.
  - DaySmart sometimes books several adjacent lanes as **one event** on a
    combined resource area (e.g. `"Main Pool Lanes 2-4"` = 3 lanes in a
    single booking) rather than one event per lane, so the script parses
    lane counts from the resource-area name instead of just counting
    matching events.
- **Alerting**: if any day's rule doesn't match (missing booking, wrong lane
  count, wrong pool, wrong time) — or if the DaySmart API can't be reached at
  all — one summary email covering the whole week is sent via Gmail SMTP
  (requires a one-time setup, see below). [Web3Forms](https://web3forms.com)
  (used by this repo's flyer contact form) was tried first since it was
  already set up here, but its free tier only accepts requests sent from a
  browser — a server-side request from GitHub Actions gets a 403 ("Pro plan
  is required"). Gmail SMTP has no such restriction.
- **History**: every run also updates `history.json` — one entry per
  calendar date, overwritten each time that date gets re-checked, so it
  always reflects the most recent, most-informed check for that day. The
  workflow commits this file back to the repo automatically (using the
  built-in `GITHUB_TOKEN`, no extra secret needed). `schedule-dashboard/`
  reads it to show a calendar view, pass rate, and trend over time.

## Current rules

See the **[admin page](https://alexandraasmirnova.github.io/bravewhales/schedule-admin/)**
for the live, editable schedule, or `schedule_rules.json` directly. Each
entry looks like:

```json
{
  "weekday": "Monday",
  "start_time": "20:30",
  "end_time": "21:30",
  "lane_count": 3,
  "pool": "main",
  "match_text": "Brave Whales",
  "label": "Monday evening practice"
}
```

`weekday` is the full English name (`Monday`, `Tuesday`, …). Times are
24-hour `HH:MM` in the pool's local time (America/Los_Angeles), matching
what's shown on the calendar. `pool` is `"main"` or `"small"` (Juanita's
pool is split into a 6-lane Main Pool / deep end and a 6-lane Small Pool /
shallow end). `match_text` (optional, defaults to `"Brave Whales"`) is
matched case-insensitively against the event description.

## Admin page (add/edit/update the schedule)

https://alexandraasmirnova.github.io/bravewhales/schedule-admin/

A small static page (`schedule-admin/index.html`, served by the same GitHub
Pages site as the flyer) that lets you pick a weekday, start/end time, lane
count, and Main/Small pool from a form, and commits the result straight to
`schedule_rules.json` in this repo — no local git commands needed.

It talks directly to the GitHub REST API from your browser (no backend
server). Viewing the current schedule needs nothing; **saving changes**
needs a GitHub token:

1. Go to https://github.com/settings/personal-access-tokens/new
2. **Repository access** → *Only select repositories* → `bravewhales`
3. **Permissions** → *Repository permissions* → **Contents: Read and write**
4. Generate the token and paste it into the "Connect to GitHub" box on the
   admin page.

The token is saved only in that browser's `localStorage` and is sent only
to `api.github.com` — never to any other server. Use "Forget token" to
remove it from the browser, or just revoke it on GitHub's token settings
page.

## Dashboard (history, calendar view, open issues)

https://alexandraasmirnova.github.io/bravewhales/schedule-dashboard/

A read-only static page (`schedule-dashboard/index.html`) that reads
`history.json` and `schedule_rules.json` straight from GitHub (public,
unauthenticated, no token needed) and shows:

- Summary stats (days tracked, all-time pass rate, days with a mismatch,
  last checked time)
- "Issues in [Month]" — every mismatch in whichever month the calendar
  below is showing, updates as you navigate months
- A month calendar, color-coded green/red/pending/no-practice, click a day
  for its rule-by-rule detail
- A bar chart of mismatches per week over the last 10 weeks

## One-time setup (for the scheduled checker to actually send email)

Add two repo secrets (**Settings → Secrets and variables → Actions → New
repository secret**):

| Secret | Value |
|---|---|
| `GMAIL_USER` | The Gmail address to send *from*, e.g. `smirnovaae@gmail.com` |
| `GMAIL_APP_PASSWORD` | A 16-character [Gmail App Password](https://myaccount.google.com/apppasswords) for that account (not your normal Gmail password — requires 2-Step Verification to be enabled first) |

Optional:

| Secret | Value |
|---|---|
| `NOTIFY_EMAIL` | Who gets the alert (defaults to `smirnovaae@gmail.com` if unset) |

Without `GMAIL_USER`/`GMAIL_APP_PASSWORD` set, the script still runs the
check and logs what it *would* have emailed to the workflow's log output —
useful for a first test run before wiring up email.

## Testing it manually

Go to **Actions → Lane schedule check → Run workflow** in GitHub. You can
optionally pass a specific `check_date` (e.g. `2026-10-06`, a Monday is in
the following week from here) to test against a known week instead of the
real "today".

To run it locally:

```bash
cd lane-schedule-monitor
pip install -r requirements.txt
GMAIL_USER=you@gmail.com GMAIL_APP_PASSWORD=xxxxxxxxxxxxxxxx \
CHECK_DATE=2026-10-06 python check_schedule.py
```
