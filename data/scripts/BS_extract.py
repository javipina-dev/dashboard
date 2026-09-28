"""Extract official tourism series for The Bahamas -> data/BS.json

Sources (downloaded to data/raw/BS/ on every run by BS_fetch.py, which finds each file on the official
listing pages so the newest version is always used):
  * Bahamas Ministry of Tourism (tourismtoday.com/statistics)
      - as_YYYY.pdf  "Air, Sea Landed & Cruise Arrivals YYYY" (foreign arrivals by 1st port of entry, monthly), 2019..current year
      - stopover_month_island.pdf "Yearly Comparison of Stopover Visitors by Island and Month 2015-YYYY"
      - exp_q_YYYY_YYYY-1.pdf "Expenditure by Quarter YYYY & YYYY-1" (visitor expenditure estimates)
      - alos.pdf "Stopover Visitors Average Length of Stay"
  * Central Bank of The Bahamas, Quarterly Statistical Digest (QSD) PDFs, cbob_qsd_YYYY-MM.pdf
      - Table 7.1 Balance of Payments (Travel credits = tourism receipts; Direct investment liabilities)
      - Table 8.5 Tourism: Estimates of Visitor Expenditure (avg expenditure per stopover)
Run: .venv/bin/python data/scripts/BS_extract.py              (downloads, then extracts)
     .venv/bin/python data/scripts/BS_extract.py --no-download  (uses the files already in data/raw/BS/)
If a download fails or a series would lose periods vs the current data/BS.json, exits != 0 without writing.
"""
import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

import pdfplumber

sys.path.insert(0, str(Path(__file__).resolve().parent))
import BS_fetch  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RAW = BS_fetch.RAW
RETRIEVED = date.today().isoformat()  # overwritten per file with the real download date (manifest)
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
    years = sorted(int(p.stem[3:]) for p in RAW.glob("as_*.pdf") if re.fullmatch(r"as_\d{4}", p.stem))
    assert years and years == list(range(2019, years[-1] + 1)), f"missing as_YYYY.pdf files: {years}"
    for y in years:
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
    for text in pdf_texts(RAW / "stopover_month_island.pdf"):
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
def qsd_files():
    """[(period 'YYYY-MM', path)] oldest -> newest; newer vintages overwrite older values."""
    out = sorted((p.stem[len("cbob_qsd_"):], p) for p in RAW.glob("cbob_qsd_*.pdf")
                 if re.fullmatch(r"cbob_qsd_\d{4}-\d{2}", p.stem))
    assert [v for v, _ in out if v in BS_fetch.QSD_FIXED] == BS_fetch.QSD_FIXED, f"missing QSD vintages: {out}"
    return out


ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4}


def _floats(line, label):
    return [float(x) for x in line[len(label):].split()]


def qsd_bop():
    q_travel, a_travel, a_fdi = {}, {}, {}
    for vintage, path in qsd_files():
        fname = path.name
        texts = pdf_texts(path)
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
                if vintage == "2021-08":
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
    for text in pdf_texts(qsd_files()[-1][1]):
        if not text.startswith("Table 8.5 Tourism: Estimates of Visitor Expenditure"):
            continue
        for l in text.split("\n"):
            m = re.match(r"^(\d{4})\s+[\d,]+\s+[\d,]+\s+([\d,.]+)\s+([\d,.]+)\s+([\d,.]+)\s+([\d,.]+)\s+([\d,.]+)\s+([\d,.]+)$", l)
            if m:
                out[m.group(1)] = float(m.group(6).replace(",", ""))  # avg annual expenditure per stopover, current prices
    return out


# ---------------------------------------------------------------- 4. MoT expenditure by quarter & ALOS
def mot_stopover_expenditure():
    # "Expenditure by Quarter Y+1 and Y": column 0 = year Y+1, column 1 = year Y. Each year is taken from the newest
    # PDF that contains it (prior-year column of the next file = revised figure; else current-year column).
    pairs = sorted(tuple(map(int, p.stem.split("_")[2:])) for p in RAW.glob("exp_q_*_*.pdf"))
    assert pairs and pairs[0][0] == 2020 and [a for a, _ in pairs] == list(range(2020, pairs[-1][0] + 1)), pairs
    files = []
    for y in range(2019, pairs[-1][0] + 1):
        files.append((f"exp_q_{y + 1}_{y}.pdf", {str(y): 1}) if y + 1 <= pairs[-1][0] else (f"exp_q_{y}_{y - 1}.pdf", {str(y): 0}))
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
    for y in range(2019, pairs[-1][0] + 1):
        assert all(f"{y}-Q{q}" in out for q in range(1, 5)), f"expenditure quarters missing for {y}"
    return dict(sorted(out.items())), dict(sorted(allv.items()))


