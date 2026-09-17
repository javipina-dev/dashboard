"""Barbados (BB): stay-over arrivals (BSS HTML tables), intended avg length of stay (BSS monthly
bulletin PDFs), travel credits (Central Bank of Barbados Economic Review PDF appendix).
Usage: python BB_extract.py [--download]
"""
import re, sys, json, glob, html, pathlib, warnings
import requests, pdfplumber

warnings.filterwarnings("ignore")
WD = pathlib.Path(__file__).resolve().parents[1]
RAW = WD / "raw" / "BB"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
BSS = "https://stats.gov.bb/subjects/social-demographic-statistics/visitor-arrivals-statistics"
BSS_PAGES = {
    "stay-over-tourist-arrivals-2015-2022": "wide",  # Month x Year table (2015-2022)
    "stay-over-tourist-arrivals-visitor-market-by-month-2022": 2022,
    "visitor-arrival-statistics_stay-over-tourist-arrivals-visitor-market-by-month-2023": 2023,
    "visitor-arrival-statistics_stay-over-tourist-arrivals-visitor-market-by-month-2024": 2024,
    "visitor-arrival-statistics_stay-over-tourist-arrivals-visitor-market-by-month-2025": 2025,
    "visitor-arrival-statistics_stay-over-tourist-arrivals-visitor-market-by-month-2026": 2026,
}
REVIEW_FY = "https://cdn.centralbank.org.bb/documents/2026-01-30-13-30-10-Review-of-the-Barbados-Economy-in-2025.pdf"
REVIEW_H1 = "https://cdn.centralbank.org.bb/documents/2026-07-31-14-01-23-Central-Bank-of-Barbados-Review-of-Barbados-Economy-January-to-June-2026.pdf"
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september",
          "october", "november", "december"]
RETRIEVED = "2026-09-17"


def get(url, dest):
    r = requests.get(url, headers={"User-Agent": UA}, timeout=120)
    r.raise_for_status()
    pathlib.Path(dest).write_bytes(r.content)


def html_tables(path):
    s = open(path, encoding="utf-8", errors="ignore").read()
    out = []
    for t in re.findall(r"<table.*?</table>", s, flags=re.S):
        rows = []
        for r in re.findall(r"<tr.*?</tr>", t, flags=re.S):
            rows.append([re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", c))).strip()
                         for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, flags=re.S)])
        out.append(rows)
    return out


def num(x):
    x = re.sub(r"\(R\)|\(P\)", "", x).replace(",", "").strip()
    return int(x) if re.fullmatch(r"\d+", x) else None


def arrivals():
    data = {}
    for slug, kind in BSS_PAGES.items():
        rows = html_tables(RAW / f"bss_{slug}.html")[0]
        if kind == "wide":
            hdr = next(r for r in rows if r and r[0] == "Month")
            years = [int(y) for y in hdr[1:]]
            for r in rows:
                if r and r[0].lower() in MONTHS:
                    m = MONTHS.index(r[0].lower()) + 1
                    for y, v in zip(years, r[1:]):
                        if num(v) is not None:
                            data[f"{y}-{m:02d}"] = num(v)
        else:
            hdr = next(r for r in rows if r and r[0] == "Month")
            ti = hdr.index("Total")
            for r in rows:
                if r and r[0].lower() in MONTHS and len(r) > ti and num(r[ti]) is not None:
                    data[f"{kind}-{MONTHS.index(r[0].lower()) + 1:02d}"] = num(r[ti])  # later vintages overwrite
    return [[k, data[k]] for k in sorted(data) if k >= "2019-01"]


def avg_stay():
    """Table 5 'Stay-Over Visitor Arrivals by Intended Length of Stay' (current month column), 2023-2025."""
    out = {}
    for f in sorted(glob.glob(str(RAW / "bulletins" / "VA202[3-9][01][0-9].pdf"))):
        ym = re.search(r"VA(\d{4})(\d{2})", f)
        key = f"{ym.group(1)}-{ym.group(2)}"
        with pdfplumber.open(f) as p:
            for i, pg in enumerate(p.pages):
                t = pg.extract_text() or ""
                hit = None
                for m in re.finditer(r"Intended Length of Stay", t):
                    if "...." in t[m.start():m.start() + 120]:
                        continue  # table of contents
                    seg = t[m.start():m.start() + 800]
                    a = re.search(r"\nAverage\s+([\d.]+)\s+([\d.]+)", seg)
                    if a:
                        hit = float(a.group(2))
                        break
                if hit is not None:
                    out[key] = hit
                    break
    return [[k, out[k]] for k in sorted(out)]


