# =============================================================================
#  IPO TRACKER  -  multi-year NSE/BSE mainboard IPO tracker -> Google Sheets
# =============================================================================
#  Sheet1 = EVERY IPO (master list). Add-only: new IPOs are appended, existing
#           rows are never touched, so your notes and ordering stay put.
#  Sheet2 = only IPOs whose Diff (Current Return - Listing Gain) > threshold.
#           REBUILT every run, so an IPO that crosses the mark weeks AFTER
#           listing shows up here automatically (and one that drops below
#           leaves).
#  Email  = one message the FIRST time an IPO enters Sheet2 (whether on listing
#           day or later). Sheet2's previous contents are the memory, so you are
#           never emailed twice about the same share, and never daily.
#
#  Secrets can be read from environment variables (used by the GitHub Actions
#  cloud run) and otherwise fall back to the values typed below.
# =============================================================================

import os

# -----------------------------------------------------------------------------
#  >>>>>>>>>>>>>>>>>>>>>>  CONFIG  -  EDIT ONLY THIS BLOCK  <<<<<<<<<<<<<<<<<<<<<<
# -----------------------------------------------------------------------------

YEARS = ["2025", "2026"]            # all the years you want, on one line

SHEET_ID = os.environ.get("IPO_SHEET_ID", "PASTE_YOUR_GOOGLE_SHEET_ID_HERE")
SHEET1_NAME = "Sheet1"
SHEET2_NAME = "Sheet2"
CREDENTIALS_PATH = os.environ.get(
    "IPO_CREDENTIALS_PATH", r"C:\Users\YourName\Desktop\IPOTracker\credentials.json")

USE_GOOGLEFINANCE_FORMULA = True
INCLUDE_REITS_INVITS = False

# Sheet2 filter. Diff = Current Return - Listing Gain (column H).
SHEET2_DIRECTION = "above"          # "above": in Sheet2 when Diff > threshold
SHEET2_DIFF_THRESHOLD = 25

# One-time helper: True wipes both sheets and rebuilds. Leave False for daily.
REBUILD = False

# Email alerts (fire when an IPO first enters Sheet2).
SEND_EMAIL_ALERTS = True
EMAIL_ON_FIRST_RUN = False          # stay silent the first time (just seed memory)
SENDER_EMAIL = os.environ.get("IPO_SENDER_EMAIL", "youremail@gmail.com")
SENDER_APP_PASSWORD = os.environ.get("IPO_APP_PASSWORD", "xxxx xxxx xxxx xxxx")
RECIPIENT_EMAILS = os.environ.get(
    "IPO_RECIPIENTS", "person1@example.com,person2@example.com").split(",")

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
COLUMN_HEADERS = [
    "IPO Name", "Listing Date", "Issue Price", "Listing Day Price",
    "Current Price", "Listing Gain", "Current Return", "Diff (Current - Listing)",
]
LAST_COL = "H"


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
    try:
        return spreadsheet.worksheet(name)
    except gspread.exceptions.WorksheetNotFound:
        return spreadsheet.add_worksheet(title=name, rows=2000, cols=12)


def in_sheet2(rec):
    if rec["diff"] is None:
        return False
    if SHEET2_DIRECTION == "above":
        return rec["diff"] > SHEET2_DIFF_THRESHOLD
    return rec["diff"] < SHEET2_DIFF_THRESHOLD


def make_row(rec, row_num):
    if USE_GOOGLEFINANCE_FORMULA and rec["ticker"]:
        cmp_cell = f'=GOOGLEFINANCE("NSE:{rec["ticker"]}","price")'
    elif rec["cmp"] != "":
        cmp_cell = rec["cmp"]
    else:
        cmp_cell = "SYMBOL NOT FOUND"
    current_return = f'=IFERROR(((E{row_num}-C{row_num})/C{row_num})*100,"")'   # G
    difference = f'=IFERROR(G{row_num}-F{row_num},"")'                           # H
    return [rec["company"], rec["listing_date"], rec["issue_price"], rec["listing_price"],
            cmp_cell, rec["listing_gain"], current_return, difference]


