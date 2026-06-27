# IPO Tracker

An automated tracker for Indian mainboard IPOs (NSE/BSE). It builds and maintains
a Google Sheet of every IPO for the years you choose, runs itself every day in the
cloud, and emails you the moment an IPO crosses a return threshold you set.

You do not have to touch it once it is set up. It quietly updates your sheet each
day and only emails you when something worth acting on happens.

---

## The idea behind it

The strategy this tool is built around is simple:

> When an IPO rises past a certain percentage **above its listing price** after
> listing (the default is **35%**), it often shows momentum and has a good chance
> of continuing upward.

So the tracker watches every IPO, and the first time one climbs past that mark, it
flags it into a "good IPOs" list and emails you a buy suggestion. The threshold is
a single number you can change to anything you like.

---

## What it produces (three tabs in your Google Sheet)

The tool only ever writes to **three** tabs (named in the config). Any other tabs
you add to the same spreadsheet are never touched — keep your own notes/data there
safely.

### 1. `IPOs 25-26` — the full list
Every mainboard IPO for the years you chose. Grows over time (new listings are
added daily). Columns:

| Column | Meaning |
|--------|---------|
| IPO Name | Company name |
| Listing Date | Date it listed |
| Issue Price | IPO price |
| Listing Day Price | Price at listing (the opening price) |
| Current Price | Live price (auto-updates via `GOOGLEFINANCE`) |
| Listing Gain | % gain at listing vs issue price |
| Current Return | % gain now vs issue price |
| Diff (Current - Listing) | How much it has moved since listing |

### 2. `Good_IPOs` — the ones that crossed the mark
A copy of only the IPOs whose gain since listing is above your threshold. An IPO is
added here once, the first time it crosses (it never leaves and is never
duplicated). Same columns as above, plus two extras:

| Column | Meaning |
|--------|---------|
| Net Return % (entry at +25%) | `Current Return − Listing Gain − 25`. The return measured from the point you would actually buy (once it is already 25% above listing), **not** from the issue price. |
| Profit (Rs 10000) | Estimated profit if you invested ₹10,000 at that entry point. |

(The "25" and "10000" above follow the `CROSSING_MARK` and `INVESTMENT_PER_IPO`
settings — change those and these columns update.)

### 3. `Net Profit/Loss` — the summary
A small rollup of the Good_IPOs tab:

| Metric | Meaning |
|--------|---------|
| Total Invested (Rs) | ₹10,000 × number of good IPOs |
| Total Profit (Rs) | Sum of the profit column |
| Net Profit / Loss (%) | Total profit ÷ total invested × 100 |

---

## The email alert

The first time an IPO crosses your threshold, you get one email (and only one, ever,
for that stock). It is formatted as a buy suggestion with all the details:

```
Subject: BUY alert: 1 IPO(s) crossed 25%

BUY: SEDEMAC Mechatronics Ltd.  ->  invest Rs 10000
    Listing Date      : 2026-03-11
    Issue Price       : Rs 1352
    Listing Day Price : Rs 1535
    Current Price     : Rs 2666.2
    Listing Gain      : 13.54%
    Current Return    : 97.2%
    Net Return (entry at +25%) : 58.66%
    Est. Profit on Rs 10000 : Rs 5866.0
```

You can send the alert to multiple email addresses.

---

## How it works (in short)

1. Downloads public IPO performance data for each year you list.
2. Reads the data, works out listing gain / current return / etc. for each IPO.
3. Appends any new IPOs to the master tab, and any newly-crossing IPOs to the
   good tab, then refreshes the summary.
4. Sends the buy email for newly-crossed IPOs.

It uses `requests` to read the data and `gspread` to write the sheet — no browser,
no paid services.

---

## What you need

- A Google account
- A Google Cloud **service account** and its `credentials.json` key file
- A Google Sheet shared with that service account as **Editor**
- A **Gmail App Password** (only if you want email alerts)

---

## Setup

### 1. Get the code
Download or clone this repo, then install the libraries:
```
pip install -r requirements.txt
```

### 2. Create a Google service account (so the script can write your sheet)
1. Go to <https://console.cloud.google.com/> and create a project.
2. Enable **Google Sheets API** and **Google Drive API** (search each, click Enable).
3. **APIs & Services -> Credentials -> Create Credentials -> Service Account**, name it
   anything, finish.
4. Open the service account -> **Keys -> Add Key -> Create new key -> JSON**. A
   `credentials.json` downloads. Keep it private.

