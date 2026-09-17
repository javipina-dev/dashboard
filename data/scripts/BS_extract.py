"""Extract official tourism series for The Bahamas -> data/BS.json

Sources (all downloaded to data/raw/BS/):
  * Bahamas Ministry of Tourism (tourismtoday.com/statistics)
      - as_YYYY.pdf  "Air, Sea Landed & Cruise Arrivals YYYY" (foreign arrivals by 1st port of entry, monthly)
      - stopover_month_island_2015_2026.pdf "Yearly Comparison of Stopover Visitors by Island and Month"
      - exp_q_*.pdf "Expenditure by Quarter" (visitor expenditure estimates, 2019-2022)
      - alos_1992_2021.pdf "Stopover Visitors Average Length of Stay"
  * Central Bank of The Bahamas, Quarterly Statistical Digest (QSD) PDFs
      - Table 7.1 Balance of Payments (Travel credits = tourism receipts; Direct investment liabilities)
      - Table 8.5 Tourism: Estimates of Visitor Expenditure (avg expenditure per stopover)
Run: .venv/bin/python data/scripts/BS_extract.py
"""
import json
import re
from pathlib import Path

import pdfplumber

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "raw" / "BS"
RETRIEVED = "2026-09-17"
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]
MABBR = [m[:3] for m in MONTHS]
TT = "https://www.tourismtoday.com"


def num(s):
    return int(s.replace(",", ""))


def pdf_texts(path):
    with pdfplumber.open(path) as pdf:
        return [(p.extract_text() or "") for p in pdf.pages]


# ---------------------------------------------------------------- 1. foreign arrivals by port of entry
def foreign_arrivals():
    out = {}  # "YYYY-MM" -> dict
    for y in range(2019, 2027):
        for text in pdf_texts(RAW / f"as_{y}.pdf"):
            lines = text.split("\n")
            if not lines or not lines[0].startswith("Total Foreign Arrivals Summary Report"):
                continue
            head = " ".join(lines[:3])
            m = re.search(r"(" + "|".join(MONTHS) + r")\s*(\d{4})", head)
            if not m:
                continue
            key = f"{m.group(2)}-{MONTHS.index(m.group(1)) + 1:02d}"
            g = re.search(r"Grand Total\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)", text)
            subs = re.findall(r"Island Group Subtotal\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)", text)
            order = [l.strip() for l in lines if l.strip() in ("Grand Bahama", "New Providence", "The Out Islands")]
            assert order == ["Grand Bahama", "New Providence", "The Out Islands"], (key, order)
            assert len(subs) == 3, key
            air, sea, cruise, tot = map(num, g.groups())
            assert air + sea + cruise == tot, key
            for i in range(4):
                assert sum(num(s[i]) for s in subs) == num(g.groups()[i]), (key, i)
            rec = dict(air=air, sea_landed=sea, cruise=cruise, total=tot,
                       gb_total=num(subs[0][3]), np_total=num(subs[1][3]), oi_total=num(subs[2][3]))
            if key in out:
                assert out[key] == rec, f"duplicate page mismatch {key}"
            out[key] = rec
    return dict(sorted(out.items()))


# ---------------------------------------------------------------- 2. stopover visitors by month & island
def _fix_tokens(tokens, expected=8):
    toks = list(tokens)
    # split thousands like "1 ,228,991"
    i = 0
    while i < len(toks) - 1:
        if toks[i + 1].startswith(","):
            toks[i:i + 2] = [toks[i] + toks[i + 1]]
        else:
            i += 1
    # the PDF splits the Out Islands prior-year value (last column) as "2 9,286"
    if len(toks) == expected + 1:
        toks[-2:] = [toks[-2] + toks[-1]]
    assert len(toks) == expected, tokens
    for t in toks:
        assert re.fullmatch(r"\d{1,3}(,\d{3})*", t), (t, tokens)
    return [num(t) for t in toks]