def bop_row(pdf, label):
    """Read one row of the CBB Economic Review appendix 'Balance of Payments (BDS$ Millions)'."""
    with pdfplumber.open(pdf) as p:
        for pg in p.pages:
            t = pg.extract_text() or ""
            if "Balance of Payments (BDS$ Millions)" in t and label in t:
                lines = t.splitlines()
                hdr = next(l for l in lines if re.match(r"^\s*20\d\d\s", l))
                cols = re.findall(r"(Jun 20\d\d|20\d\d)", hdr)
                vals = re.findall(r"[\d,]+\.\d", next(l for l in lines if l.strip().startswith(label)))
                return dict(zip(cols, [float(v.replace(",", "")) for v in vals]))
    raise RuntimeError(label + " row not found in " + pdf)


def travel_credits(label="o/w: Travel"):
    fy = bop_row(str(RAW / pathlib.Path(REVIEW_FY).name), label)
    h1 = bop_row(str(RAW / pathlib.Path(REVIEW_H1).name), label)
    annual = dict(fy)
    annual.update({k: v for k, v in h1.items() if not k.startswith("Jun")})  # latest vintage wins
    annual_series = [[k, annual[k]] for k in sorted(annual) if k >= "2019"]
    h1_series = [[k.replace("Jun ", "") + "-H1", v] for k, v in sorted(h1.items()) if k.startswith("Jun")]
    return annual_series, h1_series, fy, h1


