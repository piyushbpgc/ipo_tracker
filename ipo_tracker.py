# =============================================================================
#  IPO TRACKER  -  multi-year NSE/BSE mainboard IPO tracker -> Google Sheets
# =============================================================================
#  Sheet1 = EVERY IPO (master list). Add-only: new IPOs are appended, existing
#           rows are never touched, so your notes and ordering stay put.
#  Sheet2 = only IPOs whose Diff (Current Return - Listing Gain) > threshold.
#           REBUILT every run, so an IPO that crosses the mark weeks AFTER
#           listing shows up here automatically (and one that drops below
#           leaves).
#  Email  = every run, checks Good_IPOs for any row whose "Alert Sent" column
#           is not TRUE (new crossers, or ones that failed to send before) and
#           emails all of them in one message. Marks each row TRUE on success,
#           leaves it FALSE (so it retries next run) if sending fails.
#
#  Secrets can be read from environment variables (used by the GitHub Actions
#  cloud run) and otherwise fall back to the values typed below.
# =============================================================================

import os

# -----------------------------------------------------------------------------
#  >>>>>>>>>>>>>>>>>>>>>>  CONFIG  -  EDIT ONLY THIS BLOCK  <<<<<<<<<<<<<<<<<<<<<<
# -----------------------------------------------------------------------------

YEARS = ["2026"]            # all the years you want, on one line

SHEET_ID = os.environ.get("IPO_SHEET_ID", "PASTE_YOUR_GOOGLE_SHEET_ID_HERE")
SHEET1_NAME = "IPOs 26"        # the full master list
SHEET2_NAME = "Good_IPOs"         # only IPOs more than 25% above listing
SHEET3_NAME = "Net Profit/Loss"   # the summary / rollup tab
INVESTMENT_PER_IPO = 5000     # rupees invested per Good IPO (used in profit calc)
CREDENTIALS_PATH = os.environ.get(
    "IPO_CREDENTIALS_PATH", r"C:\Users\YourName\Desktop\IPOTracker\credentials.json")

USE_GOOGLEFINANCE_FORMULA = True
INCLUDE_REITS_INVITS = False

# THE ONE NUMBER THAT CONTROLS EVERYTHING:
# - an IPO moves to Good_IPOs when its gain-since-listing is above this %, and
# - it is also the assumed buy point, so Net Return = Current - Listing - this %.
# Change this single line to use a different marker (e.g. 30).
CROSSING_MARK = 30
SHEET2_DIRECTION = "above"          # "above": in Good_IPOs when Diff > CROSSING_MARK

# One-time helper: True wipes both sheets and rebuilds. Leave False for daily.
REBUILD = True

# Email alerts.
SEND_EMAIL_ALERTS = True
EMAIL_ON_FIRST_RUN = True  # False = seed Good_IPOs silently on the very first run
SENDER_EMAIL = os.environ.get("IPO_SENDER_EMAIL", "youremail@gmail.com")
SENDER_APP_PASSWORD = os.environ.get("IPO_APP_PASSWORD", "xxxx xxxx xxxx xxxx")
RECIPIENT_EMAILS = os.environ.get(
    "IPO_RECIPIENTS", "piyushlalwani2021@gmail.com").split(",")

# -----------------------------------------------------------------------------
#  >>>>>>>>>>>>>>>>>>>>>>>>>>>  END OF CONFIG  <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
# -----------------------------------------------------------------------------


import json
import time
import ssl
import smtplib
from email.message import EmailMessage

import requests
import gspread
from oauth2client.service_account import ServiceAccountCredentials

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

# IPOs (master) keeps the plain Diff column in H.
MASTER_HEADERS = [
    "IPO Name", "Listing Date", "Issue Price", "Listing Day Price",
    "Current Price", "Listing Gain", "Current Return", "Diff (Current - Listing)",
]
MASTER_LAST_COL = "H"

