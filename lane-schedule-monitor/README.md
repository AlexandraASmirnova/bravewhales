# Lane Schedule Monitor

Checks, every morning, that Brave Whales' pool lanes are booked correctly for
*today* on the [Juanita Aquatics Center calendar](https://apps.daysmartrecreation.com/dash/x/waveaquatics/calendar),
and emails an alert if they aren't.

## How it works

- **Schedule**: a GitHub Actions workflow (`.github/workflows/lane-schedule-check.yml`)
  runs daily at ~5:30am Pacific (two cron entries cover both sides of
  daylight saving time; the script itself figures out which one is the real
  5:30am run and no-ops on the other).
- **Data source**: the DaySmart calendar page is a web app that calls a
  public, unauthenticated JSON API
  (`https://api.daysmartrecreation.com/v1/events`) to list events. No login
  is required to *read* the calendar, so the script calls that API directly
  — fast, reliable, and nothing to log in with or keep secret on that side.
- **Rules**: `schedule_rules.json` lists the lanes Brave Whales should have,
  per weekday/time slot. The script fetches today's events, finds the ones
  whose description contains `"Brave Whales"` at the expected start/end
  time, and compares the count of distinct lanes found against what's
  expected.
- **Alerting**: if a rule doesn't match (missing booking, wrong lane count,
  wrong time) — or if the DaySmart API can't be reached at all — an email is
  sent via Gmail SMTP.

## Current rules

| Weekday | Time | Lanes |
|---|---|---|
| Monday | 8:30–9:30 PM | 3 |

Edit `schedule_rules.json` to add more. Each entry looks like:

```json
{
  "weekday": "Monday",
  "start_time": "20:30",
  "end_time": "21:30",
  "lane_count": 3,
  "match_text": "Brave Whales",
  "label": "Monday evening practice"
}
```

`weekday` is the full English name (`Monday`, `Tuesday`, …). Times are
24-hour `HH:MM` in the pool's local time (America/Los_Angeles), matching
what's shown on the calendar. `match_text` (optional, defaults to `"Brave
Whales"`) is matched case-insensitively against the event description.

## One-time setup

The workflow needs two repo secrets so it can send mail from a Gmail
account (**Settings → Secrets and variables → Actions → New repository
secret**):

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
optionally pass a specific `check_date` (e.g. `2026-10-12`, a Monday) to
test against a known day instead of today. `force_run` defaults to `true`
for manual runs so it ignores the "only run near 5:30am" guard.

To run it locally:

```bash
cd lane-schedule-monitor
pip install -r requirements.txt
GMAIL_USER=you@gmail.com GMAIL_APP_PASSWORD=xxxxxxxxxxxxxxxx \
CHECK_DATE=2026-10-12 FORCE_RUN=true \
python check_schedule.py
```