def main():
    if "--download" in sys.argv:
        for slug in BSS_PAGES:
            get(f"{BSS}/{slug}/", RAW / f"bss_{slug}.html")
        for u in (REVIEW_FY, REVIEW_H1):
            get(u, RAW / pathlib.Path(u).name)
    arr = arrivals()
    stay = avg_stay()
    tc, tc_h1, fy, h1 = travel_credits()
    fdi, fdi_h1, _, _ = travel_credits("Net Foreign Direct Investment")
    doc = {
        "id": "BB", "name": "Barbados", "type": "country", "lat": 13.10, "lon": -59.62,
        "series": [
            {"key": "arrivals_stopover", "category": "arrivals", "label": "Llegadas de turistas stopover",
             "unit": "personas", "frequency": "monthly", "data": arr,
             "source": {"org": "Barbados Statistical Service (BSS)",
                        "title": "Stay-Over Tourist Arrivals (2015-2022) y Stay Over Tourist Arrivals: Visitor Market by Month (2022-2026)",
                        "page_url": BSS + "/",
                        "file_url": BSS + "/visitor-arrival-statistics_stay-over-tourist-arrivals-visitor-market-by-month-2026/",
                        "format": "html", "update_frequency": "mensual (tabla HTML anual que se va completando)",
                        "release_lag": "~3-5 meses (sep-2026: último dato abr-2026)",
                        "last_period": arr[-1][0], "retrieved": RETRIEVED,
                        "access_method": "GET de las páginas WordPress por año (slug ...visitor-market-by-month-YYYY) y parseo de <table>; columna 'Total'; limpiar '(R)' y comas. Slugs no siempre predecibles (p.ej. '-2025-2'): descubrir desde la página índice."}},
            {"key": "spending_tourism_receipts", "category": "spending",
             "label": "Ingresos por viajes (créditos de 'Travel' en balanza de pagos)",
             "unit": "BDS$ millones (paridad fija 2 BDS$ = 1 US$)", "frequency": "annual", "data": tc,
             "source": {"org": "Central Bank of Barbados (CBB)",
                        "title": "Review of Barbados' Economy – Appendix 'Balance of Payments (BDS$ Millions)', fila 'o/w: Travel'",
                        "page_url": "https://www.centralbank.org.bb/news/quarterly-economic-release",
                        "file_url": REVIEW_H1, "format": "pdf",
                        "update_frequency": "trimestral (reviews acumuladas ene-mar, ene-jun, ene-sep, anual)",
                        "release_lag": "~1 mes tras cierre del periodo (review ene-jun 2026 publicada 31-jul-2026)",
                        "last_period": tc[-1][0], "retrieved": RETRIEVED,
                        "access_method": "Descargar PDF del Economic Review más reciente (enlace cdn.centralbank.org.bb en la página de la review) y leer tabla del apéndice con pdfplumber; 2019 tomado de la review anual 2025 (ene-2026). Cifras 2022-2025 marcadas (e) estimadas."}},
            {"key": "spending_tourism_receipts_h1", "category": "spending",
             "label": "Ingresos por viajes, enero-junio (acumulado)",
             "unit": "BDS$ millones (paridad fija 2 BDS$ = 1 US$)", "frequency": "semiannual", "data": tc_h1,
             "source": {"org": "Central Bank of Barbados (CBB)",
                        "title": "Review of Barbados' Economy January to June 2026 – Appendix 3 Balance of Payments",
                        "page_url": "https://www.centralbank.org.bb/news/economic-reviews/economic-review-january-june-2026",
                        "file_url": REVIEW_H1, "format": "pdf", "update_frequency": "trimestral (acumulado del año)",
                        "release_lag": "~1 mes", "last_period": tc_h1[-1][0], "retrieved": RETRIEVED,
                        "access_method": "Igual que la serie anual; columnas 'Jun YYYY(e)'."}},
            {"key": "spending_avg_stay", "category": "spending",
             "label": "Estancia media prevista de turistas stopover",
             "unit": "días (duración de estancia declarada/prevista)", "frequency": "monthly", "data": stay,
             "source": {"org": "Barbados Statistical Service (BSS)",
                        "title": "Visitor Arrivals Monthly Statistical Bulletin – Table 5 'Stay-Over Visitor Arrivals by Intended Length of Stay' (fila 'Average')",
                        "page_url": BSS + "/",
                        "file_url": "https://stats.gov.bb/wp-content/uploads/2026/03/VA202512.pdf", "format": "pdf",
                        "update_frequency": "mensual (boletín PDF)",
                        "release_lag": "~2-3 meses (dic-2025 subido mar-2026); boletines 2026 aún no enlazados a sep-2026",
                        "last_period": stay[-1][0] if stay else None, "retrieved": RETRIEVED,
                        "access_method": "Enlaces VAYYYYMM.pdf en la página índice de BSS; pdfplumber, buscar 'Intended Length of Stay' fuera del índice y leer la línea 'Average' (2ª cifra = mes actual)."}},
            {"key": "investment_fdi_net", "category": "investment",
             "label": "Inversión extranjera directa neta (total economía)",
             "unit": "BDS$ millones (paridad fija 2 BDS$ = 1 US$)", "frequency": "annual", "data": fdi,
             "source": {"org": "Central Bank of Barbados (CBB)",
                        "title": "Review of Barbados' Economy – Appendix 'Balance of Payments (BDS$ Millions)', fila 'Net Foreign Direct Investment'",
                        "page_url": "https://www.centralbank.org.bb/news/quarterly-economic-release",
                        "file_url": REVIEW_H1, "format": "pdf", "update_frequency": "trimestral (acumulado)",
                        "release_lag": "~1 mes", "last_period": fdi[-1][0], "retrieved": RETRIEVED,
                        "access_method": "Igual que spending_tourism_receipts (misma tabla del apéndice)."}},
        ],
        "notes": [
            "Llegadas: se usa la última versión publicada por BSS en HTML. 2022 y ene-mar 2023 fueron revisadas al alza por BSS (nota de revisión 2023, reconciliación tras eliminar la tarjeta ED física); dic-2024 a mar-2025 revisadas a la baja (nota de revisión jun-2026, fuente alternativa por fallas IT de Inmigración).",
            "Oct-dic 2019 NO están publicados en la versión vigente (celdas vacías en BSS y en el xlsx H1 del CBB desde 2022); una versión anterior del CBB (H1 dic-2020) mostraba cifras provisionales 52,400 / 63,311 / 76,354 luego retiradas. Se dejan fuera.",
            "Abr-jun 2020 llegadas casi nulas por cierre de fronteras (136, 346, 494): datos reales, no huecos.",
            "Discrepancia de vintages: el boletín BSS dic-2025 (pre-revisión) y el Ministerio de Turismo citan 727,310-729,310 llegadas en 2025; la tabla BSS revisada suma 707,046.",
            "CBB publica también xlsx 'Table H1 – Long-stay arrivals by country & cruise' (cdn.centralbank.org.bb, p.ej. 2025-09-16-...H1LongStayCruiseJune2025.xlsx), pero con rezago mayor (último jun-2025) y vintages sin revisar; fuente original es BSS.",
            "Ingresos por viajes: CBB marca 2022-2025 como estimados (e) y revisa entre publicaciones (2025: 2,709.3 en review ene-2026 vs 2,739.5 en review jul-2026; se usa la más reciente). Moneda BDS$; US$ = BDS$/2 por paridad fija.",
            "Estancia media: 'intended length of stay' en DÍAS declarada por el visitante (no noches hoteleras). Dic-2024 a mar-2025 provienen de boletines originales anteriores a la revisión de llegadas. CBB cita otra métrica (7.6→7.3 noches, H1 2025→H1 2026) no publicada como serie.",
            "Gasto medio por visitante: no hay serie oficial publicada periódicamente por BSS/CBB/BTMI (no se calcula).",
            "Inversión: 'Net Foreign Direct Investment' del CBB es total economía, sin desglose turístico (CBB solo menciona cualitativamente inflows 'for tourism-related projects'). H1: 2025 = 367.3, 2026 = 338.3 BDS$ M.",
        ],
    }
    (WD / "BB.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1))
    print("arrivals", arr[0], arr[-1], len(arr))
    print("travel", tc, tc_h1)
    print("fdi", fdi, fdi_h1)
    print("stay", stay[:2], stay[-2:], len(stay))


if __name__ == "__main__":
    main()
