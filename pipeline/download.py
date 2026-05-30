#!/usr/bin/env python3
"""
download.py  --  Download WertrechteIsinReport (PDF) and BondExplorer (CSV) from SIX.
Usage: python pipeline/download.py --out-dir data/
"""
import argparse, re, sys, time
from pathlib import Path

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/148.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

BOND_EXPLORER_URL = (
    "https://www.six-group.com/fqs/ref.csv"
    "?select=ShortName,ISIN,ClosingPrice,CouponRate,IssuerNameShort,"
    "ValorSymbol,ValorNumber,YieldToWorst,DurationToWorst,"
    "SubscriptionPaymentDueDate,RemainingTermOfMaturity,MaturityDate,"
    "AmountInIssue,ProductLineDesc,TradingBaseCurrency,ClosingPerformance,"
    "ClosingDelta,BidVolume,BidPrice,AskPrice,AskVolume,MidSpread,"
    "PreviousClosingPrice,LatestTradeDate,LatestTradeTime,OpeningPrice,"
    "DailyHighPrice,DailyLowPrice,OnMarketVolume,OffBookVolume,"
    "TotalTurnover,TotalTurnoverCHF,GeographicalAreaDesc,IndustrySectorDesc,"
    "SecTypeDesc,BondListedFlag,BondDutyToReportFlag,SpecialFlagDesc"
    "&where=PortalSegment=BO"
    "&orderby=ShortName"
    "&page=1"
    "&pagesize=99999"
)


def download_bondexplorer(out_dir):
    try:
        import requests
    except ImportError:
        print("ERROR: pip install requests"); sys.exit(1)

    print("  Downloading BondExplorer CSV...")
    r = requests.get(
        BOND_EXPLORER_URL,
        headers={
            **HEADERS,
            "Accept": "text/csv,*/*",
            "Referer": "https://www.six-group.com/en/market-data/bonds/bond-explorer.html",
        },
        timeout=60,
    )
    r.raise_for_status()

    if len(r.content) < 1_000:
        print(f"ERROR: Response too small ({len(r.content)} bytes)")
        print("Preview:", r.text[:300])
        sys.exit(1)

    out_path = Path(out_dir) / "BondExplorer.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(r.content)
    lines = r.text.count("\n")
    print(f"  Saved -> {out_path} ({len(r.content)//1024:,} KB, ~{lines:,} bonds)")
    return out_path