# Good_IPOs gets two extra columns (net return, profit) plus an Alert Sent flag.
GOOD_HEADERS = [
    "IPO Name", "Listing Date", "Issue Price", "Listing Day Price",
    "Current Price", "Listing Gain", "Current Return",
    f"Net Return % (entry at +{CROSSING_MARK}%)", f"Profit (Rs {INVESTMENT_PER_IPO})",
    "Alert Sent",
]
GOOD_LAST_COL = "J"
ALERT_SENT_COL = "J"        # column letter for the Alert Sent flag
ALERT_SENT_IDX = 9          # 0-based index of "Alert Sent" within a Good_IPOs row


# ----------------------------- scraping --------------------------------------
def extract_performance_json(html_text):
    key = "performancesDetails"
    idx = html_text.find(key)
    if idx == -1:
        return None
    start = html_text.find("[", idx)
    depth, i = 0, start
    while i < len(html_text):
        ch = html_text[i]
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                break
        i += 1
    raw = html_text[start:i + 1]
    try:
        unescaped = json.loads('"' + raw + '"')
    except Exception:
        unescaped = raw.replace('\\"', '"').replace('\\u0026', '&').replace('\\/', '/')
    return json.loads(unescaped)


def to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def scrape_year(year):
    url = f"https://www.chittorgarh.com/ipo/ipo_perf_tracker.asp?year={year}"
    print(f"Downloading {year} ...")
    try:
        page = requests.get(url, headers=HEADERS, timeout=30)
        page.raise_for_status()
    except Exception as error:
        print(f"  WARNING: could not download {year} -> {error}")
        return []
    raw_list = extract_performance_json(page.text)
    if not raw_list:
        print(f"  WARNING: no data found for {year}.")
        return []

    out = []
    for item in raw_list:
        try:
            if not INCLUDE_REITS_INVITS and str(item.get("ipo_issue_type", "")).strip() != "IPO":
                continue
            listing_date = str(item.get("il_ipo_listing_date", ""))[:10]
            if not listing_date.startswith(str(year)):
                continue
            issue_price = to_float(item.get("ipo_issue_price_final"))
            if issue_price is None:
                continue
            listing_price = to_float(item.get("ildt_open_price")) or to_float(item.get("ildt_close_price"))
            cmp_value = to_float(item.get("nse_close")) or to_float(item.get("bse_close"))
            listing_gain = round((listing_price - issue_price) / issue_price * 100, 2) \
                if (listing_price is not None and issue_price) else None
            current_return = round((cmp_value - issue_price) / issue_price * 100, 2) \
                if (cmp_value is not None and issue_price) else None
            diff = round(current_return - listing_gain, 2) \
                if (current_return is not None and listing_gain is not None) else None
            out.append({
                "company": str(item.get("ipo_company_name", "")).strip(),
                "listing_date": listing_date,
                "issue_price": issue_price,
                "listing_price": listing_price if listing_price is not None else "",
                "cmp": cmp_value if cmp_value is not None else "",
                "listing_gain": listing_gain if listing_gain is not None else "",
                "current_return": current_return,
                "diff": diff,
                "ticker": str(item.get("il_nse_script_symbol", "")).strip(),
            })
        except Exception as row_error:
            print(f"  (skipped one row: {row_error})")
    print(f"  {year}: {len(out)} IPO(s).")
    return out


def build_records():
    seen, records = set(), []
    for year in YEARS:
        for rec in scrape_year(year):
            key = rec["company"].lower()
            if key in seen:
                continue
            seen.add(key)
            records.append(rec)
        time.sleep(1)
    records.sort(key=lambda r: r["listing_date"], reverse=True)
    return records


