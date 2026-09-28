"""Extract official tourism series for Jamaica -> data/JM.json

Sources (downloaded to data/raw/JM/):
  * Bank of Jamaica (BOJ) statistics, Excel tables (direct, stable URLs; files overwritten on each update)
      - ES.BOP.00.xls  Balance of Payments Summary (BPM6), quarterly; sheet 'Services':
                        'Travel' credit row / 'Visitor Expenditure (US$MN)' (source: Jamaica Tourist Board)
      - ES.FDI.00.xls  FDI Inflows by Sector, annual; row 'TOURISM'
  * Planning Institute of Jamaica (PIOJ), free "Economic and Social Survey Jamaica - (Selected Indicators &) Overview"
      PDFs, one per edition -> pioj_essj_<year>_overview.pdf (product pages in ESSJ_PRODUCTS)
Download (automatic on every run; see download_all):
  - BOJ .xls files are re-downloaded on every run (the file at the fixed URL is overwritten with each update):
      https://boj.org.jm/wp-content/uploads/2020/09/ES.BOP.00.xls
      https://boj.org.jm/wp-content/uploads/2020/09/ES.FDI.00.xls
  - ESSJ PDFs: GET https://www.pioj.gov.jm/product/<slug>/ and POST its free-download (somdn) form with the session
    cookies. Past editions are immutable -> cached in data/raw/JM once downloaded. Newer editions (year > last known,
    up to last calendar year) are probed on the known slug patterns; HTTP 404 = not published yet.
  Any download failure aborts the run with exit != 0 and data/JM.json is left untouched.
Run: .venv/bin/python data/scripts/JM_extract.py [--no-download]   (--no-download: use files already in data/raw/JM)
"""
import argparse
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "raw" / "JM"
RETRIEVED = date.today().isoformat()  # overwritten in main() with the real download date (or file date with --no-download)
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
BOJ_FILES = {
    "ES.BOP.00.xls": "https://boj.org.jm/wp-content/uploads/2020/09/ES.BOP.00.xls",
    "ES.FDI.00.xls": "https://boj.org.jm/wp-content/uploads/2020/09/ES.FDI.00.xls",
}
PIOJ_PRODUCT = "https://www.pioj.gov.jm/product/{}/"
XLS_MAGIC, PDF_MAGIC = b"\xd0\xcf\x11\xe0", b"%PDF"


class DownloadError(RuntimeError):
    pass


def _get(sess, method, url, tries=3, **kw):
    last = None
    for i in range(tries):
        try:
            r = sess.request(method, url, timeout=180, **kw)
            if r.status_code == 404 or (r.status_code == 200):
                return r
            last = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            last = f"{type(e).__name__}: {e}"
        if i < tries - 1:
            time.sleep(10 * (i + 1))
    raise DownloadError(f"{method} {url} failed after {tries} tries ({last})")


def _save(content, dest, magic, url):
    if not content.startswith(magic) or len(content) < 5000:
        raise DownloadError(f"{url}: unexpected content ({len(content)} bytes, starts {content[:8]!r})")
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(content)
    tmp.replace(dest)
    print(f"downloaded {dest.name} ({len(content):,} bytes) <- {url}")


def download_boj(sess):
    for name, url in BOJ_FILES.items():
        r = _get(sess, "GET", url)
        if r.status_code != 200:
            raise DownloadError(f"{url}: HTTP {r.status_code}")
        _save(r.content, RAW / name, XLS_MAGIC, url)


def download_essj(sess, year, slug, required=True):
    """Download the free ESSJ overview PDF of one edition. Returns False if the product page does not exist (404)
    and required=False; raises DownloadError on any other problem."""
    url = PIOJ_PRODUCT.format(slug)
    r = _get(sess, "GET", url)
    if r.status_code == 404:
        if required:
            raise DownloadError(f"{url}: HTTP 404")
        return False
    form = re.search(r'<form[^>]*class="somdn-download-form"[^>]*>(.*?)</form>', r.text, re.S)
    if not form:
        raise DownloadError(f"{url}: free-download form (somdn) not found")
    fields = dict(re.findall(r'<input[^>]*name="([^"]+)"[^>]*value="([^"]*)"', form.group(1)))
    if fields.get("action") != "somdn_download_single" or not fields.get("somdn_download_key"):
        raise DownloadError(f"{url}: unexpected download form fields {sorted(fields)}")
    r = _get(sess, "POST", url, data=fields)
    if r.status_code != 200:
        raise DownloadError(f"POST {url}: HTTP {r.status_code}")
    _save(r.content, RAW / f"pioj_essj_{year}_overview.pdf", PDF_MAGIC, url)
    return True


