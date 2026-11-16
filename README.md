# Plotted sample-data refresh

Every morning this moves the dates in each template's sample data so the
template always looks the way it did on the day it was built: Tom is always
"Nothing in 74 days", the navigation insight is always Stale, B. Kimura is
always "Deciding on 2 of 4", and This Week is never empty.

Each template has one reference row whose date belongs a fixed number of days
from today. The script measures how far that row has drifted and moves every
date in that template by the same amount. A missed day is caught up on the next
run; a second run on the same day changes nothing.

## One-time setup (about 25 minutes)

The templates live across two Notion workspaces, and a Notion integration can
only see the workspace it was made in, so there are two keys.

1. **One integration per workspace.** Switch to the first workspace, go to
   notion.so/profile/integrations, create an **Internal** integration in that
   workspace called "Sample refresh", with Read content and Update content
   only. Copy the secret. Switch workspaces and do the same again.
2. **Connect each template to its own workspace's integration.** On each
   published template page, ••• → Connections → Sample refresh: Executive OS,
   1:1 Meeting Tracker, Performance & Feedback Tracker, Career Growth Tracker,
   Interview Loop & Scorecards, Research Repository OS, Competitor Signal
   Tracker, The Scholar, Assignment Tracker. Only the published pages, not test
   duplicates. The integration can only touch pages connected to it, so this is
   the safety list.
3. **GitHub secrets.** Settings → Secrets and variables → Actions:
   `NOTION_TOKEN` = first workspace's secret, `NOTION_TOKEN_2` = second's.
   Which is which doesn't matter; the script finds each template wherever it is.
4. **First run.** Actions → Refresh template sample dates → Run workflow, with
   Dry run ticked. Each template should say which key found it and
   "shifting by +N days". Then run again with Dry run unticked. From then on it
   runs every morning on its own.

## Expected first-run shifts (from 13 Nov 2026)

| Template | Shift |
|---|---|
| Executive OS | +94 |
| 1:1 Meeting Tracker | +23 |
| Performance & Feedback Tracker | +73 |
| Career Growth Tracker | +52 |
| Interview Loop & Scorecards | +53 |
| Research Repository OS | +72 |
| Competitor Signal Tracker | +53 |
| The Scholar | +73 |
| Assignment Tracker | +72 |

A very different number means a reference row was edited by hand. Fix that
row's date (or the offset in `TEMPLATES`) before running for real.

## What it never touches

- Any property called Published (real articles' real dates)
- Created time, formulas and rollups (Notion won't let anything edit them)
- Research Source Library (no date-driven views)
- Any page the integration isn't connected to

## Still yours to do by hand

- **Each quarter:** Executive OS Priorities Quarter/Year and the
  "Quarter: Q_ 20__" header line.
- **Each term:** The Scholar's semester line.
- **Each January:** titles that name a year ("Draft 2027 planning framework").
- Summary cards with typed-in numbers ("Overdue actions: 2") don't update
  themselves. Consider replacing them with an instruction line.

## When it reports an ERROR

- *not found in any workspace* — the row was renamed or deleted, or the
  template isn't connected to its workspace's integration. Fix the row title in
  `TEMPLATES` or reconnect.
- *found N times* or *more than one workspace* — a copy of the template is
  connected. Disconnect the copy.
- *no 'X' database found* — a database was renamed. Update `targets`.
- GitHub emails you when a run fails.

## Adding a template

Add an entry to `TEMPLATES`: a reference database, a row title in it, a date
property on that row, the number of days from today that date should sit, and
the databases to move along with it.
