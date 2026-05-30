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
}

# ── BondExplorer ──────────────────────────────────────────────────────────────
# Direct API endpoint — no session or token required.
# Discovered from the _mchHr parameter in the Marketo tracking request.
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
    """Download the full BondExplorer CSV from the SIX public API."""
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

    content_type = r.headers.get("Content-Type", "")
    if len(r.content) < 1_000:
        print(f"ERROR: Response too small ({len(r.content)} bytes). "
              f"Content-Type: {content_type}")
        print("Preview:", r.text[:300])
        sys.exit(1)

    out_path = Path(out_dir) / "BondExplorer.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(r.content)

    # Count rows for confirmation
    lines = r.text.count("\n")
    print(f"  Saved -> {out_path} ({len(r.content)//1024:,} KB, ~{lines:,} bonds)")
    return out_path


# ── WertrechteIsinReport ──────────────────────────────────────────────────────
def download_wertrechte(out_dir):
    """
    Two-step download:
    1. GET the registration page to obtain session cookie + one-time token URL
       (the token is embedded in the <form action="..."> attribute)
    2. POST to that token URL with PdfReport param to receive the PDF
    """
    try:
        import requests
    except ImportError:
        print("ERROR: pip install requests"); sys.exit(1)

    base = "https://sws.six-group.com"
    page = f"{base}/registration/WertrechteIsinReport"

    print("  [1/2] Loading WertrechteIsinReport page (getting session token)...")
    session = requests.Session()
    r = session.get(
        page,
        headers={**HEADERS, "Accept": "text/html,application/xhtml+xml,*/*"},
        timeout=30,
    )
    r.raise_for_status()

    # Extract form action URL — contains the one-time token
    m = re.search(r"<form[^>]+action=['\"]([^'\"]+)['\"]", r.text)
    if not m:
        print("ERROR: Could not find form action in page HTML.")
        print("Page snippet:", r.text[:800])
        sys.exit(1)

    action_path = m.group(1)
    action_url  = base + action_path if action_path.startswith("/") else action_path
    print(f"  Token URL: ...{action_url[-40:]}")

    # Extract hidden NR field (session identifier required by the form)
    nr_match = re.search(r"name=['\"]NR['\"][^>]*value=['\"]([^'\"]+)['\"]", r.text)
    nr_value = nr_match.group(1) if nr_match else ""

    time.sleep(1)  # be polite

    print("  [2/2] Submitting PdfReport request...")
    r2 = session.post(
        action_url,
        data={
            "NR":        nr_value,
            "F":         "",
            "FP":        "",
            "M":         "",
            "PdfReport": "Report",
        },
        headers={
            **HEADERS,
            "Accept": "text/html,application/xhtml+xml,application/pdf,*/*",
            "Referer": page,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        timeout=120,
    )
    r2.raise_for_status()

    content_type = r2.headers.get("Content-Type", "")
    if "pdf" not in content_type.lower() and len(r2.content) < 50_000:
        print(f"ERROR: Did not receive a PDF. Content-Type: {content_type}, "
              f"size: {len(r2.content)} bytes")
        print("Response preview:", r2.text[:400])
        sys.exit(1)

    out_path = Path(out_dir) / "WertrechteIsinReport.pdf"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(r2.content)
    print(f"  Saved -> {out_path} ({len(r2.content)//1024:,} KB)")
    return out_path


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Download SIX source files")
    ap.add_argument("--out-dir",       default="data/", help="Output directory")
    ap.add_argument("--wertrechte",    action="store_true", default=True)
    ap.add_argument("--bondexplorer",  action="store_true", default=True)
    ap.add_argument("--no-wertrechte", action="store_true")
    ap.add_argument("--no-bondexplorer", action="store_true")
    args = ap.parse_args()

    Path(args.out_dir).mkdir(parents=True, exist_ok=True)

    if args.wertrechte and not args.no_wertrechte:
        print("Downloading WertrechteIsinReport (PDF)...")
        download_wertrechte(args.out_dir)

    if args.bondexplorer and not args.no_bondexplorer:
        print("Downloading BondExplorer (CSV)...")
        download_bondexplorer(args.out_dir)

    print("All downloads complete.")

if __name__ == "__main__":
    main()