def download_all():
    """BOJ tables every run; ESSJ editions cached (immutable); probe for newer ESSJ editions."""
    RAW.mkdir(parents=True, exist_ok=True)
    sess = requests.Session()
    sess.headers["User-Agent"] = UA
    download_boj(sess)
    for year, slug in sorted(ESSJ_PRODUCTS.items()):
        f = RAW / f"pioj_essj_{year}_overview.pdf"
        if f.exists() and f.stat().st_size > 5000 and f.read_bytes()[:4] == PDF_MAGIC:
            print(f"cached {f.name}")
            continue
        download_essj(sess, year, slug)
    for year in range(max(ESSJ_PRODUCTS) + 1, date.today().year):
        for slug in (f"economic-and-social-survey-jamaica-{year}-selected-indicators-overview",
                     f"economic-and-social-survey-jamaica-{year}-overview-and-selected-indicators",
                     f"economic-and-social-survey-jamaica-{year}-overview"):
            if download_essj(sess, year, slug, required=False):  # not cached until it is added to ESSJ_PRODUCTS
                ESSJ_PRODUCTS[year] = slug
                print(f"new ESSJ edition {year}: {PIOJ_PRODUCT.format(slug)}")
                break
        else:
            print(f"ESSJ {year} overview not published yet (404 on known slugs)")


def travel_credits():
    df = pd.read_excel(RAW / "ES.BOP.00.xls", sheet_name="Services", header=None)
    dates = df.iloc[1, 1:]
    rows = {str(r).strip(): i for i, r in df[0].items() if isinstance(r, str)}
    # first 'Travel' row after 'Credit' is credits; also cross-check the memo row 'Visitor Expenditure (US$MN)'
    credit_idx = df.index[df[0].astype(str).str.strip() == "Credit"][0]
    travel_idx = next(i for i in df.index if i > credit_idx and str(df.iat[i, 0]).strip() == "Travel")
    ve_idx = rows["Visitor Expenditure (US$MN)"]
    out = {}
    for col, d in dates.items():
        if pd.isna(d):
            continue
        d = pd.Timestamp(d)
        v, ve = df.iat[travel_idx, col], df.iat[ve_idx, col]
        if pd.isna(v):
            continue
        assert pd.isna(ve) or abs(float(v) - float(ve)) < 0.01, (d, v, ve)
        out[f"{d.year}-Q{(d.month - 1) // 3 + 1}"] = round(float(v), 1)
    return out


def tourism_fdi():
    df = pd.read_excel(RAW / "ES.FDI.00.xls", sheet_name="ES.FDI.00", header=None)
    hdr_idx = df.index[df[0].astype(str).str.strip() == "FDI Inflows By Sector"][0]
    tour_idx = df.index[df[0].astype(str).str.strip() == "TOURISM"][0]
    out, flags = {}, {}
    for col in range(1, df.shape[1]):
        y = str(df.iat[hdr_idx, col]).strip()
        if y in ("nan", ""):
            continue
        year = y.rstrip("*^").split(".")[0]
        if y.endswith("*"):
            flags[year] = "revised (*)"
        out[year] = round(float(df.iat[tour_idx, col]), 1)
    return out, flags


ESSJ_PRODUCTS = {  # PIOJ free "Economic and Social Survey Jamaica – (Selected Indicators &) Overview" editions
    2019: "economic-and-social-survey-jamaica-2019-overview",
    2020: "economic-and-social-survey-jamaica-2020-overview",
    2021: "economic-and-social-survey-jamaica-2021-selected-indicators-overview",
    2022: "economic-and-social-survey-jamaica-2022-selected-indicators-overview",
    2023: "economic-and-social-survey-jamaica-2023-selected-indicators-overview",
    2024: "economic-and-social-survey-jamaica-2024-selected-indicators-overview",
    2025: "economic-and-social-survey-jamaica-2025-selected-indicators-overview",
}


def _essj_text(year):
    """Two-column layout: extract left/right halves separately, then flatten whitespace."""
    import pdfplumber
    parts = []
    with pdfplumber.open(RAW / f"pioj_essj_{year}_overview.pdf") as pdf:
        for p in pdf.pages:
            w = p.width
            for box in ((0, 0, w / 2, p.height), (w / 2, 0, w, p.height)):
                parts.append(p.crop(box).extract_text() or "")
    return re.sub(r"\s+", " ", " ".join(parts))


def _n(s):
    return s.replace(" ", "")