def stopovers():
    res = {}
    for text in pdf_texts(RAW / "stopover_month_island_2015_2026.pdf"):
        lines = text.split("\n")
        if "STOPOVER VISITORS BY MONTH" not in text:
            continue
        year = int(lines[3].strip())
        rows = {}
        for l in lines:
            parts = l.split()
            if not parts or parts[0] not in MABBR + ["Total"]:
                continue
            vals = [p for p in parts[1:] if "%" not in p and "#DIV" not in p]
            if all(v == "-" for v in vals) or not vals:
                continue
            rows[parts[0]] = _fix_tokens(vals)
        tot = rows.pop("Total")
        for idx in range(0, 8, 2):  # current-year columns: All, NP, GB, OI
            assert sum(r[idx] for r in rows.values()) == tot[idx], (year, idx)
        for mon, r in rows.items():
            assert r[0] == r[2] + r[4] + r[6], (year, mon)
            res[f"{year}-{MABBR.index(mon) + 1:02d}"] = dict(all=r[0], np=r[2], gb=r[4], oi=r[6])
    return dict(sorted(res.items()))


# ---------------------------------------------------------------- 3. CBOB QSD balance of payments
QSD = [  # oldest -> newest; newer vintages overwrite older values
    ("cbob_qsd_quarterly-statistical-digest-august-2021-1.pdf",
     "https://cdn.centralbankbahamas.com/documents/2021-09-10-14-24-12-CBOB-Quarterly-Statistical-Digest---August-2021updated.pdf"),
    ("cbob_qsd_quarterly-statistical-digest-august-2023.pdf",
     "https://cdn.centralbankbahamas.com/documents/2023-08-29-10-15-22-CBOB-Quarterly-Statistical-DigestAugust-2023Final.pdf"),
    ("cbob_qsd_quarterly-statistical-digest-august-2025-1.pdf",
     "https://cdn.centralbankbahamas.com/documents/2025-08-27-14-40-07-CBOB-Quarterly-Digest-August-2025.pdf"),
    ("cbob_qsd_2026-08.pdf",
     "https://cdn.centralbankbahamas.com/documents/2026-08-26-13-58-19-CBOB-Quarterly-Digest---August-2026.pdf"),
]
ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4}


def _floats(line, label):
    return [float(x) for x in line[len(label):].split()]


def qsd_bop():
    q_travel, a_travel, a_fdi = {}, {}, {}
    for fname, _ in QSD:
        texts = pdf_texts(RAW / fname)
        for text in texts:
            lines = text.split("\n")
            if not any(l.startswith("Table 7.1 Balance of Payments") for l in lines[:3]):
                continue
            trav = [l for l in lines if l.startswith("Travel ")]
            di = [l for l in lines if l.startswith("Direct Investment ")]
            if len(trav) != 2 or len(di) != 2:
                continue  # old BPM5 layout
            credits = _floats(trav[1], "Travel")      # 2nd Travel row = CURRENT ACCOUNT RECEIPTS
            if "Qtr" in text:
                qline = next(l for l in lines if l.startswith("Qtr"))
                if "august-2021" in fname:
                    # header in this edition is mistyped ("2019 2020 2021 / Qtr IIp Qtr IIp Qtr IVp Qtr Ip Qtr IIp Qtr IIp Qtr IVp Qtr Ip");
                    # 8 consecutive quarters 2019Q2..2021Q1 (sum of 2020 quarters = 967.5 vs annual 967.4).
                    periods = ["2019-Q2", "2019-Q3", "2019-Q4", "2020-Q1", "2020-Q2", "2020-Q3", "2020-Q4", "2021-Q1"]
                else:
                    yline = next(l for l in lines if re.fullmatch(r"(\d{4}\s*)+", l.strip()))
                    years = yline.split()
                    qs = re.findall(r"Qtr\.\s*(IV|III|II|I)p?", qline)
                    assert len(years) == len(qs), (fname, years, qs)
                    periods = [f"{y}-Q{ROMAN[q]}" for y, q in zip(years, qs)]
                assert len(periods) == len(credits), (fname, periods, credits)
                q_travel.update(dict(zip(periods, credits)))
            else:
                yline = next(l for l in lines if re.fullmatch(r"(\d{4}\s*)+", l.strip()))
                years = yline.split()
                assert len(years) == len(credits)
                a_travel.update(dict(zip(years, credits)))
                a_fdi.update(dict(zip(years, _floats(di[1], "Direct Investment"))))  # 2nd = liabilities
    return dict(sorted(q_travel.items())), dict(sorted(a_travel.items())), dict(sorted(a_fdi.items()))


