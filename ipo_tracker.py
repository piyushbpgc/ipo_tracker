# =============================================================================
#  IPO TRACKER  -  multi-year NSE/BSE mainboard IPO tracker -> Google Sheets
# =============================================================================
#  Sheet1 = EVERY IPO (master list). Add-only: new IPOs are appended, existing
#           rows are never touched, so your notes and ordering stay put.
#  Sheet2 = only IPOs whose Diff (Current Return - Listing Gain) > threshold.
#
#  Email  = ONE separate email PER IPO (not batched).
#           - REBUILD = True  -> Good_IPOs is wiped and rebuilt from scratch,
#             so every IPO that ends up in Good_IPOs is "new" this run and
#             gets its own email. Use this when you want a full refresh +
#             alert for everything currently above the mark.
#           - REBUILD = False -> only IPOs that newly cross the mark THIS
#             run (i.e. weren't already in Good_IPOs) get emailed. Already-
#             alerted IPOs are never re-emailed.
#           Each Good_IPOs row also gets an "Alert Sent" column (TRUE/FALSE)
#           so you can see at a glance what actually went out; if sending
#           fails for a given IPO it stays FALSE and will be retried the
#           next time that row would otherwise qualify.
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
SHEET2_NAME = "Good_IPOs"         # only IPOs more than 30% above listing
SHEET3_NAME = "Net Profit/Loss"   # the summary / rollup tab
INVESTMENT_PER_IPO = 5000     # rupees invested per Good IPO (used in profit calc)
CREDENTIALS_PATH = os.environ.get(
    "IPO_CREDENTIALS_PATH", r"C:\Users\YourName\Desktop\IPOTracker\credentials.json")

USE_GOOGLEFINANCE_FORMULA = True
INCLUDE_REITS_INVITS = False

# THE ONE NUMBER THAT CONTROLS EVERYTHING:
# - an IPO moves to Good_IPOs when its gain-since-listing is above this %, and
# - it is also the assumed buy point, so Net Return = Current - Listing - this %.
CROSSING_MARK = 30
SHEET2_DIRECTION = "above"          # "above": in Good_IPOs when Diff > CROSSING_MARK

# True  = wipe + rebuild Good_IPOs from scratch, email for EVERY IPO in it.
# False = normal daily run, email only for IPOs that newly cross the mark.
REBUILD = True

# Email alerts.
SEND_EMAIL_ALERTS = True
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
    """Current Price cell - plain GOOGLEFINANCE formula, untouched."""
    if USE_GOOGLEFINANCE_FORMULA and rec["ticker"]:
        return f'=GOOGLEFINANCE("NSE:{rec["ticker"]}","price")'
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
    INVESTMENT_PER_IPO, J = Alert Sent flag (set once the per-IPO email for
    this row is actually sent).
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
def _build_email_body(rec):
    cr, lg = rec["current_return"], rec["listing_gain"]
    if isinstance(cr, (int, float)) and isinstance(lg, (int, float)):
        net_return = round(cr - lg - CROSSING_MARK, 2)
        profit = round(INVESTMENT_PER_IPO * net_return / 100, 2)
    else:
        net_return = profit = "N/A"
    return (
        f"BUY: {rec['company']}  ->  invest Rs {INVESTMENT_PER_IPO}\n"
        f"    Listing Date      : {rec['listing_date']}\n"
        f"    Issue Price       : Rs {rec['issue_price']}\n"
        f"    Listing Day Price : Rs {rec['listing_price']}\n"
        f"    Current Price     : Rs {rec['cmp']}\n"
        f"    Listing Gain      : {rec['listing_gain']}%\n"
        f"    Current Return    : {rec['current_return']}%\n"
        f"    Net Return (entry at +{CROSSING_MARK}%) : {net_return}%\n"
        f"    Est. Profit on Rs {INVESTMENT_PER_IPO} : Rs {profit}\n\n"
        f"-- IPO Tracker"
    )


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
        return True
    except Exception as error:
        print(f"WARNING: could not send email for this IPO -> {error}")
        return False


def send_alert_emails(ws2, crossers_with_rows):
    """
    Sends ONE separate email per IPO in crossers_with_rows = [(rec, row_num), ...].
    Marks that row's Alert Sent column TRUE on success, FALSE on failure.
    """
    if not SEND_EMAIL_ALERTS or not crossers_with_rows:
        return

    updates = []
    sent_count = 0
    for rec, row_num in crossers_with_rows:
        subject = f"BUY alert: {rec['company']} crossed {CROSSING_MARK}%"
        body = _build_email_body(rec)
        ok = _send_email(subject, body)
        updates.append({"range": f"{ALERT_SENT_COL}{row_num}", "values": [["TRUE" if ok else "FALSE"]]})
        if ok:
            sent_count += 1
            print(f"Email sent: {rec['company']}")

    if updates:
        ws2.batch_update(updates, value_input_option="USER_ENTERED")
    print(f"Alert emails: {sent_count}/{len(crossers_with_rows)} sent successfully.")


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
        print("REBUILD is ON: wiping all three sheets. "
              "Every IPO that ends up in Good_IPOs will get its own email.")
        ws1.clear(); ws2.clear(); ws3.clear()

    rows1 = ws1.get_all_values()
    rows2 = ws2.get_all_values()

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

    # ---- Good_IPOs: append IPOs that have (newly) crossed the mark -------
    # When REBUILD=True, existing2 is empty (sheet was wiped), so every IPO
    # above the mark lands here and gets emailed. When REBUILD=False,
    # existing2 still holds previously-alerted IPOs, so only genuinely new
    # crossers show up here and get emailed.
    next2, block2, r2 = max(2, len(rows2) + 1), [], 0
    crossers_with_rows = []
    r2 = next2
    for rec in records:
        if not in_sheet2(rec):                  # not above the mark
            continue
        if rec["company"].lower() in existing2: # already in Good_IPOs
            continue
        block2.append(make_good_row(rec, r2))
        crossers_with_rows.append((rec, r2))
        r2 += 1
        existing2.add(rec["company"].lower())
    write_block(ws2, next2, block2, GOOD_LAST_COL)

    # ---- Net Profit/Loss: always (re)write the 4 small summary cells -----
    g = ws2.title
    summary = [
        ["Metric", "Value"],
        ["Total Invested (Rs)", f"=COUNTA('{g}'!A2:A)*{INVESTMENT_PER_IPO}"],
        ["Total Profit (Rs)", f"=SUM('{g}'!I2:I)"],
        ["Net Profit / Loss (%)", '=IFERROR(B3/B2*100,"")'],
    ]
    ws3.update(values=summary, range_name="A1:B4", value_input_option="USER_ENTERED")

    print(f"Done. {SHEET1_NAME}: +{added1} new IPO(s). "
          f"{SHEET2_NAME}: +{len(crossers_with_rows)} IPO(s) to alert on.")

    # ---- Email: one per IPO -----------------------------------------------
    send_alert_emails(ws2, crossers_with_rows)


if __name__ == "__main__":
    main()