def essj_tourism():
    """Annual figures quoted in the 'Accommodation & Food Service' paragraph of each ESSJ overview.
    Editions processed oldest->newest; revised prior-year values ('moved from US$X') overwrite older vintages."""
    cruise, stop, stop_exp, cruise_exp = {}, {}, {}, {}
    for ed in sorted(ESSJ_PRODUCTS):
        t = _essj_text(ed)
        y, py = str(ed), str(ed - 1)
        m = re.search(r"Cruise passenger arrivals (?:declined|fell) by [\d.]+ per cent,? to (\d{1,3}(?: \d{3})+) persons", t)
        if m:
            cruise[y] = int(_n(m.group(1)))
        m = re.search(r"Stopover Arrivals and Cruise passengers by [\d.]+ per cent to (\d{1,3}(?: \d{3})+) persons and [\d.]+ per cent to (\d{1,3}(?: \d{3})+) persons", t)
        if m:
            stop[y], cruise[y] = int(_n(m.group(1))), int(_n(m.group(2)))
        m = re.search(r"Cruise Passenger arrivals registered respective decreases of [\d.]+ per cent to (\d{1,3}(?: \d{3})+) persons and [\d.]+ per cent to (\d{1,3}(?: \d{3})+) persons", t)
        if m:
            stop[y], cruise[y] = int(_n(m.group(1))), int(_n(m.group(2)))
        # stopover expenditure
        m = re.search(r"(?:Stopover visitors? expenditure|Stopover visitor’s expenditure|Expenditure by Stopover visitors|Stopover visitors expanded)"
                      r" (?:increased|decreased|moved|expanded)?\s?(?:by US\$[\d .]+? million )?(?:from US\$([\d ]+\.\d) million )?to US\$([\d ]+\.\d) ?million", t)
        if m:
            if m.group(1):
                stop_exp[py] = float(_n(m.group(1)))
            stop_exp[y] = float(_n(m.group(2)))
        # cruise expenditure
        m = re.search(r"cruise passenger expenditure (?:totalled|was|moved from US\$([\d ]+\.\d) million to) US\$([\d ]+\.\d) ?million"
                      r"(?: (?:a decline of [^,.]*|relative to|compared with) US\$([\d ]+\.\d) million in (\d{4}))?", t)
        if m:
            if m.group(1):
                cruise_exp[py] = float(_n(m.group(1)))
            cruise_exp[y] = float(_n(m.group(2)))
            if m.group(3) and m.group(4) == py:
                cruise_exp[py] = float(_n(m.group(3)))
        m = re.search(r"cruise passenger remained relatively flat at US\$([\d ]+\.\d) ?million", t)
        if m:
            cruise_exp[y] = float(_n(m.group(1)))
    return (dict(sorted(stop.items())), dict(sorted(cruise.items())),
            dict(sorted(stop_exp.items())), dict(sorted(cruise_exp.items())))


def check_against_current(series, path):
    """Safeguard: abort if a series present in the current JSON disappears, comes out empty, has fewer periods
    or ends earlier than before."""
    if not path.exists():
        return
    old = {s["key"]: s["data"] for s in json.loads(path.read_text())["series"]}
    new = {s["key"]: s["data"] for s in series}
    problems = []
    for k, od in old.items():
        nd = new.get(k)
        if not nd:
            problems.append(f"{k}: missing or empty (was {len(od)} periods)")
            continue
        if len(nd) < len(od):
            problems.append(f"{k}: {len(nd)} periods < {len(od)} in current JSON")
        if od and nd[-1][0] < od[-1][0]:
            problems.append(f"{k}: last period {nd[-1][0]} < {od[-1][0]} in current JSON")
        lost = sorted({p for p, _ in od} - {p for p, _ in nd})
        if lost:
            problems.append(f"{k}: periods dropped {lost}")
    if problems:
        raise SystemExit(f"ABORT: {path.name} not written; new extraction is poorer than the current file:\n  "
                         + "\n  ".join(problems))