### 3. Make and share the Sheet
1. Create a new Google Sheet.
2. Click **Share**, paste the service account's email (the `client_email` line
   inside `credentials.json`), set it to **Editor**.
3. Copy the **Sheet ID** from the URL — the long code between `/d/` and `/edit`.

### 4. (Optional) Set up email
1. On the sending Gmail account, turn on **2-Step Verification**.
2. Create an **App Password** at <https://myaccount.google.com/apppasswords> and copy
   the 16-character code.

### 5. Fill in the config
Open `ipo_tracker.py` and edit the CONFIG block at the top (see the table below),
put `credentials.json` next to the script, and run it once:
```
python ipo_tracker.py
```

---

## Run it automatically

### Option A — your computer (simple)
Use your OS scheduler (e.g. Windows Task Scheduler) to run `python ipo_tracker.py`
daily. Your computer has to be on at that time.

### Option B — the cloud, always on (recommended)
This repo includes a GitHub Actions workflow (`.github/workflows/daily.yml`) that
runs the script every day automatically, even when your computer is off.

1. Push the project to a GitHub repo.
2. In the repo: **Settings -> Secrets and variables -> Actions -> New repository secret**,
   and add these (so your private keys are never in the code):

   | Secret name | Value |
   |-------------|-------|
   | `GOOGLE_CREDENTIALS` | the entire contents of `credentials.json` |
   | `IPO_SHEET_ID` | your Sheet ID |
   | `IPO_SENDER_EMAIL` | your Gmail address |
   | `IPO_APP_PASSWORD` | your 16-char Gmail App Password |
   | `IPO_RECIPIENTS` | alert emails, comma-separated, e.g. `a@x.com,b@y.com` |

3. Go to the **Actions** tab and run it once to test (a "Run workflow" button).

The schedule in `daily.yml` is `30 5 * * *` (UTC) = **11:00 AM India time**. Change
that cron line to run at a different time. (Note: GitHub pauses a schedule after 60
days of no commits — any small commit re-activates it.)

---

## Customize it for your own needs

Everything is controlled from the **CONFIG block** at the top of `ipo_tracker.py`:

| Setting | What it does |
|---------|--------------|
| `YEARS` | Which years of IPOs to track, e.g. `["2024", "2025", "2026"]`. |
| `SHEET_ID` | Your Google Sheet ID. |
| `SHEET1_NAME` / `SHEET2_NAME` / `SHEET3_NAME` | The three tab names. Rename freely. |
| `CROSSING_MARK` | **The key number.** An IPO joins `Good_IPOs` when it is more than this % above its listing price, and this is also the assumed buy point (used in the Net Return formula and the email). Change this one line to use any threshold. |
| `INVESTMENT_PER_IPO` | The amount assumed invested per good IPO (default `10000`), used in the profit columns and the email. |
| `SHEET2_DIRECTION` | `"above"` (default) flags IPOs above the mark; `"below"` flags those below it. |
| `USE_GOOGLEFINANCE_FORMULA` | `True` puts a live price formula in Current Price; `False` stores a fixed scraped value. |
| `INCLUDE_REITS_INVITS` | `False` keeps only regular equity IPOs; `True` also includes REITs/InvITs. |
| `SEND_EMAIL_ALERTS` | `True`/`False` to turn email on/off. |
| `EMAIL_ON_FIRST_RUN` | Keep `False` so the first big run does not email you the whole back-catalogue. |
| `SENDER_EMAIL` / `SENDER_APP_PASSWORD` / `RECIPIENT_EMAILS` | Email account and recipients. |
| `REBUILD` | See below. |

### When you change `CROSSING_MARK` (or other settings)
Daily runs are **add-only** — they never rewrite existing rows. So if you change the
threshold, existing rows keep their old values. To apply the change to everything:

1. Set `REBUILD = True`.
2. Run it once (this wipes and rebuilds the three tabs with the new settings).
3. Set `REBUILD = False` again.

Leaving `REBUILD = True` would wipe and rebuild on every run and suppress emails, so
always switch it back.

---

## Security

`credentials.json` and your Gmail App Password are private. They are listed in
`.gitignore` and must never be committed to the repo. The cloud setup keeps them as
encrypted GitHub Secrets instead. Anyone who forks this project should create their
own Google service account, sheet, and email credentials.

---

## Notes

- This reads publicly available IPO performance data. Run it about once a day; do
  not hammer the source.
- This is a personal tracking tool, not financial advice. Do your own research
  before investing.
