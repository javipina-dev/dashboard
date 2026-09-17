"""Extract official tourism series for Jamaica -> data/JM.json

Sources (downloaded to data/raw/JM/):
  * Bank of Jamaica (BOJ) statistics, Excel tables (direct, stable URLs; files overwritten on each update)
      - ES.BOP.00.xls  Balance of Payments Summary (BPM6), quarterly; sheet 'Services':
                        'Travel' credit row / 'Visitor Expenditure (US$MN)' (source: Jamaica Tourist Board)
      - ES.FDI.00.xls  FDI Inflows by Sector, annual; row 'TOURISM'
Download:
  curl -A "Mozilla/5.0" -o data/raw/JM/ES.BOP.00.xls https://boj.org.jm/wp-content/uploads/2020/09/ES.BOP.00.xls
  curl -A "Mozilla/5.0" -o data/raw/JM/ES.FDI.00.xls https://boj.org.jm/wp-content/uploads/2020/09/ES.FDI.00.xls
Run: .venv/bin/python data/scripts/JM_extract.py
"""
import json
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "raw" / "JM"
RETRIEVED = "2026-09-17"


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


def main():
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
                    "release_lag": "~1 trimestre (último dato 2026-T1 disponible a sep-2026)",
                    "last_period": max(tc), "retrieved": RETRIEVED,
                    "access_method": "GET the fixed .xls URL (legacy Excel; read with xlrd). Sheet 'Services': row 1 = quarter-end dates, "
                                     "'Travel' row under 'Credit' (equal to memo row 'Visitor Expenditure (US$MN)')."}},
    ]
    PIOJ_PAGE = "https://www.pioj.gov.jm/product-category/annual-publications/the-economic-social-survey-jamaica/"
    PIOJ_2025 = "https://www.pioj.gov.jm/product/" + ESSJ_PRODUCTS[2025] + "/"
    how_essj = ("Product page per edition (slug in ESSJ_PRODUCTS); free file obtained by POSTing the page's somdn form "
                "(fields somdn_download_key [url-encode], action=somdn_download_single, somdn_product) to the same URL with cookies. "
                "pdfplumber, crop each page into two columns, regex on the 'Accommodation & Food Service Activities' paragraph.")

    def essj_src(title, last):
        return {"org": "Planning Institute of Jamaica (PIOJ), cifras del Jamaica Tourist Board",
                "title": title, "page_url": PIOJ_PAGE, "file_url": PIOJ_2025, "format": "pdf",
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
    (ROOT / "JM.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1))
    for s in series:
        print(f"{s['key']:32s} {s['frequency']:9s} {len(s['data']):3d} {s['data'][0]} -> {s['data'][-1]}")


if __name__ == "__main__":
    main()
