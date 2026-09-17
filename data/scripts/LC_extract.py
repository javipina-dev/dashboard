"""Saint Lucia (LC): ECCB Selected Tourism Statistics (monthly CSV export) + CSO Saint Lucia annual ALOS.
Usage: python LC_extract.py [--download]   (download uses scripts/ECCB_fetch.py)
"""
import re, sys, json, html, pathlib
import pandas as pd, requests

WD = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WD / "scripts"))
RAW_E = WD / "raw" / "ECCB"
RAW = WD / "raw" / "LC"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
PAGE = "https://www.eccb-centralbank.org/statistics-category/external-sector/selected-tourism-statistics/m"
CSO = "https://stats.gov.lc/subjects/economy/tourism/selected-visitor-statistics-2012-to-2023/"
RETRIEVED = "2026-09-17"


def load(freq):
    p = RAW_E / f"eccb_selected_tourism_{freq}_LC.csv"
    meta = open(p, encoding="utf-8").read().splitlines()[:8]
    asof = next(l for l in meta if l.startswith('"Data as at"')).split('","')[1].strip('"')
    d = pd.read_csv(p, skiprows=8, dtype=str)
    d["Indicator Label"] = d["Indicator Label"].str.strip()
    d["Amount"] = pd.to_numeric(d["Amount"].str.replace(",", ""), errors="coerce")
    return d.dropna(subset=["Amount"]), asof


def series(d, label, freq, as_int):
    s = d[d["Indicator Label"] == label].sort_values("Date")
    fmt = (lambda x: x[:7]) if freq == "M" else (lambda x: x[:4])
    return [[fmt(r.Date), int(round(r.Amount)) if as_int else round(float(r.Amount), 2)] for r in s.itertuples()]


def cso_alos():
    p = RAW / "cso_selected-visitor-statistics-2012-to-2023.html"
    s = open(p, encoding="utf-8").read()
    t = re.findall(r"<table.*?</table>", s, flags=re.S)[0]
    rows = [[re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", c))).strip()
             for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, flags=re.S)]
            for r in re.findall(r"<tr.*?</tr>", t, flags=re.S)]
    years = [re.match(r"\d{4}", y).group(0) for y in rows[0][1:]]
    row = next(r for r in rows if r[0].startswith("Average Length of Stay"))
    return [[y, float(v)] for y, v in zip(years, row[1:]) if y >= "2019" and re.fullmatch(r"[\d.]+", v)]


def main():
    if "--download" in sys.argv:
        import ECCB_fetch
        ECCB_fetch.fetch(7, "M", start="31/01/2019")
        ECCB_fetch.fetch(7, "A", start="31/12/2019")
        r = requests.get(CSO, headers={"User-Agent": UA}, timeout=60)
        (RAW / "cso_selected-visitor-statistics-2012-to-2023.html").write_bytes(r.content)
    m, asof = load("M")
    a, _ = load("A")
    arr = series(m, "Stay-Over Arrivals", "M", True)
    exp_m = series(m, "Total Visitor Expenditure (EC$M)", "M", False)
    exp_a = series(a, "Total Visitor Expenditure (EC$M)", "A", False)
    alos = cso_alos()
    eccb_src = lambda title, last: {
        "org": "Eastern Caribbean Central Bank (ECCB)", "title": title, "page_url": PAGE,
        "file_url": "https://www.eccb-centralbank.org/statistics/csv-export (POST; parámetros cifrados generados por el formulario)",
        "format": "csv", "update_frequency": "mensual",
        "release_lag": f"~2-3 meses (datos 'as at {asof}': último jun-2026)", "last_period": last, "retrieved": RETRIEVED,
        "access_method": "Sesión requests: GET página (cookie + _token CSRF) -> POST formulario (country_code[]=7, frequency=M|A, start_date/end_date dd/mm/yyyy) -> POST campos ocultos de #frmCsvDownload con format=csv a /statistics/csv-export. Ver scripts/ECCB_fetch.py."}
    doc = {
        "id": "LC", "name": "Santa Lucía", "type": "country", "lat": 14.01, "lon": -60.99,
        "series": [
            {"key": "arrivals_stopover", "category": "arrivals", "label": "Llegadas de turistas stopover",
             "unit": "personas", "frequency": "monthly", "data": arr,
             "source": eccb_src("Selected Tourism Statistics – Saint Lucia – Stay-Over Arrivals", arr[-1][0])},
            {"key": "spending_tourism_receipts", "category": "spending", "label": "Gasto total de visitantes",
             "unit": "EC$ millones (paridad fija 2.70 EC$ = 1 US$)", "frequency": "monthly", "data": exp_m,
             "source": eccb_src("Selected Tourism Statistics – Saint Lucia – Total Visitor Expenditure (EC$M)", exp_m[-1][0])},
            {"key": "spending_tourism_receipts_annual", "category": "spending", "label": "Gasto total de visitantes (anual)",
             "unit": "EC$ millones (paridad fija 2.70 EC$ = 1 US$)", "frequency": "annual", "data": exp_a,
             "source": eccb_src("Selected Tourism Statistics – Saint Lucia – Total Visitor Expenditure (EC$M), frecuencia anual", exp_a[-1][0])},
            {"key": "spending_avg_stay", "category": "spending", "label": "Estancia media de turistas stopover",
             "unit": "noches", "frequency": "annual", "data": alos,
             "source": {"org": "Central Statistical Office of Saint Lucia (CSO)",
                        "title": "Selected Visitor Statistics, 2012 to 2023 – 'Average Length of Stay'",
                        "page_url": "https://stats.gov.lc/subjects/economy/tourism/", "file_url": CSO,
                        "format": "html", "update_frequency": "irregular (última actualización 16/05/2024)",
                        "release_lag": ">2 años a sep-2026 (último dato 2023)", "last_period": alos[-1][0],
                        "retrieved": RETRIEVED,
                        "access_method": "GET página WordPress y parseo de <table>, fila 'Average Length of Stay'."}},
        ],
        "notes": [
            "Fuente principal: ECCB (compila datos de la Saint Lucia Tourism Authority / Ministerio de Turismo). Coincide con la CSO de Santa Lucía para 2019-2023 salvo diferencias menores (jul-2019: ECCB 42,773 vs CSO 42,778).",
            "Abr-jun 2020 = 0 llegadas y 0 gasto (cierre de fronteras COVID): valores publicados, no huecos.",
            "El gasto de ECCB es 'Total Visitor Expenditure' (todos los visitantes: stay-over, crucero, yates, excursionistas); es una estimación oficial, no el crédito 'Travel' de la balanza de pagos. Los totales anuales ECCB (p.ej. 2019: 2,696.15) difieren de la serie CSO/SLTA 'Tourist Expenditure' (2019: 2,604.5).",
            "Julio-agosto 2026 aparecen como '---' (no publicados) en ECCB a sep-2026.",
            "Estancia media: CSO no especifica unidad (se asume noches); serie anual detenida en 2023 (2020 = 8.8 pese a pandemia). ECCB no publica estancia media ni gasto medio por visitante.",
            "Gasto medio por visitante: sin serie oficial publicada (no se calcula dividiendo series).",
            "CSO Santa Lucía publica también llegadas mensuales 2010-2023 (tabla HTML), sin actualizar desde mayo 2024.",
        ],
    }
    (WD / "LC.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1))
    for s in doc["series"]:
        print(s["key"], len(s["data"]), s["data"][0], s["data"][-1])


if __name__ == "__main__":
    main()