def qsd_avg_exp_per_stopover():
    out = {}
    for text in pdf_texts(RAW / "cbob_qsd_2026-08.pdf"):
        if not text.startswith("Table 8.5 Tourism: Estimates of Visitor Expenditure"):
            continue
        for l in text.split("\n"):
            m = re.match(r"^(\d{4})\s+[\d,]+\s+[\d,]+\s+([\d,.]+)\s+([\d,.]+)\s+([\d,.]+)\s+([\d,.]+)\s+([\d,.]+)\s+([\d,.]+)$", l)
            if m:
                out[m.group(1)] = float(m.group(6).replace(",", ""))  # avg annual expenditure per stopover, current prices
    return out


# ---------------------------------------------------------------- 4. MoT expenditure by quarter & ALOS
def mot_stopover_expenditure():
    files = [("exp_q_2020_2019.pdf", {"2019": 1}), ("exp_q_2021_2020.pdf", {"2020": 1}),
             ("exp_q_2022_2021.pdf", {"2021": 1, "2022": 0})]  # column 0 = current year, 1 = prior year
    qnames = ["FIRST QUARTER", "SECOND QUARTER", "THIRD QUARTER", "FOURTH QUARTER"]
    out, allv = {}, {}
    for fname, years in files:
        lines = pdf_texts(RAW / fname)[0].split("\n")
        q = None
        for l in lines:
            if l.strip() in qnames:
                q = qnames.index(l.strip()) + 1
            elif l.strip() == "FULL YEAR TOTAL":
                q = None
            elif l.startswith("All Bahamas") and q:
                amounts = re.findall(r"\$([\d,]+(?:\.\d+)?)", l)
                for y, col in years.items():
                    out[f"{y}-Q{q}"] = round(float(amounts[col].replace(",", "")) / 1e6, 1)  # stopover cols 0/1
                    allv[f"{y}-Q{q}"] = round(float(amounts[6 + col].replace(",", "")) / 1e6, 1)  # all-visitors cols 6/7
    return dict(sorted(out.items())), dict(sorted(allv.items()))


def alos():
    out = {}
    for l in pdf_texts(RAW / "alos_1992_2021.pdf")[0].split("\n"):
        m = re.match(r"^(\d{4})\s+([\d.]+)\s", l)
        if m and int(m.group(1)) >= 2019:
            out[m.group(1)] = float(m.group(2))
    return out


def src(org, title, page_url, file_url, fmt, freq, lag, last, how):
    return {"org": org, "title": title, "page_url": page_url, "file_url": file_url, "format": fmt,
            "update_frequency": freq, "release_lag": lag, "last_period": last, "retrieved": RETRIEVED,
            "access_method": how}


