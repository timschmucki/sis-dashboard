#!/usr/bin/env python3
"""
SIX SIS Dashboard Pipeline - run.py
====================================
Classifies all securities from WertrechteIsinReport, diffs against
previous run, and builds the updated dashboard HTML.

Usage:
    python pipeline/run.py \\
        --wertrechte   data/WertrechteIsinReport.pdf \\
        --bondexplorer data/BondExplorer.csv \\
        --pp-training  data/PP_Training.xlsx \\
        --output       index.html \\
        --data-out     data/classified.json \\
        --prev         data/classified_prev.json
"""
import re, json, argparse, sys, datetime
from pathlib import Path
from collections import Counter

# ── Parsing ───────────────────────────────────────────────────────────────────
def parse_wertrechte(path):
    src = Path(path)
    if src.suffix.lower() == ".pdf":
        try: import pdfplumber
        except ImportError:
            print("ERROR: pip install pdfplumber"); sys.exit(1)
        lines = []
        with pdfplumber.open(src) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t: lines.extend(t.split("\n"))
    else:
        with open(src, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    lines = [l.strip() for l in lines if l.strip()]
    isin_re = re.compile(r"^[A-Z]{2}[A-Z0-9]{10}$")
    num_re  = re.compile(r"^[\d.]+$")
    rows = []
    i = 0
    while i < len(lines):
        if isin_re.match(lines[i]):
            isin,name,denom,qty = lines[i],lines[i+1] if i+1<len(lines) else "",lines[i+2] if i+2<len(lines) else "",lines[i+3] if i+3<len(lines) else ""
            if num_re.match(denom) and num_re.match(qty):
                rows.append((isin, name, float(denom), float(qty))); i += 4; continue
        i += 1
    print(f"  Parsed {len(rows):,} rows")
    return rows

# ── Classifier ────────────────────────────────────────────────────────────────
BTP = (r"BD|MB|MMKT|DISCBD|BONDS|OBLIGATION|WRDWN|MTN|OB|NOTES|FRN|EMTN"
       r"|PFANDBRIEF|ANLEIHE|KASSASCH|KASSASCHEIN|KASSENSCHEIN"
       r"|PF|MCS|NT|CVBD|COVERED|COCO|EURO MEDIUM|LPN|MBS")

def coupon(name):
    if "/" not in name: return None
    m = re.match(r"^([\d]+\.?[\d]*)", name.split("/",1)[1].strip())
    try: return float(m.group(1)) if m else None
    except: return None

def is_eidg_snb(name):
    iss = name.split("/",1)[0].strip().upper() if "/" in name else name.upper()
    return any(x in iss for x in ("EIDG","SNB","NATIONALBANK","NAT BK","NATL BK"))

def safe(s): return "".join(c if ord(c)<128 else "?" for c in str(s))

def classify_one(isin, name, denom, qty, be_isins, pp_isins):
    desc = name.split("/",1)[1].strip().upper() if "/" in name else ""
    eff  = qty if qty > 0 else denom
    cc   = isin[:2]
    iss  = name.split("/",1)[0].strip() if "/" in name else name.strip()

    if isin in be_isins: return ("Bond","Publicly Listed Bond",100,eff,iss)
    if isin in pp_isins:
        if is_eidg_snb(name): return None
        return ("Bond","Private Placement Bond",100,eff,iss)
    if isin.startswith("CH") and "/" in name:
        if not("LEONTEQ" in name.upper() or denom<5000 or re.search(r"\bABS\b",desc)):
            it  = bool(re.match(r"^(VAR|\d[\d.,]*)?\s*("+BTP+r")\b",desc))
            idt = bool(re.match(r"^(VAR|\d[\d.,]*)\s+\d{4}-[\d.]+",desc))
            if it or idt:
                c = coupon(name)
                if not(c is not None and c>3.0) and eff>=1_000_000:
                    return ("Bond","Private Placement Bond",95 if it else 90,eff,iss)

    SP_RULES = [
        (r"^(VAR|\d[\d.,]*)?\s*BRC\b","Barrier Reverse Convertible (BRC)",99),
        (r"^(VAR|\d[\d.,]*)?\s*RVCV\b","Reverse Convertible (RVCV)",99),
        (r"REVERSE CONV","Reverse Convertible",99),
        (r"^(VAR|\d[\d.,]*)?\s*(EXPR|CLKDNT|CLKDN)\b","Express / Autocall",99),
        (r"^(VAR|\d[\d.,]*)?\s*(CAPPROT|BARCAPPROT|KAPITALSCHUTZ)\b","Capital Protection",99),
        (r"PROTECTION PARTIC|\bCAPITAL\b|^(VAR|\d[\d.,]*)?\s*PROTECTION\b","Capital Protection",95),
        (r"^(VAR|\d[\d.,]*)?\s*(TRACKER|BNCTF|OUTPCTF|OUTPBNCTF)\b","Tracker / Outperformance",99),
        (r"^(VAR|\d[\d.,]*)?\s*(LEVDCTF|DISCCFT|BARDISCCFT)\b","Discount / Leverage",99),
        (r"^(C|P|L|S)\s+(WT|KOWT|MINIF|SPREADWT|MINIFUT)\b|^(C|P)\s+MI.FUT\b|^P\s+EX.OS\b","Warrant / Mini-Future",99),
        (r"^(VAR|\d[\d.,]*)?\s*P\.A\.\s|^(WARRANT|MINI\s*FUTURE|MINI\b|LONG\s+MINI|WT\b)\b","Warrant / Mini-Future",99),
        (r"^(VAR|\d[\d.,]*)?\s*(FAKTOR|SHORT\b)\b|^(LEVERAGED|UBS\s+LEVERAGED)","Faktor / Leverage",99),
        (r"^(VAR|\d[\d.,]*)?\s*(STRUCT\b|STRUCTURED\b)","Structured Product (generic)",95),
        (r"OPEN END|MONEY MARKET|^(OPEN END\s+)?PERLES\b","Structured Product (Open-End)",95),
        (r"^(CERTIFICAT\b|CERTIFICATES\b|CERTIFICATE\b|CERT\.\b|UBP\b)|CERT\.\s+\d{4}","Structured Certificate",95),
        (r"^ZKB\s+(TRACKER|CAPITAL)|^UBS\s+ETC\b","SP (ZKB/UBS branded)",95),
        (r"^(MSCI|CONSTANT|ACTIVELY|DYNAMIC|STRATEGY|EXCHANGE)\b","Index-Linked / Strategy",90),
        (r"^(TWIN\b|REVERSE\b|ZERO COUPON\b|ZERO\b)|^(VAR|\d[\d.,]*)?\s*CD\b","Structured Product (other)",90),
        (r"ASSET BACKED","Asset-Backed Security",85),
    ]
    for pat,sub,cf in SP_RULES:
        if re.search(pat,desc): return ("Structured Product",sub,cf,eff,iss)
    if cc in ("DE","GB","US","NL","JE"): return ("Structured Product",f"SP (foreign {cc})",95,eff,iss)

    CIS_RULES = [
        (r"^(UT\s+CL|UNITS|ANTEILE\b|ANTEIL\b)|ANTEILE\b|UT CL","Fund Unit",95),
        (r"^UT\s+[A-Z]{3}\b","Fund Unit (UT ccy)",92),
        (r"^ANSPRUECHE\b","Fund Claims",90),
        (r"^(ETC\b|ETN\b|ETT\b|UBS-ETT\b)","ETC / ETN / ETT",90),
        (r"^(SHS\b|M/UT\b|S/UT\b|WKBF|WKB[A-Z]*/|SWEQ/|KOMMANDITANTEILE|PARTS\b)","Fund Share Class",90),
    ]
    for pat,sub,cf in CIS_RULES:
        if re.search(pat,desc): return ("Collective Investment Scheme",sub,cf,eff,iss)
    if cc=="LI": return ("Collective Investment Scheme","Liechtenstein Fund",90,eff,iss)

    if re.match(r"^(REGSH|REGPS|NA\b|NCU\b|SH\b|NAMEN|STIMMRECHTS|VORZ|BRSH|BRPS|ACT\b|ACT\.NOM|ACT NOM|PTG\.PREF\.SHS|N/IA\b|REGISTERED\s+SHS|CU\b)",desc):
        return ("Equity / Share","Registered Share",97,eff,iss)
    if cc in ("CY","IL","PT","VC"): return ("Equity / Share","Foreign Equity",85,eff,iss)
    if re.match(r"^RTS\b",desc): return ("Subscription Right","Rights Issue",95,eff,iss)

    SP_ISS = re.compile(r"LEONTEQ|VONTOBEL|EFG INTL|BNP PARIBAS|UBS LONDON|UBS JERSEY|BARCLAYS|GOLDMAN|JULIUS BAE|RAIFFEISEN SCHW|ZUERCHER KB|SG ISSUER|21SHARES|VALOUR|BITCOIN|MAVERIX|HASHDEX")
    if SP_ISS.search(iss.upper()): return ("Structured Product","SP (issuer-based)",85,eff,iss)
    return ("Unclassified","Unclassified",0,eff,iss)

# ── Build HTML ────────────────────────────────────────────────────────────────
def fmtN(n):
    if n>=1e9: return f"{n/1e9:.1f}bn"
    if n>=1e6: return f"{n/1e6:.0f}M"
    if n>=1e3: return f"{n/1e3:.0f}K"
    return str(int(n))

def build_chunks(data, var):
    chunks = [data[i:i+100] for i in range(0,len(data),100)]
    lines  = "\n".join(f"var {var}{i}={json.dumps(c,separators=(',',':'),ensure_ascii=True)};" for i,c in enumerate(chunks))
    assem  = f"var {var}=[].concat("+",".join(f"{var}{i}" for i in range(len(chunks)))+");"
    return lines+"\n"+assem

def build_html(data, out_path, template_path):
    secs    = data["securities"]
    diff    = data.get("diff",{})
    rdate   = data.get("report_date","")
    counts  = data.get("counts",{})
    n_total = data.get("total",0)

    def bycat(cat): return [s for s in secs if s["cat"]==cat]
    bonds    = bycat("Bond")
    listed   = [b for b in bonds if b["sub"]=="Publicly Listed Bond"]
    pp       = [b for b in bonds if b["sub"]=="Private Placement Bond"]
    sps      = sorted(bycat("Structured Product"),key=lambda x:-x["q"])[:2000]
    cis      = sorted(bycat("Collective Investment Scheme"),key=lambda x:-x["q"])[:1000]
    equities = bycat("Equity / Share")
    rights   = bycat("Subscription Right")

    li_qty = sum(b["q"] for b in listed)
    pp_qty = sum(b["q"] for b in pp)
    tot_qty= li_qty+pp_qty

    sp_iss   = Counter(s["iss"] for s in sps).most_common(8)
    sp_sub   = Counter(s["sub"] for s in bycat("Structured Product")).most_common(8)
    cis_iss  = Counter(s["iss"] for s in cis).most_common(8)
    cis_sub  = Counter(s["sub"] for s in cis).most_common(6)
    pp_iss   = Counter(s["iss"] for s in pp if s["conf"]==100).most_common(10)
    charts   = {"spIss":[{"l":k[:30],"c":v} for k,v in sp_iss],
                "spSubs":[{"l":k[:28],"c":v} for k,v in sp_sub],
                "cisIss":[{"l":k[:30],"c":v} for k,v in cis_iss],
                "cisSubs":[{"l":k[:28],"c":v} for k,v in cis_sub],
                "ppIss":[{"n":k[:35],"c":v} for k,v in pp_iss]}

    n_added   = len(diff.get("added",[]))
    n_removed = len(diff.get("removed",[]))
    prev_date = diff.get("prev_date","")
    has_diff  = bool(n_added or n_removed)

    all_js = "\n".join([
        build_chunks(bonds,"BD"), build_chunks(sps,"SP"),
        build_chunks(cis,"CI"),   build_chunks(equities,"EQ"),
        build_chunks(rights,"RI"),
        "var DADD="+json.dumps(diff.get("added",[])[:200],separators=(",",":"),ensure_ascii=True)+";",
        "var DREM="+json.dumps(diff.get("removed",[])[:200],separators=(",",":"),ensure_ascii=True)+";",
        "var CHARTS="+json.dumps(charts,separators=(",",":"),ensure_ascii=True)+";",
        f"var REPORT_DATE=\"{rdate}\";",
        f"var PREV_DATE=\"{prev_date}\";",
        f"var HAS_DIFF={'true' if has_diff else 'false'};",
        f"var N_ADDED={n_added};",
        f"var N_REMOVED={n_removed};",
    ])
    assert all(ord(c)<=127 for c in all_js)

    # Build changes page HTML
    ch_pg_js = ""
    changes_tab_html = ""
    if has_diff:
        changes_tab_html = f'<div class="tab" onclick="go('ch',this)">Changes (+{n_added}/-{n_removed})</div>'
        ch_pg_js = f"""
var diffStates={{add:{{pg:0}},rem:{{pg:0}}}};
function rendDiff(id){{
  var src=id==="add"?DADD:DREM;
  var q=(document.getElementById("srch-"+id)||{{}}).value||"";q=q.toLowerCase();
  var fil=src.filter(function(r){{return r.i.toLowerCase().indexOf(q)>=0||r.n.toLowerCase().indexOf(q)>=0;}});
  var tot=fil.length,pg=diffStates[id].pg,mp=Math.max(0,Math.ceil(tot/25)-1);
  if(pg>mp)pg=diffStates[id].pg=0;
  var sl=fil.slice(pg*25,pg*25+25),rows="";
  var col=id==="add"?"#1a6b3a":"#9a1f1f";
  sl.forEach(function(r){{rows+="<tr><td><span class=\'mn\'>"+r.i+"</span></td><td style=\'font-size:11px;max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap\'>"+r.n+"</td><td><span style=\'font-size:10px;color:"+col+"\'>"+r.cat+"</span></td><td style=\'text-align:center\'><span class=\'cb c90\'>"+r.conf+"%</span></td></tr>";}});
  var tb=document.getElementById("tbody-"+id);if(tb)tb.innerHTML=rows;
  var pi=document.getElementById("pinfo-"+id);if(pi)pi.textContent=(pg*25+1)+"-"+Math.min(pg*25+25,tot)+" of "+tot.toLocaleString();
  var fc=document.getElementById("fcnt-"+id);if(fc)fc.textContent=tot!==src.length?tot.toLocaleString()+" of "+src.length.toLocaleString():"";
  var bp=document.getElementById("bprev-"+id);if(bp)bp.disabled=pg===0;
  var bn=document.getElementById("bnext-"+id);if(bn)bn.disabled=pg>=mp;
}}
function chpgDiff(id,d){{diffStates[id].pg+=d;rendDiff(id);}}
if(HAS_DIFF){{rendDiff("add");rendDiff("rem");}}
"""

    with open(template_path) as f: tmpl = f.read()
    html = (tmpl
        .replace("%%REPORT_DATE%%", rdate)
        .replace("%%N_TOTAL%%", f"{n_total:,}")
        .replace("%%ALL_DATA_JS%%", all_js)
        .replace("%%CHANGES_PAGE_JS%%", ch_pg_js)
        .replace("%%CHANGES_TAB%%", changes_tab_html)
    )

    # Inject the changes page HTML div if needed
    if has_diff:
        n_b_add = sum(1 for s in diff.get("added",[]) if s.get("cat")=="Bond")
        n_b_rem = sum(1 for s in diff.get("removed",[]) if s.get("cat")=="Bond")
        ch_div = f'''<div class="pg" id="pg-ch">
  <div class="r4">
    <div class="met"><div class="mlb">Report date</div><div class="mv">{rdate}</div><div class="ms">vs {prev_date}</div></div>
    <div class="met"><div class="mlb">New securities</div><div class="mv" style="color:#1a6b3a">+{n_added:,}</div><div class="ms">added since {prev_date}</div></div>
    <div class="met"><div class="mlb">Removed</div><div class="mv" style="color:#9a1f1f">-{n_removed:,}</div><div class="ms">no longer in register</div></div>
    <div class="met"><div class="mlb">Net change</div><div class="mv">{n_added-n_removed:+,}</div><div class="ms">net new securities</div></div>
  </div>
  <div class="r2">
    <div class="card"><div class="ct">New securities (+{n_added:,})</div>
      <div class="srch"><input class="sinp" id="srch-add" type="text" placeholder="Search..." oninput="rendDiff('add')">
        <span id="fcnt-add" style="font-size:10.5px;color:#9a9a94;margin-left:auto"></span></div>
      <div style="overflow-x:auto"><table class="tbl"><thead><tr><th>ISIN</th><th>Name</th><th>Category</th><th style="text-align:center">Conf.</th></tr></thead>
        <tbody id="tbody-add"></tbody></table></div>
      <div class="pgr"><div class="pi" id="pinfo-add"></div>
        <div class="pb"><button class="pbtn" id="bprev-add" onclick="chpgDiff('add',-1)">Prev</button>
          <button class="pbtn" id="bnext-add" onclick="chpgDiff('add',1)">Next</button></div></div>
    </div>
    <div class="card"><div class="ct">Removed securities (-{n_removed:,})</div>
      <div class="srch"><input class="sinp" id="srch-rem" type="text" placeholder="Search..." oninput="rendDiff('rem')">
        <span id="fcnt-rem" style="font-size:10.5px;color:#9a9a94;margin-left:auto"></span></div>
      <div style="overflow-x:auto"><table class="tbl"><thead><tr><th>ISIN</th><th>Name</th><th>Category</th><th style="text-align:center">Conf.</th></tr></thead>
        <tbody id="tbody-rem"></tbody></table></div>
      <div class="pgr"><div class="pi" id="pinfo-rem"></div>
        <div class="pb"><button class="pbtn" id="bprev-rem" onclick="chpgDiff('rem',-1)">Prev</button>
          <button class="pbtn" id="bnext-rem" onclick="chpgDiff('rem',1)">Next</button></div></div>
    </div>
  </div>
</div>'''
        # Insert changes page before the Classifier Rules page
        html = html.replace('<div class="pg" id="pg-ru">', ch_div+'<div class="pg" id="pg-ru">')

    assert all(ord(c)<=127 for c in html), "Non-ASCII in HTML!"
    Path(out_path).parent.mkdir(parents=True,exist_ok=True)
    with open(out_path,"w",encoding="ascii") as f: f.write(html)
    print(f"  Dashboard: {Path(out_path).stat().st_size//1024} KB")

# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="SIX SIS Dashboard Pipeline")
    ap.add_argument("--wertrechte",   required=True, help="WertrechteIsinReport PDF or TXT")
    ap.add_argument("--bondexplorer", required=True, help="BondExplorer CSV export")
    ap.add_argument("--pp-training",  required=True, help="PP Training dataset XLSX")
    ap.add_argument("--output",       default="index.html",            help="Output HTML file")
    ap.add_argument("--data-out",     default="data/classified.json",  help="Output JSON data")
    ap.add_argument("--prev",         default="data/classified_prev.json", help="Previous JSON for diff")
    ap.add_argument("--template",     default="pipeline/_template.html",   help="HTML template")
    args = ap.parse_args()

    import pandas as pd
    print("Loading reference data...")
    be = pd.read_csv(args.bondexplorer, encoding="latin-1", sep=";")
    be["ISIN"] = be["ISIN"].str.strip()
    be_isins = set(be["ISIN"].dropna())
    pp = pd.read_excel(args.pp_training)
    pp["ISIN"] = pp["ISIN"].str.strip()
    pp_isins = set(pp["ISIN"].dropna())

    print("Parsing WertrechteIsinReport...")
    rows = parse_wertrechte(args.wertrechte)

    print("Classifying all securities...")
    results = []
    for isin,name,denom,qty in rows:
        r = classify_one(isin,name,denom,qty,be_isins,pp_isins)
        if r is None: continue
        cat,sub,cf,eff,iss = r
        results.append({"i":isin,"n":safe(name[:55]),"iss":safe(iss[:35]),
                        "d":int(denom),"q":int(eff),"cat":cat,"sub":sub,"conf":cf})

    counts = Counter(r["cat"] for r in results)
    print("Results:")
    for cat,cnt in counts.most_common(): print(f"  {cat:<42s} {cnt:>8,}")

    diff = {"added":[],"removed":[],"prev_date":""}
    if Path(args.prev).exists():
        with open(args.prev) as f: prev = json.load(f)
        prev_isins = {r["i"] for r in prev.get("securities",[])}
        curr_isins = {r["i"] for r in results}
        cd = {r["i"]:r for r in results}
        pd_ = {r["i"]:r for r in prev.get("securities",[])}
        diff["added"]     = [cd[i] for i in curr_isins-prev_isins]
        diff["removed"]   = [pd_[i] for i in prev_isins-curr_isins]
        diff["prev_date"] = prev.get("report_date","unknown")
        print(f"  Diff: +{len(diff['added']):,} added, -{len(diff['removed']):,} removed vs {diff['prev_date']}")

    today = datetime.date.today().isoformat()
    output = {"report_date":today,"total":len(results),"counts":dict(counts),
              "securities":results,"diff":diff}

    Path(args.data_out).parent.mkdir(parents=True,exist_ok=True)
    with open(args.data_out,"w") as f:
        json.dump(output, f, separators=(",",":"), ensure_ascii=True)
    print(f"Saved -> {args.data_out} ({Path(args.data_out).stat().st_size//1024} KB)")

    print("Building dashboard HTML...")
    build_html(output, args.output, args.template)
    print("Done!")

if __name__ == "__main__":
    main()