def alos():
    out = {}
    for l in pdf_texts(RAW / "alos.pdf")[0].split("\n"):
        m = re.match(r"^(\d{4})\s+([\d.]+)\s", l)
        if m and int(m.group(1)) >= 2019:
            out[m.group(1)] = float(m.group(2))
    return out


def src(org, title, page_url, file_url, fmt, freq, lag, last, how):
    return {"org": org, "title": title, "page_url": page_url, "file_url": file_url, "format": fmt,
            "update_frequency": freq, "release_lag": lag, "last_period": last, "retrieved": _retrieved(file_url),
            "access_method": how}


MANIFEST = {}


def raw_url(name):
    assert name in MANIFEST, f"{name}: source URL unknown (data/raw/BS/_sources.json); run without --no-download"
    return MANIFEST[name]["url"]


def _retrieved(url):
    dates = {v["retrieved"] for v in MANIFEST.values() if v["url"] == url}
    assert len(dates) == 1, (url, dates)
    return dates.pop()


def check_against_current(series):
    """Abort if any series in the current data/BS.json would disappear, be empty or lose periods."""
    path = ROOT / "BS.json"
    if not path.exists():
        return
    new = {s["key"]: [k for k, _ in s["data"]] for s in series}
    errs = []
    for s in json.loads(path.read_text()).get("series", []):
        old = [k for k, _ in s["data"]]
        cur = new.get(s["key"])
        if cur is None:
            errs.append(f"{s['key']}: missing in new extraction")
        elif not cur:
            errs.append(f"{s['key']}: empty in new extraction")
        elif len(cur) < len(old) or set(old) - set(cur):
            errs.append(f"{s['key']}: {len(old)} -> {len(cur)} periods; lost {sorted(set(old) - set(cur))[:12]}")
    if errs:
        raise SystemExit("BS_extract: NOT writing data/BS.json, series would shrink vs current file:\n  " + "\n  ".join(errs))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--no-download", action="store_true", help="use the files already in data/raw/BS/")
    args = ap.parse_args()
    if not args.no_download:
        try:
            BS_fetch.fetch_all()
        except (BS_fetch.FetchError, OSError) as e:
            raise SystemExit(f"BS_extract: download failed, data/BS.json not written: {e}")
    MANIFEST.update(BS_fetch.load_manifest())

    fa = foreign_arrivals()
    so = stopovers()
    so_span = f"{min(so)[:4]}-{max(so)[:4]}"
    q_trav, a_trav, a_fdi = qsd_bop()
    avg_exp = qsd_avg_exp_per_stopover()
    mot_exp, mot_all = mot_stopover_expenditure()
    exp_last = int(max(mot_exp)[:4])
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
    for y in range(2020, date.today().year):
        qs = [q_trav.get(f"{y}-Q{i}") for i in range(1, 5)]
        if None not in qs:
            assert abs(sum(qs) - a_trav[str(y)]) < 0.3, (y, sum(qs), a_trav[str(y)])

    MOT_ARR_PAGE = f"{TT}/statistics/foreign-arrivals-air-sea-data"
    AS_CUR = raw_url(f"as_{max(fa)[:4]}.pdf")
    STOP_PAGE = f"{TT}/statistics/frequently-requested-stopover-statistics-trends"
    STOP_PDF = raw_url("stopover_month_island.pdf")
    QSD_PAGE = "https://www.centralbankbahamas.com/publications/qsd"
    QSD_PDF = raw_url(qsd_files()[-1][1].name)
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
            f"Yearly Comparison of Stopover Visitors by Island and Month {so_span} (preliminary)", STOP_PAGE, STOP_PDF, "pdf",
            "mensual (PDF acumulado reemplazado cada mes)", "~6-7 semanas tras el cierre del mes", last_s, how_stop))
    for reg, lbl in [("np", "Nassau/Paradise Island"), ("gb", "Grand Bahama"), ("oi", "Out Islands (Family Islands)")]:
        add(f"arrivals_stopover_{ {'np':'nassau_pi','gb':'grand_bahama','oi':'out_islands'}[reg] }", "arrivals",
            f"Llegadas stopover – {lbl} (por lugar de estadía)", "personas", "monthly",
            {k: v[reg] for k, v in so.items()},
            src("Bahamas Ministry of Tourism, Investments & Aviation – Research & Statistics Dept.",
                f"Yearly Comparison of Stopover Visitors by Island and Month {so_span} (preliminary)", STOP_PAGE, STOP_PDF, "pdf",
                "mensual", "~6-7 semanas tras el cierre del mes", last_s, how_stop))
    add("arrivals_cruise", "arrivals", "Llegadas de pasajeros de crucero (extranjeros, 1er puerto de entrada)", "personas", "monthly",
        {k: v["cruise"] for k, v in fa.items()},
        src("Bahamas Ministry of Tourism, Investments & Aviation (datos del Dept. of Immigration)",
            "Air, Sea Landed & Cruise Arrivals – Total Foreign Arrivals Summary Report by 1st Port of Entry", MOT_ARR_PAGE, AS_CUR,
            "pdf", "mensual (PDF anual acumulado)", "~6-7 semanas tras el cierre del mes", last_m, how_arr))
    add("arrivals_air", "arrivals", "Llegadas aéreas de extranjeros (1er puerto de entrada, incluye tránsitos)", "personas", "monthly",
        {k: v["air"] for k, v in fa.items()},
        src("Bahamas Ministry of Tourism, Investments & Aviation (datos del Dept. of Immigration)",
            "Air, Sea Landed & Cruise Arrivals – Total Foreign Arrivals Summary Report by 1st Port of Entry", MOT_ARR_PAGE, AS_CUR,
            "pdf", "mensual", "~6-7 semanas tras el cierre del mes", last_m, how_arr))
    add("arrivals_sea_landed", "arrivals", "Llegadas marítimas no crucero (yates/ferris, 'sea landed')", "personas", "monthly",
        {k: v["sea_landed"] for k, v in fa.items()},
        src("Bahamas Ministry of Tourism, Investments & Aviation (datos del Dept. of Immigration)",
            "Air, Sea Landed & Cruise Arrivals – Total Foreign Arrivals Summary Report by 1st Port of Entry", MOT_ARR_PAGE, AS_CUR,
            "pdf", "mensual", "~6-7 semanas tras el cierre del mes", last_m, how_arr))
    add("arrivals_total_foreign", "arrivals", "Llegadas totales de extranjeros (aire + mar + crucero)", "personas", "monthly",
        {k: v["total"] for k, v in fa.items()},
        src("Bahamas Ministry of Tourism, Investments & Aviation (datos del Dept. of Immigration)",
            "Air, Sea Landed & Cruise Arrivals – Total Foreign Arrivals Summary Report by 1st Port of Entry", MOT_ARR_PAGE, AS_CUR,
            "pdf", "mensual", "~6-7 semanas tras el cierre del mes", last_m, how_arr))

    add("spending_tourism_receipts", "spending", "Ingresos por viajes (turismo) – crédito de balanza de pagos", "US$ millones",
        "quarterly", q_trav,
        src("Central Bank of The Bahamas", "Quarterly Statistical Digest – Table 7.1 Balance of Payments (BPM6), Travel receipts",
            QSD_PAGE, QSD_PDF, "pdf", "trimestral (QSD publicado feb/may/ago/nov)", f"~5 meses (QSD {qsd_files()[-1][0]} trae hasta {max(q_trav)})",
            max(q_trav), how_qsd))
    add("spending_tourism_receipts_annual", "spending", "Ingresos por viajes (turismo) – anual, balanza de pagos", "US$ millones",
        "annual", {k: v for k, v in a_trav.items() if k >= "2019"},
        src("Central Bank of The Bahamas", "Quarterly Statistical Digest – Table 7.1 Balance of Payments (annual page)",
            QSD_PAGE, QSD_PDF, "pdf", "trimestral (revisión anual)", "~5-8 meses tras cierre del año",
            max(a_trav), how_qsd.replace("(quarterly page)", "(annual page)")))
    add("spending_stopover_expenditure", "spending", "Gasto estimado de visitantes stopover (Ministerio de Turismo)", "US$ millones",
        "quarterly", mot_exp,
        src("Bahamas Ministry of Tourism, Investments & Aviation – Research & Statistics Dept.",
            f"Expenditure by Quarter (preliminary, revised) 2020&2019 … {exp_last}&{exp_last - 1} – All Bahamas, stopover column",
            BS_fetch.EXP_PAGE, raw_url(f"exp_q_{exp_last}_{exp_last - 1}.pdf"), "pdf",
            "anual (un PDF por año con sus 4 trimestres, más el año previo revisado)", "~1,5 años tras el cierre del año", max(mot_exp),
            "Scrape the Expenditure page table (link text 'YYYY & YYYY-1'), download each PDF; 'All Bahamas' row under each quarter, "
            "first $ amount = stopover (current year), second = prior year. Each year is taken from the newest PDF containing it."))
    add("spending_avg_per_visitor", "spending", "Gasto promedio por visitante stopover (por viaje, precios corrientes)", "US$",
        "annual", {k: v for k, v in avg_exp.items() if k >= "2019"},
        src("Central Bank of The Bahamas (fuente: Ministry of Tourism exit surveys)",
            "Quarterly Statistical Digest – Table 8.5 Tourism: Estimates of Visitor Expenditure", QSD_PAGE, QSD_PDF, "pdf",
            f"anual (serie n.a. después de {max(k for k in avg_exp if k >= '2019')})",
            f"sin datos después de {max(k for k in avg_exp if k >= '2019')}", max(k for k in avg_exp if k >= "2019"),
            "QSD PDF, page titled 'Table 8.5 Tourism: Estimates of Visitor Expenditure'; column 'Average Annual Expenditure of Stopover Visitors – In Current Prices'."))
    add("spending_avg_stay", "spending", "Estadía promedio de visitantes stopover", "noches", "annual", stay,
        src("Bahamas Ministry of Tourism, Investments & Aviation – Research & Statistics Dept.",
            f"Stopover Visitors Average Length of Stay 1992-{max(stay)} (immigration cards)", STOP_PAGE,
            raw_url("alos.pdf"), "pdf", f"anual (último PDF publicado cubre hasta {max(stay)})",
            f"sin actualizaciones desde {max(stay)}", max(stay), "Download PDF; row per year, first value = All Bahamas nights."))
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
        "All MoT monthly figures are labelled preliminary and are revised within the current-year PDF; values here are from the latest PDF of each year "
        f"(current-year PDF retrieved {_retrieved(AS_CUR)}).",
        f"Stopover monthly values from the combined {so_span} PDF; each year's own page was used (current-year columns); regional sums and annual totals validated. "
        "2025 total there (1,838,168) differs from CBOB QSD Table 8.4 (1,826,103 / 1,821,076) — different vintages.",
        "Minor vintage differences vs CBOB QSD Table 8.4 annual totals: 2021 air arrivals (monthly sum 886,653 vs 886,629) and 2020 stopovers (440,594 vs 440,588); all other years 2019-2025 match exactly for air, cruise, total and stopover (except 2025 stopover, see above).",
        "April-May 2020: borders closed (COVID-19) — near-zero arrivals are genuine.",
        "spending_tourism_receipts: CBOB BoP travel credits equal MoT total visitor expenditure estimates (stopover+cruise+day). 2019-Q1 is missing: "
        f"the earliest BPM6-layout digest (Aug-2021) starts at 2019-Q2, so 2019-Q1 ({q_trav['2019-Q1']:,.1f}) is taken from the MoT 'Expenditure by Quarter 2020 and 2019' PDF, All Bahamas / All Visitors "
        "(MoT all-visitor expenditure matches CBOB travel credits within 0.2 on every overlapping quarter checked). "
        "Q2-2019..Q1-2021 come from the Aug-2021 QSD (header row mistyped there; quarter order verified against 2020 annual total); later quarters from the most recent digest containing them.",
        f"Quarterly receipts validated: 2020-{date.today().year - 1} quarters sum to CBOB annual totals within ±0.3 (rounding).",
        f"MoT stopover expenditure by quarter is published through {exp_last} (yearly 'Expenditure by Quarter' PDFs); avg expenditure per stopover "
        f"(CBOB Table 8.5) only through {max(k for k in avg_exp if k >= '2019')}; average length of stay PDF only through {max(stay)}.",
        "Hurricane Dorian (Sep-2019, Abaco & Grand Bahama), Tropical Storm Imelda (Sep-2025) and Hurricane Melissa (late Oct-2025) affected arrivals per MoT notes.",
        "FDI series is total economy (no tourism-sector split published by CBOB); kept as secondary context only.",
    ]
    doc = {"id": "BS", "name": "Bahamas", "type": "country", "lat": 25.03, "lon": -77.4, "series": series, "notes": notes}
    check_against_current(series)
    (ROOT / "BS.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1))
    for s in series:
        print(f"{s['key']:40s} {s['frequency']:9s} {len(s['data']):4d} {s['data'][0]} -> {s['data'][-1]}")


if __name__ == "__main__":
    main()