def main():
    fa = foreign_arrivals()
    so = stopovers()
    q_trav, a_trav, a_fdi = qsd_bop()
    avg_exp = qsd_avg_exp_per_stopover()
    mot_exp, mot_all = mot_stopover_expenditure()
    stay = alos()

    fa = {k: v for k, v in fa.items() if k >= "2019-01"}
    so = {k: v for k, v in so.items() if k >= "2019-01"}
    q_trav = {k: v for k, v in q_trav.items() if k >= "2019-Q1"}
    # MoT all-visitor expenditure = CBOB travel credits (checked on overlapping quarters); use it only to fill 2019-Q1
    for k in ("2019-Q2", "2019-Q3", "2019-Q4", "2021-Q2", "2022-Q1", "2022-Q4"):
        assert abs(mot_all[k] - q_trav[k]) <= 0.2, (k, mot_all[k], q_trav[k])
    if "2019-Q1" not in q_trav:
        q_trav["2019-Q1"] = mot_all["2019-Q1"]
        q_trav = dict(sorted(q_trav.items()))

    # sanity: quarterly travel receipts must add up to annual figures (±0.3 rounding)
    for y in range(2020, 2026):
        qs = [q_trav.get(f"{y}-Q{i}") for i in range(1, 5)]
        if None not in qs:
            assert abs(sum(qs) - a_trav[str(y)]) < 0.3, (y, sum(qs), a_trav[str(y)])

    MOT_ARR_PAGE = f"{TT}/statistics/foreign-arrivals-air-sea-data"
    AS2026 = f"{TT}/sites/default/files/docs/Oliver%20Air%20Sea%20Landed%20%26%20Cruise%20Arrivals%202026%20%283%29.pdf"
    STOP_PAGE = f"{TT}/statistics/frequently-requested-stopover-statistics-trends"
    STOP_PDF = f"{TT}/sites/default/files/docs/2026%20Stopover%20By%20Month%20and%20Island%20Comparision%202015-2026_1.pdf"
    QSD_PAGE = "https://www.centralbankbahamas.com/publications/qsd"
    QSD_PDF = QSD[-1][1]
    last_m = max(fa)
    last_s = max(so)
    how_arr = ("Scrape the page for the current-year 'Air Sea Landed & Cruise Arrivals YYYY' PDF link (file name changes with each "
               "revision), download, pdfplumber: each monthly page 'Total Foreign Arrivals Summary Report <Month YYYY>' has a "
               "'Grand Total air sea_landed cruise total' line and 'Island Group Subtotal' lines (GB, NP, Out Islands).")
    how_stop = ("Scrape page for 'Yearly Comparison of Stopover Visitors by Island and Month' PDF (one page per year, newest first); "
                "pdfplumber text rows 'Mon cur prev %chg' x4 regions; Out Islands prior-year column is split ('2 9,286') and must be re-joined.")
    how_qsd = ("List https://www.centralbankbahamas.com/publications/qsd, open newest digest page, take cdn.centralbankbahamas.com PDF; "
               "Table 7.1 Balance of Payments (quarterly page): second 'Travel' row = current-account receipts. Each digest carries "
               "~10 quarters, so older quarters come from older digests.")

    series = []

    def add(key, cat, label, unit, freq, data, source):
        series.append({"key": key, "category": cat, "label": label, "unit": unit, "frequency": freq,
                       "data": [[k, v] for k, v in data.items()], "source": source})

    add("arrivals_stopover", "arrivals", "Llegadas de visitantes stopover (pernoctan; aire y mar)", "personas", "monthly",
        {k: v["all"] for k, v in so.items()},
        src("Bahamas Ministry of Tourism, Investments & Aviation – Research & Statistics Dept.",
            "Yearly Comparison of Stopover Visitors by Island and Month 2015-2026 (preliminary)", STOP_PAGE, STOP_PDF, "pdf",
            "mensual (PDF acumulado reemplazado cada mes)", "~6-7 semanas tras el cierre del mes", last_s, how_stop))
    for reg, lbl in [("np", "Nassau/Paradise Island"), ("gb", "Grand Bahama"), ("oi", "Out Islands (Family Islands)")]:
        add(f"arrivals_stopover_{ {'np':'nassau_pi','gb':'grand_bahama','oi':'out_islands'}[reg] }", "arrivals",
            f"Llegadas stopover – {lbl} (por lugar de estadía)", "personas", "monthly",
            {k: v[reg] for k, v in so.items()},
            src("Bahamas Ministry of Tourism, Investments & Aviation – Research & Statistics Dept.",
                "Yearly Comparison of Stopover Visitors by Island and Month 2015-2026 (preliminary)", STOP_PAGE, STOP_PDF, "pdf",
                "mensual", "~6-7 semanas tras el cierre del mes", last_s, how_stop))
    add("arrivals_cruise", "arrivals", "Llegadas de pasajeros de crucero (extranjeros, 1er puerto de entrada)", "personas", "monthly",
        {k: v["cruise"] for k, v in fa.items()},
        src("Bahamas Ministry of Tourism, Investments & Aviation (datos del Dept. of Immigration)",
            "Air, Sea Landed & Cruise Arrivals – Total Foreign Arrivals Summary Report by 1st Port of Entry", MOT_ARR_PAGE, AS2026,
            "pdf", "mensual (PDF anual acumulado)", "~6-7 semanas tras el cierre del mes", last_m, how_arr))
    add("arrivals_air", "arrivals", "Llegadas aéreas de extranjeros (1er puerto de entrada, incluye tránsitos)", "personas", "monthly",
        {k: v["air"] for k, v in fa.items()},
        src("Bahamas Ministry of Tourism, Investments & Aviation (datos del Dept. of Immigration)",
            "Air, Sea Landed & Cruise Arrivals – Total Foreign Arrivals Summary Report by 1st Port of Entry", MOT_ARR_PAGE, AS2026,
            "pdf", "mensual", "~6-7 semanas tras el cierre del mes", last_m, how_arr))
    add("arrivals_sea_landed", "arrivals", "Llegadas marítimas no crucero (yates/ferris, 'sea landed')", "personas", "monthly",
        {k: v["sea_landed"] for k, v in fa.items()},
        src("Bahamas Ministry of Tourism, Investments & Aviation (datos del Dept. of Immigration)",
            "Air, Sea Landed & Cruise Arrivals – Total Foreign Arrivals Summary Report by 1st Port of Entry", MOT_ARR_PAGE, AS2026,
            "pdf", "mensual", "~6-7 semanas tras el cierre del mes", last_m, how_arr))
    add("arrivals_total_foreign", "arrivals", "Llegadas totales de extranjeros (aire + mar + crucero)", "personas", "monthly",
        {k: v["total"] for k, v in fa.items()},
        src("Bahamas Ministry of Tourism, Investments & Aviation (datos del Dept. of Immigration)",
            "Air, Sea Landed & Cruise Arrivals – Total Foreign Arrivals Summary Report by 1st Port of Entry", MOT_ARR_PAGE, AS2026,
            "pdf", "mensual", "~6-7 semanas tras el cierre del mes", last_m, how_arr))

    add("spending_tourism_receipts", "spending", "Ingresos por viajes (turismo) – crédito de balanza de pagos", "US$ millones",
        "quarterly", q_trav,
        src("Central Bank of The Bahamas", "Quarterly Statistical Digest – Table 7.1 Balance of Payments (BPM6), Travel receipts",
            QSD_PAGE, QSD_PDF, "pdf", "trimestral (QSD publicado feb/may/ago/nov)", "~5 meses (Ago-2026 trae hasta 2026-T1)",
            max(q_trav), how_qsd))
    add("spending_tourism_receipts_annual", "spending", "Ingresos por viajes (turismo) – anual, balanza de pagos", "US$ millones",
        "annual", {k: v for k, v in a_trav.items() if k >= "2019"},
        src("Central Bank of The Bahamas", "Quarterly Statistical Digest – Table 7.1 Balance of Payments (annual page)",
            QSD_PAGE, QSD_PDF, "pdf", "trimestral (revisión anual)", "~5-8 meses tras cierre del año",
            max(a_trav), how_qsd.replace("(quarterly page)", "(annual page)")))
    add("spending_stopover_expenditure", "spending", "Gasto estimado de visitantes stopover (Ministerio de Turismo)", "US$ millones",
        "quarterly", mot_exp,
        src("Bahamas Ministry of Tourism, Investments & Aviation – Research & Statistics Dept.",
            "Expenditure by Quarter (2020&2019, 2021&2020, 2022&2021) – All Bahamas, stopover column", f"{TT}/statistics/expenditure",
            f"{TT}/sites/default/files/docs/Expenditure%20by%20Quarter%202022%20and%202021.pdf", "pdf",
            "trimestral/anual (discontinuado: último PDF publicado = 2022)", "n/d (sin actualizaciones desde 2022)", max(mot_exp),
            "Download 'Expenditure by Quarter YYYY and YYYY-1' PDFs; 'All Bahamas' row under each quarter, first $ amount = stopover."))
    add("spending_avg_per_visitor", "spending", "Gasto promedio por visitante stopover (por viaje, precios corrientes)", "US$",
        "annual", {k: v for k, v in avg_exp.items() if k >= "2019"},
        src("Central Bank of The Bahamas (fuente: Ministry of Tourism exit surveys)",
            "Quarterly Statistical Digest – Table 8.5 Tourism: Estimates of Visitor Expenditure", QSD_PAGE, QSD_PDF, "pdf",
            "anual (serie n.a. desde 2023)", "sin datos después de 2022", max(k for k in avg_exp if k >= "2019"),
            "QSD PDF, page titled 'Table 8.5 Tourism: Estimates of Visitor Expenditure'; column 'Average Annual Expenditure of Stopover Visitors – In Current Prices'."))
    add("spending_avg_stay", "spending", "Estadía promedio de visitantes stopover", "noches", "annual", stay,
        src("Bahamas Ministry of Tourism, Investments & Aviation – Research & Statistics Dept.",
            "Stopover Visitors Average Length of Stay 1992-2021 (immigration cards)", STOP_PAGE,
            f"{TT}/sites/default/files/average_length_of_stay_1992_to_2021_3.pdf", "pdf", "anual (último PDF publicado cubre hasta 2021)",
            "sin actualizaciones desde 2021", max(stay), "Download PDF; row per year, first value = All Bahamas nights."))
    add("investment_fdi_inflows", "investment", "Inversión extranjera directa – pasivos netos incurridos (BdP)", "US$ millones",
        "annual", {k: v for k, v in a_fdi.items() if k >= "2019"},
        src("Central Bank of The Bahamas", "Quarterly Statistical Digest – Table 7.1 Balance of Payments, Financial account: Net incurrence of liabilities – Direct Investment",
            QSD_PAGE, QSD_PDF, "pdf", "trimestral", "~5 meses", max(a_fdi),
            "Annual BoP page of latest QSD; second 'Direct Investment' row (liabilities block). No tourism-sector breakdown is published."))

    notes = [
        "Bahamian dollar is pegged 1:1 to the US dollar; B$ millions from CBOB/MoT are reported as US$ millones without conversion.",
        "arrivals_stopover = MoT 'stopover visitors' (stay >24h, by island of stay, from immigration cards; excludes excursionists). "
        "It is NOT equal to foreign air arrivals (arrivals_air counts air arrivals by first port of entry, incl. transit, and excludes sea-arriving stopovers).",
        "arrivals_cruise = cruise passenger arrivals by first port of entry (includes private-island calls: Coco Cay, Castaway Cay, Celebration Key, Ocean Cay, etc.). "
        "Cruise passengers visiting several Bahamian ports are counted once by first port of entry.",
        "All MoT monthly figures are labelled preliminary and are revised within the current-year PDF; values here are from the latest PDF of each year retrieved 2026-09-17.",
        "Stopover monthly values from the combined 2015-2026 PDF; each year's own page was used (current-year columns); regional sums and annual totals validated. "
        "2025 total there (1,838,168) differs from CBOB QSD Table 8.4 (1,826,103 / 1,821,076) — different vintages.",
        "Minor vintage differences vs CBOB QSD Table 8.4 annual totals: 2021 air arrivals (monthly sum 886,653 vs 886,629) and 2020 stopovers (440,594 vs 440,588); all other years 2019-2025 match exactly for air, cruise, total and stopover (except 2025 stopover, see above).",
        "April-May 2020: borders closed (COVID-19) — near-zero arrivals are genuine.",
        "spending_tourism_receipts: CBOB BoP travel credits equal MoT total visitor expenditure estimates (stopover+cruise+day). 2019-Q1 is missing: "
        "the earliest BPM6-layout digest (Aug-2021) starts at 2019-Q2, so 2019-Q1 (1,295.1) is taken from the MoT 'Expenditure by Quarter 2020 and 2019' PDF, All Bahamas / All Visitors "
        "(MoT all-visitor expenditure matches CBOB travel credits within 0.2 on every overlapping quarter checked). "
        "Q2-2019..Q1-2021 come from the Aug-2021 QSD (header row mistyped there; quarter order verified against 2020 annual total); later quarters from the most recent digest containing them.",
        "Quarterly receipts validated: 2020-2025 quarters sum to CBOB annual totals within ±0.3 (rounding).",
        "MoT stopover expenditure by quarter and avg expenditure per stopover are only published through 2022 (CBOB Table 8.5 shows n.a. for 2023-2025); average length of stay PDF only through 2021.",
        "Hurricane Dorian (Sep-2019, Abaco & Grand Bahama), Tropical Storm Imelda (Sep-2025) and Hurricane Melissa (late Oct-2025) affected arrivals per MoT notes.",
        "FDI series is total economy (no tourism-sector split published by CBOB); kept as secondary context only.",
    ]
    doc = {"id": "BS", "name": "Bahamas", "type": "country", "lat": 25.03, "lon": -77.4, "series": series, "notes": notes}
    (ROOT / "BS.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1))
    for s in series:
        print(f"{s['key']:40s} {s['frequency']:9s} {len(s['data']):4d} {s['data'][0]} -> {s['data'][-1]}")


if __name__ == "__main__":
    main()
