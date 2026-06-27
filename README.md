# IPO Tracker

Automatically tracks every NSE/BSE mainboard IPO for the years you choose and
writes them to a Google Sheet, then checks daily for new listings and for IPOs
that climb 25%+ above their listing price.

- **Sheet1** - the full list of all IPOs.
- **Sheet2** - only IPOs that are more than 25% above their listing price
  (refreshed every run, so later movers show up automatically).
- **Email** - one alert the first time an IPO crosses the 25% mark.

## What you need
- A Google account
- A Google Cloud "service account" + its `credentials.json`
- A Google Sheet shared with that service account (Editor)
- (For email) a Gmail App Password

## Setup
1. `pip install -r requirements.txt`
2. Open `ipo_tracker.py` and fill in the CONFIG block (years, Sheet ID, paths,
   email settings). Put `credentials.json` next to the script.
3. Run it once:  `python ipo_tracker.py`

## Run it automatically
- **Windows:** Task Scheduler, daily at 11:00 AM (your PC must be on).
- **Cloud (always on):** GitHub Actions - see `.github/workflows/daily.yml`.
  Add these repo secrets: `GOOGLE_CREDENTIALS` (paste the whole credentials.json),
  `IPO_SHEET_ID`, `IPO_SENDER_EMAIL`, `IPO_APP_PASSWORD`, `IPO_RECIPIENTS`.

## Security
`credentials.json` and your Gmail App Password are private. They are listed in
`.gitignore` and must never be committed. Anyone you share the repo with should
create their own.