def set_header(ws):
    ws.update(values=[COLUMN_HEADERS], range_name=f"A1:{LAST_COL}1",
              value_input_option="USER_ENTERED")


def write_block(ws, start_row, block):
    if not block:
        return
    end_row = start_row + len(block) - 1
    ws.update(values=block, range_name=f"A{start_row}:{LAST_COL}{end_row}",
              value_input_option="USER_ENTERED")


def names_in(rows):
    return {r[0].strip().lower() for r in rows[1:] if r and r[0].strip()}


# ----------------------------- email -----------------------------------------
def send_alert_email(new_crossers):
    if not new_crossers or not SEND_EMAIL_ALERTS:
        return
    lines = [f"- {r['company']}: now {r['current_return']}% from issue, "
             f"{r['diff']}% above its listing price" for r in new_crossers]
    body = (f"These IPO(s) just crossed the {SHEET2_DIFF_THRESHOLD}% mark "
            f"(gain since listing):\n\n" + "\n".join(lines) + "\n\n-- IPO Tracker")
    msg = EmailMessage()
    msg["Subject"] = f"IPO alert: {len(new_crossers)} share(s) crossed {SHEET2_DIFF_THRESHOLD}%"
    msg["From"] = SENDER_EMAIL
    msg["To"] = ", ".join(RECIPIENT_EMAILS)
    msg.set_content(body)
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as server:
            server.login(SENDER_EMAIL, SENDER_APP_PASSWORD)
            server.send_message(msg)
        print(f"Email sent to {len(RECIPIENT_EMAILS)} recipient(s).")
    except Exception as error:
        print(f"WARNING: could not send email -> {error}")


# ----------------------------- main ------------------------------------------
def main():
    records = build_records()
    if not records:
        print("No records. Stopping.")
        return

    ss = open_spreadsheet()
    ws1 = get_or_create_ws(ss, SHEET1_NAME)
    ws2 = get_or_create_ws(ss, SHEET2_NAME)

    if REBUILD:
        print("REBUILD is ON: wiping both sheets (set REBUILD = False for daily use).")
        ws1.clear(); ws2.clear()

    rows1 = ws1.get_all_values()
    prev_sheet2 = names_in(ws2.get_all_values())      # who was in Sheet2 BEFORE this run

    # ---- Sheet1: add-only master list ------------------------------------
    set_header(ws1)
    existing1 = names_in(rows1)
    next1 = max(2, len(rows1) + 1)
    block1, r1, added = [], next1, 0
    for rec in records:
        if rec["company"].lower() in existing1:
            continue
        block1.append(make_row(rec, r1)); r1 += 1
        existing1.add(rec["company"].lower())
        added += 1
    write_block(ws1, next1, block1)

    # ---- Sheet2: full refresh = current IPOs with Diff > threshold --------
    qualifiers = [rec for rec in records if in_sheet2(rec)]
    ws2.clear()
    set_header(ws2)
    block2 = [make_row(rec, i + 2) for i, rec in enumerate(qualifiers)]
    write_block(ws2, 2, block2)

    # ---- Email: IPOs that are in Sheet2 now but were NOT before ----------
    new_crossers = [rec for rec in qualifiers if rec["company"].lower() not in prev_sheet2]
    is_first_run = (len(prev_sheet2) == 0)

    print(f"Done. Sheet1 +{added} new IPO(s) (now {len(existing1)} total). "
          f"Sheet2 has {len(qualifiers)} IPO(s) over {SHEET2_DIFF_THRESHOLD}%. "
          f"{len(new_crossers)} newly crossed.")

    if new_crossers and not (is_first_run and not EMAIL_ON_FIRST_RUN):
        send_alert_email(new_crossers)
    elif new_crossers:
        print("First run: seeding the alert memory, no email sent.")


if __name__ == "__main__":
    main()