def download_wertrechte(out_dir):
    """
    Three-step download mirroring exactly what the browser does:
    1. GET the landing page -> establishes session, get JSESSIONID
    2. Parse the form action (one-time token) and all hidden fields
    3. POST with the correct JSESSIONID cookie + hidden fields -> PDF
    """
    try:
        import requests
    except ImportError:
        print("ERROR: pip install requests"); sys.exit(1)

    base        = "https://sws.six-group.com"
    landing_url = f"{base}/registration/WertrechteIsinReport"

    # Step 1: establish session and get cookies
    print("  [1/3] Establishing session...")
    session = requests.Session()
    r0 = session.get(
        landing_url,
        headers={**HEADERS, "Accept": "text/html,application/xhtml+xml,*/*;q=0.9"},
        timeout=30,
        allow_redirects=True,
    )
    r0.raise_for_status()

    # Log all cookies with their domains
    for c in session.cookies:
        print(f"        Cookie: {c.name} domain={c.domain}")

    # Pick the JSESSIONID from sws.six-group.com specifically.
    # Two JSESSIONID cookies arrive (one per domain); the server needs only its own.
    jsessionid = None
    for c in session.cookies:
        if c.name == "JSESSIONID" and "sws" in (c.domain or ""):
            jsessionid = c.value
            print(f"        Selected JSESSIONID from {c.domain}")
            break
    if jsessionid is None:
        # Fallback: use the last JSESSIONID found
        for c in session.cookies:
            if c.name == "JSESSIONID":
                jsessionid = c.value
        print("        Selected JSESSIONID (fallback)")

    if not jsessionid:
        print("ERROR: No JSESSIONID cookie received. The server may be blocking the request.")
        sys.exit(1)

    # Step 2: parse the page HTML
    html = r0.text
    if "action=" not in html:
        print("  [2/3] Loading query page...")
        r1 = session.get(
            f"{base}/registration/registration.thtm",
            headers={**HEADERS, "Accept": "text/html,*/*", "Referer": landing_url},
            timeout=30,
        )
        r1.raise_for_status()
        html = r1.text
    else:
        print("  [2/3] Form found on landing page")

    action_match = re.search(r'<form[^>]+action=[\'"]([^\'"]+)[\'"]', html)
    if not action_match:
        print("ERROR: Could not find <form action=...> in HTML")
        print("HTML snippet:", html[:800])
        sys.exit(1)

    action_path = action_match.group(1)
    action_url  = base + action_path if action_path.startswith("/") else action_path
    print(f"  Token URL: ...{action_url[-50:]}")

    # Extract all hidden fields
    hidden = {}
    for m in re.finditer(
        r'<input[^>]+type=[\'"]hidden[\'"][^>]*name=[\'"]([^\'"]+)[\'"][^>]*value=[\'"]([^\'"]*)[\'"]',
        html, re.IGNORECASE
    ):
        hidden[m.group(1)] = m.group(2)
    for m in re.finditer(
        r'<input[^>]+type=[\'"]hidden[\'"][^>]*value=[\'"]([^\'"]*)[\'"][^>]*name=[\'"]([^\'"]+)[\'"]',
        html, re.IGNORECASE
    ):
        if m.group(2) not in hidden:
            hidden[m.group(2)] = m.group(1)
    print(f"  Hidden fields: {list(hidden.keys())}")

    time.sleep(2)

    # Step 3: POST using ONLY the sws.six-group.com JSESSIONID cookie
    print("  [3/3] Submitting PdfReport...")
    post_data = {**hidden, "PdfReport": "Report"}

    r2 = requests.post(
        action_url,
        data=post_data,
        headers={
            **HEADERS,
            "Accept": "text/html,application/xhtml+xml,application/pdf,*/*;q=0.8",
            "Referer": landing_url,
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": base,
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-User": "?1",
            "Sec-Fetch-Dest": "document",
            "Upgrade-Insecure-Requests": "1",
            # Send ONLY the sws JSESSIONID — not both cookies
            "Cookie": f"JSESSIONID={jsessionid}",
        },
        timeout=120,
        allow_redirects=True,
    )
    r2.raise_for_status()

    ct      = r2.headers.get("Content-Type", "")
    size_kb = len(r2.content) // 1024
    print(f"  Response: {r2.status_code}, Content-Type: {ct}, size: {size_kb} KB")

    if "pdf" not in ct.lower() and not r2.content.startswith(b"%PDF"):
        print("ERROR: Did not receive a PDF.")
        print(f"Content-Type: {ct}, size: {len(r2.content)} bytes")
        print("Response preview:", r2.text[:600])
        sys.exit(1)

    out_path = Path(out_dir) / "WertrechteIsinReport.pdf"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(r2.content)
    print(f"  Saved -> {out_path} ({size_kb:,} KB)")
    return out_path


def main():
    ap = argparse.ArgumentParser(description="Download SIX source files")
    ap.add_argument("--out-dir",         default="data/")
    ap.add_argument("--no-wertrechte",   action="store_true")
    ap.add_argument("--no-bondexplorer", action="store_true")
    args = ap.parse_args()

    Path(args.out_dir).mkdir(parents=True, exist_ok=True)

    if not args.no_wertrechte:
        print("Downloading WertrechteIsinReport (PDF)...")
        download_wertrechte(args.out_dir)

    if not args.no_bondexplorer:
        print("Downloading BondExplorer (CSV)...")
        download_bondexplorer(args.out_dir)

    print("All downloads complete.")

if __name__ == "__main__":
    main()