def main():
    global RETRIEVED
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-download", action="store_true", help="use files already in data/raw/JM")
    args = ap.parse_args()
    if args.no_download:
        files = [RAW / n for n in BOJ_FILES] + [RAW / f"pioj_essj_{y}_overview.pdf" for y in ESSJ_PRODUCTS]
        missing = [f.name for f in files if not f.exists()]
        if missing:
            raise SystemExit(f"--no-download: missing raw files {missing}")
        RETRIEVED = date.fromtimestamp(min(f.stat().st_mtime for f in files[:2])).isoformat()
    else:
        try:
            download_all()
        except (DownloadError, OSError) as e:
            print(f"ERROR downloading Jamaica sources: {e}", file=sys.stderr)
            sys.exit(2)
        RETRIEVED = date.today().isoformat()
    e_stop, e_cruise, e_stop_exp, e_cruise_exp = essj_tourism()
    tc = {k: v for k, v in travel_credits().items() if k >= "2019-Q1"}
    fdi, fdi_flags = tourism_fdi()
    fdi = {k: v for k, v in fdi.items() if k >= "2019"}

    series = [
        {"key": "spending_tourism_receipts", "category": "spending",
         "label": "Gasto de visitantes / ingresos por viajes (crédito 'Travel' de balanza de pagos)",
         "unit": "US$ millones", "frequency": "quarterly", "data": [[k, v] for k, v in tc.items()],
         "source": {"org": "Bank of Jamaica (datos de gasto de visitantes del Jamaica Tourist Board)",
                    "title": "ES.BOP.00 Balance of Payments Summary (BPM6) – hoja 'Services': Travel (Credit) / Visitor Expenditure (US$MN)",
                    "page_url": "https://boj.org.jm/statistics/external-sector/balance-of-payments/",
                    "file_url": "https://boj.org.jm/wp-content/uploads/2020/09/ES.BOP.00.xls",
                    "format": "xlsx", "update_frequency": "trimestral (ver Advance Release Calendar del BOJ)",
                    "release_lag": f"~1 trimestre (último dato {max(tc)} disponible al {RETRIEVED})",
                    "last_period": max(tc), "retrieved": RETRIEVED,
                    "access_method": "GET the fixed .xls URL (legacy Excel; read with xlrd). Sheet 'Services': row 1 = quarter-end dates, "
                                     "'Travel' row under 'Credit' (equal to memo row 'Visitor Expenditure (US$MN)')."}},
    ]
    PIOJ_PAGE = "https://www.pioj.gov.jm/product-category/annual-publications/the-economic-social-survey-jamaica/"
    PIOJ_LATEST = PIOJ_PRODUCT.format(ESSJ_PRODUCTS[max(ESSJ_PRODUCTS)])
    how_essj = ("Product page per edition (slug in ESSJ_PRODUCTS); free file obtained by POSTing the page's somdn form "
                "(fields somdn_download_key [url-encode], action=somdn_download_single, somdn_product) to the same URL with cookies. "
                "pdfplumber, crop each page into two columns, regex on the 'Accommodation & Food Service Activities' paragraph.")

    def essj_src(title, last):
        return {"org": "Planning Institute of Jamaica (PIOJ), cifras del Jamaica Tourist Board",
                "title": title, "page_url": PIOJ_PAGE, "file_url": PIOJ_LATEST, "format": "pdf",
                "update_frequency": "anual (ESSJ Overview publicado ~abril-mayo del año siguiente)",
                "release_lag": "~4-5 meses tras cierre del año", "last_period": last, "retrieved": RETRIEVED,
                "access_method": how_essj}

    series += [
        {"key": "arrivals_stopover_annual", "category": "arrivals", "label": "Llegadas de visitantes stopover (anual)",
         "unit": "personas", "frequency": "annual", "data": [[k, v] for k, v in e_stop.items()],
         "source": essj_src("Economic and Social Survey Jamaica – Selected Indicators & Overview (texto, sección Accommodation & Food Service)", max(e_stop))},
        {"key": "arrivals_cruise_annual", "category": "arrivals", "label": "Llegadas de pasajeros de crucero (anual)",
         "unit": "personas", "frequency": "annual", "data": [[k, v] for k, v in e_cruise.items()],
         "source": essj_src("Economic and Social Survey Jamaica – Selected Indicators & Overview (texto, sección Accommodation & Food Service)", max(e_cruise))},
        {"key": "spending_stopover_expenditure", "category": "spending", "label": "Gasto de visitantes stopover (anual)",
         "unit": "US$ millones", "frequency": "annual", "data": [[k, v] for k, v in e_stop_exp.items()],
         "source": essj_src("Economic and Social Survey Jamaica – Overview: 'Expenditure by Stopover visitors'", max(e_stop_exp))},
        {"key": "spending_cruise_expenditure", "category": "spending", "label": "Gasto de pasajeros de crucero (anual)",
         "unit": "US$ millones", "frequency": "annual", "data": [[k, v] for k, v in e_cruise_exp.items()],
         "source": essj_src("Economic and Social Survey Jamaica – Overview: 'cruise passenger expenditure'", max(e_cruise_exp))},
    ]
    series += [
        {"key": "investment_fdi_tourism", "category": "investment",
         "label": "Inversión extranjera directa en turismo (flujos de entrada)", "unit": "US$ millones", "frequency": "annual",
         "data": [[k, v] for k, v in fdi.items()],
         "source": {"org": "Bank of Jamaica", "title": "ES.FDI.00 FDI Inflows by Sector – row TOURISM",
                    "page_url": "https://boj.org.jm/statistics/external-sector/foreign-direct-investments/",
                    "file_url": "https://boj.org.jm/wp-content/uploads/2020/09/ES.FDI.00.xls",
                    "format": "xlsx", "update_frequency": "anual (archivo actualizado trimestralmente)",
                    "release_lag": "'Last Business Day of the Quarter Following the Reporting Period' (2025 disponible en 2026, marcado revisado)",
                    "last_period": max(fdi), "retrieved": RETRIEVED,
                    "access_method": "GET the fixed .xls URL; sheet 'ES.FDI.00', header row 'FDI Inflows By Sector' (years), row 'TOURISM'."}},
    ]
    notes = [
        "NO monthly or quarterly arrivals and NO average spend per visitor / average length of stay series were obtained: the only official "
        "monthly source is the Jamaica Tourist Board (https://www.jtbonline.org/report-and-statistics/monthly-statistics/ and /annual-travel/). "
        "On 2026-09-17 this network's FortiGuard web filter blocked jtbonline.org (category 'Malicious Websites', HTTP 403) and the site also serves an "
        "incomplete TLS certificate chain (WebFetch: 'unable to verify the first certificate'). The block was not bypassed. "
        "BOJ, STATIN and PIOJ do not publish a monthly arrivals table (BOJ only reports visitor expenditure in the BoP; see alternatives checked below).",
        "Alternatives checked 2026-09-17 (no monthly/quarterly arrivals table found): mot.gov.jm (press releases only, no statistics section); "
        "data.gov.jm (connection reset, unreachable); PIOJ free Review of Economic Performance decks and Monthly Economic Updates (no arrivals figures, charts only); "
        "full PIOJ ESSJ with tourism chapter is paid (US$35); STATIN (only Tourism Satellite Account tables, annual J$); BOJ QMPR Aug-2026 (charts of visitor days, no table); "
        "Caribbean Tourism Organization monthly statistics are paid (US$45/issue) and not a national official source; no gov.jm mirror of JTB PDFs found.",
        "arrivals_*_annual and spending_*_expenditure come from sentences in the free PIOJ ESSJ 'Selected Indicators & Overview' PDFs (one per year). Coverage is patchy: "
        "stopover counts are only stated exactly in the 2024 and 2025 editions (2019-2023 overviews give only rounded millions in an indicator table, not used); "
        "cruise counts for 2019-2021, 2024-2025; expenditure for 2019-2022 and 2024-2025 (2023 overview has no tourism paragraph). "
        "Where a later edition restates the prior year ('moved from US$X'), the revised value is used (2019 cruise exp 161.3 vs 162.4; 2021 cruise exp 7.1; 2024 stopover exp 4,171.9 vs 4,170.4; 2024 cruise exp 142.5 vs 142.1).",
        "To add arrivals later: from an unfiltered network, download JTB 'Monthly Statistical Report' PDFs (stopover arrivals by month & port; cruise passengers by port) "
        "and 'Annual Travel Statistics' PDFs (average length of stay, estimated visitor expenditure, avg spend per stopover/cruise passenger).",
        "spending_tourism_receipts: BOJ BoP 'Travel' credits = 'Visitor Expenditure (US$MN)' memo row; BOJ cites the Jamaica Tourist Board as source "
        "(personal travel of stopover + cruise visitors). Values are preliminary and revised in subsequent file versions.",
        "The 2025-Q4 drop coincides with Hurricane Melissa (late Oct-2025, per press reports; not verified against an official file here).",
        "investment_fdi_tourism: BOJ sector FDI (secondary; not tourist spending). 2025 flagged '*' (revised) in the file. "
        "BOJ notes a 2020 sale of a tourism property by a non-resident to a resident, reducing FDI liabilities.",
    ]
    if fdi_flags:
        notes.append("FDI year flags from file: " + ", ".join(f"{k}: {v}" for k, v in fdi_flags.items()))
    doc = {"id": "JM", "name": "Jamaica", "type": "country", "lat": 18.1, "lon": -77.3, "series": series, "notes": notes}
    check_against_current(series, ROOT / "JM.json")
    (ROOT / "JM.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1))
    for s in series:
        print(f"{s['key']:32s} {s['frequency']:9s} {len(s['data']):3d} {s['data'][0]} -> {s['data'][-1]}")


if __name__ == "__main__":
    main()