# ----------------------------- sheets ----------------------------------------
def open_spreadsheet():
    scope = ["https://www.googleapis.com/auth/spreadsheets",
             "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_PATH, scope)
    return gspread.authorize(creds).open_by_key(SHEET_ID)


def get_or_create_ws(spreadsheet, name):
    """Find a tab ignoring stray spaces/capitalisation, so we never make a duplicate."""
    target = name.strip().lower()
    for ws in spreadsheet.worksheets():
        if ws.title.strip().lower() == target:
            return ws
    return spreadsheet.add_worksheet(title=name, rows=2000, cols=12)


def in_sheet2(rec):
    if rec["diff"] is None:
        return False
    if SHEET2_DIRECTION == "above":
        return rec["diff"] > CROSSING_MARK
    return rec["diff"] < CROSSING_MARK


def _cmp_cell(rec):
    """
    Live price cell for Current Price.
    Tries NSE via GOOGLEFINANCE first. If that errors/returns nothing, falls
    back to BSE via GOOGLEFINANCE. If that also fails, falls back to the
    closing price already scraped from Chittorgarh (or "N/A" if we have none).
    This stops rows from going blank/#N/A just because GOOGLEFINANCE hasn't
    indexed a freshly-listed ticker yet.
    """
    ticker = rec["ticker"]
    fallback_literal = rec["cmp"] if rec["cmp"] != "" else '"N/A"'

    if USE_GOOGLEFINANCE_FORMULA and ticker:
        return (
            f'=IFERROR(GOOGLEFINANCE("NSE:{ticker}","price"),'
            f'IFERROR(GOOGLEFINANCE("BSE:{ticker}","price"),{fallback_literal}))'
        )
    if rec["cmp"] != "":
        return rec["cmp"]
    return "SYMBOL NOT FOUND"


def make_master_row(rec, row_num):
    """8 columns A-H; H = plain Diff (Current Return - Listing Gain)."""
    current_return = f'=IFERROR(((E{row_num}-C{row_num})/C{row_num})*100,"")'   # G
    difference = f'=IFERROR(G{row_num}-F{row_num},"")'                           # H
    return [rec["company"], rec["listing_date"], rec["issue_price"], rec["listing_price"],
            _cmp_cell(rec), rec["listing_gain"], current_return, difference]


def make_good_row(rec, row_num):
    """
    10 columns A-J; H = G-F-CROSSING_MARK (the buy point), I = profit on
    INVESTMENT_PER_IPO, J = Alert Sent flag (starts FALSE; set TRUE once the
    email for this row has actually gone out).
    """
    current_return = f'=IFERROR(((E{row_num}-C{row_num})/C{row_num})*100,"")'   # G
    net_return = f'=IFERROR(G{row_num}-F{row_num}-{CROSSING_MARK},"")'           # H
    profit = f'=IFERROR({INVESTMENT_PER_IPO}*H{row_num}/100,"")'                 # I
    return [rec["company"], rec["listing_date"], rec["issue_price"], rec["listing_price"],
            _cmp_cell(rec), rec["listing_gain"], current_return, net_return, profit,
            "FALSE"]                                                             # J


def set_header(ws, headers, last_col):
    ws.update(values=[headers], range_name=f"A1:{last_col}1",
              value_input_option="USER_ENTERED")


def write_block(ws, start_row, block, last_col):
    if not block:
        return
    end_row = start_row + len(block) - 1
    ws.update(values=block, range_name=f"A{start_row}:{last_col}{end_row}",
              value_input_option="USER_ENTERED")


def names_in(rows):
    return {r[0].strip().lower() for r in rows[1:] if r and r[0].strip()}


# ----------------------------- email -----------------------------------------
def _build_email_body(rows_with_numbers):
    """rows_with_numbers: list of (row_number, row_values) from Good_IPOs."""
    blocks = []
    for _, row in rows_with_numbers:
        name = row[0] if len(row) > 0 else ""
        listing_date = row[1] if len(row) > 1 else ""
        issue_price = row[2] if len(row) > 2 else ""
        listing_day_price = row[3] if len(row) > 3 else ""
        current_price = row[4] if len(row) > 4 else ""
        listing_gain = row[5] if len(row) > 5 else ""
        current_return = row[6] if len(row) > 6 else ""
        net_return = row[7] if len(row) > 7 else ""
        profit = row[8] if len(row) > 8 else ""
        blocks.append(
            f"BUY: {name}  ->  invest Rs {INVESTMENT_PER_IPO}\n"
            f"    Listing Date      : {listing_date}\n"
            f"    Issue Price       : Rs {issue_price}\n"
            f"    Listing Day Price : Rs {listing_day_price}\n"
            f"    Current Price     : Rs {current_price}\n"
            f"    Listing Gain      : {listing_gain}%\n"
            f"    Current Return    : {current_return}%\n"
            f"    Net Return (entry at +{CROSSING_MARK}%) : {net_return}%\n"
            f"    Est. Profit on Rs {INVESTMENT_PER_IPO} : Rs {profit}"
        )
    return (f"These IPO(s) are above the {CROSSING_MARK}% mark above their listing "
            f"price. Suggested action - buy Rs {INVESTMENT_PER_IPO} of each:\n\n"
            + "\n\n".join(blocks) + "\n\n-- IPO Tracker")


def _send_email(subject, body):
    """Returns True on success, False on failure (never raises)."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = SENDER_EMAIL
    msg["To"] = ", ".join(RECIPIENT_EMAILS)
    msg.set_content(body)
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as server:
            server.login(SENDER_EMAIL, SENDER_APP_PASSWORD)
            server.send_message(msg)
        print(f"Email sent to {len(RECIPIENT_EMAILS)} recipient(s).")
        return True
    except Exception as error:
        print(f"WARNING: could not send email -> {error}")
        return False


def send_pending_alerts(ws2):
    """
    Source of truth is the sheet itself, not this run's scrape. Reads every
    row in Good_IPOs, finds the ones whose Alert Sent column is not TRUE
    (brand-new rows, or ones where a previous email attempt failed), and
    emails ALL of them together in one message. Marks each row TRUE on
    success; leaves it FALSE (so it's retried automatically next run) if the
    send fails.
    """
    if not SEND_EMAIL_ALERTS:
        return

    rows = ws2.get_all_values()
    if len(rows) <= 1:
        print("Good_IPOs is empty - nothing to alert on.")
        return

    pending = []
    for row_num, row in enumerate(rows[1:], start=2):
        if not row or not row[0].strip():
            continue
        alert_sent = row[ALERT_SENT_IDX].strip().upper() if len(row) > ALERT_SENT_IDX else ""
        if alert_sent != "TRUE":
            pending.append((row_num, row))

    if not pending:
        print("No pending alerts - everything in Good_IPOs is already marked sent.")
        return

    subject = f"BUY alert: {len(pending)} IPO(s) crossed {CROSSING_MARK}%"
    body = _build_email_body(pending)
    sent_ok = _send_email(subject, body)

    status_value = "TRUE" if sent_ok else "FALSE"
    updates = [{"range": f"{ALERT_SENT_COL}{row_num}", "values": [[status_value]]}
               for row_num, _ in pending]
    ws2.batch_update(updates, value_input_option="USER_ENTERED")

    if sent_ok:
        print(f"Marked {len(pending)} row(s) as Alert Sent = TRUE.")
    else:
        print(f"Left {len(pending)} row(s) as Alert Sent = FALSE - will retry next run.")


def seed_alerts_silently(ws2, row_numbers):
    """First-run seeding: mark rows TRUE without emailing (EMAIL_ON_FIRST_RUN=False)."""
    if not row_numbers:
        return
    updates = [{"range": f"{ALERT_SENT_COL}{row_num}", "values": [["TRUE"]]}
               for row_num in row_numbers]
    ws2.batch_update(updates, value_input_option="USER_ENTERED")
    print(f"First run: seeded {len(row_numbers)} row(s) in Good_IPOs, no email sent.")


# ----------------------------- main ------------------------------------------
def main():
    records = build_records()
    if not records:
        print("No records. Stopping.")
        return

    ss = open_spreadsheet()
    ws1 = get_or_create_ws(ss, SHEET1_NAME)     # IPOs (master)
    ws2 = get_or_create_ws(ss, SHEET2_NAME)     # Good_IPOs
    ws3 = get_or_create_ws(ss, SHEET3_NAME)     # Net Profit/Loss

    if REBUILD:
        print("REBUILD is ON: wiping all three sheets (set False for daily use).")
        ws1.clear(); ws2.clear(); ws3.clear()

    rows1 = ws1.get_all_values()
    rows2 = ws2.get_all_values()
    is_first_run = (len(rows2) <= 1)            # Good_IPOs empty -> first population

    # Always (re)write just the header row (row 1). Your IPO data is always at
    # row 2 and below, so this only sets/repairs the headings and never touches
    # or rebuilds your rows.
    set_header(ws1, MASTER_HEADERS, MASTER_LAST_COL)
    set_header(ws2, GOOD_HEADERS, GOOD_LAST_COL)
    if len(rows1) == 0:
        rows1 = [MASTER_HEADERS]
    if len(rows2) == 0:
        rows2 = [GOOD_HEADERS]

    existing1 = names_in(rows1)                 # already in IPOs (master)
    existing2 = names_in(rows2)                 # already in Good_IPOs

    # ---- IPOs (master): append only brand-new IPOs -----------------------
    next1, block1, r1, added1 = max(2, len(rows1) + 1), [], 0, 0
    r1 = next1
    for rec in records:
        if rec["company"].lower() in existing1:
            continue
        block1.append(make_master_row(rec, r1)); r1 += 1
        existing1.add(rec["company"].lower()); added1 += 1
    write_block(ws1, next1, block1, MASTER_LAST_COL)

    # ---- Good_IPOs: append only IPOs that have NEWLY crossed the mark ----
    next2, block2, r2, new_crossers = max(2, len(rows2) + 1), [], 0, []
    r2 = next2
    for rec in records:
        if not in_sheet2(rec):                  # not above the mark yet
            continue
        if rec["company"].lower() in existing2: # already counted before
            continue
        block2.append(make_good_row(rec, r2)); r2 += 1
        existing2.add(rec["company"].lower())
        new_crossers.append(rec)
    write_block(ws2, next2, block2, GOOD_LAST_COL)

    # ---- Net Profit/Loss: always (re)write the 4 small summary cells -----
    # Reference the ACTUAL Good_IPOs tab name (ws2.title) so a stray space in
    # the tab name can never break the formula with #REF.
    g = ws2.title
    summary = [
        ["Metric", "Value"],
        ["Total Invested (Rs)", f"=COUNTA('{g}'!A2:A)*{INVESTMENT_PER_IPO}"],
        ["Total Profit (Rs)", f"=SUM('{g}'!I2:I)"],
        ["Net Profit / Loss (%)", '=IFERROR(B3/B2*100,"")'],
    ]
    ws3.update(values=summary, range_name="A1:B4", value_input_option="USER_ENTERED")

    print(f"Done. {SHEET1_NAME}: +{added1} new IPO(s). "
          f"{SHEET2_NAME}: +{len(new_crossers)} newly crossed {CROSSING_MARK}%.")

    # ---- Email: driven by the sheet's Alert Sent column, not just this ---
    # ---- run's new_crossers, so nothing ever silently stays unemailed. ---
    if is_first_run and not EMAIL_ON_FIRST_RUN and new_crossers:
        new_row_numbers = list(range(next2, next2 + len(new_crossers)))
        seed_alerts_silently(ws2, new_row_numbers)
    else:
        send_pending_alerts(ws2)


if __name__ == "__main__":
    main()
