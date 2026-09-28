"""Saint Lucia (LC): ECCB Selected Tourism Statistics (monthly CSV export) + CSO Saint Lucia annual ALOS.
Usage: python LC_extract.py [--no-download]
  By default downloads the ECCB CSVs (scripts/ECCB_fetch.py) and the CSO page on every run.
  --no-download reuses the files already in raw/. Any download/parse failure exits != 0
  without writing LC.json; so does a series that would lose periods vs the current LC.json.
"""
import re, io, csv, sys, json, html, datetime, pathlib
import pandas as pd, requests

WD = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WD / "scripts"))
RAW_E = WD / "raw" / "ECCB"
RAW = WD / "raw" / "LC"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
PAGE = "https://www.eccb-centralbank.org/statistics-category/external-sector/selected-tourism-statistics/m"
CSO = "https://stats.gov.lc/subjects/economy/tourism/selected-visitor-statistics-2012-to-2023/"
RETRIEVED = datetime.date.today().isoformat()  # date of this run's download
CSO_FILE = RAW / "cso_selected-visitor-statistics-2012-to-2023.html"


def download():
    import ECCB_fetch
    RAW.mkdir(parents=True, exist_ok=True)
    # Both ECCB files cover the current period (and revisions), so they are always re-downloaded.
    ECCB_fetch.fetch(7, "M", start="31/01/2019")
    ECCB_fetch.fetch(7, "A", start="31/12/2019")
    r = requests.get(CSO, headers={"User-Agent": UA}, timeout=60)
    r.raise_for_status()
    if "Average Length of Stay" not in r.text:
        raise RuntimeError(f"CSO page {CSO} no longer contains 'Average Length of Stay'")
    tmp = CSO_FILE.with_suffix(".part")
    tmp.write_bytes(r.content)
    tmp.replace(CSO_FILE)


def guard(doc, path):
    """Abort (exit != 0, JSON untouched) if a series present in the current JSON would be dropped,
    come out empty, have fewer periods, or lose any period it has now (even if others are added)."""
    if not path.exists():
        return
    old = {s["key"]: s["data"] for s in json.loads(path.read_text())["series"]}
    new = {s["key"]: s["data"] for s in doc["series"]}
    errs = []
    for k, od in old.items():
        nd = new.get(k)
        if not nd:
            errs.append(f"{k}: {'missing' if nd is None else 'empty'} (current JSON has {len(od)} periods)")
        else:
            lost = sorted({p for p, _ in od} - {p for p, _ in nd})
            if lost or len(nd) < len(od):
                errs.append(f"{k}: periods in current JSON missing from new series: {lost[:12]}"
                            f"{' ...' if len(lost) > 12 else ''} ({len(nd)} new vs {len(od)} current)")
    if errs:
        sys.exit(f"ABORT: {path.name} not written; series would lose periods:\n  " + "\n  ".join(errs))


def load(freq):
    """Parse an ECCB long-format CSV: metadata rows ('Country', 'Time frequency', 'Reference period')
    followed by the data header row 'Country','Indicator Label',... Returns (data, reference period)."""
    p = RAW_E / f"eccb_selected_tourism_{freq}_LC.csv"
    txt = open(p, encoding="utf-8").read()
    rows = list(csv.reader(io.StringIO(txt)))
    hdr = next((i for i, r in enumerate(rows) if r[:2] == ["Country", "Indicator Label"]), None)
    if hdr is None:
        raise RuntimeError(f"{p.name}: data header row 'Country','Indicator Label' not found")
    meta = {r[0].strip(): r[1].strip() for r in rows[:hdr] if len(r) > 1 and r[0].strip()}
    if meta.get("Country") != "Saint Lucia":
        raise RuntimeError(f"{p.name}: unexpected country {meta.get('Country')!r}")
    ref = meta.get("Reference period")
    if not ref:
        raise RuntimeError(f"{p.name}: no 'Reference period' in header; metadata = {meta}")
    d = pd.read_csv(io.StringIO(txt), skiprows=hdr, dtype=str)
    d["Indicator Label"] = d["Indicator Label"].str.strip()
    d["Amount"] = pd.to_numeric(d["Amount"].str.replace(",", ""), errors="coerce")
    return d.dropna(subset=["Amount"]), ref


def series(d, label, freq, as_int):
    s = d[d["Indicator Label"] == label].sort_values("Date")
    fmt = (lambda x: x[:7]) if freq == "M" else (lambda x: x[:4])
    return [[fmt(r.Date), int(round(r.Amount)) if as_int else round(float(r.Amount), 2)] for r in s.itertuples()]


def cso_alos():
    s = open(CSO_FILE, encoding="utf-8").read()
    t = re.findall(r"<table.*?</table>", s, flags=re.S)[0]
    rows = [[re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", c))).strip()
             for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, flags=re.S)]
            for r in re.findall(r"<tr.*?</tr>", t, flags=re.S)]
    years = [re.match(r"\d{4}", y).group(0) for y in rows[0][1:]]
    row = next(r for r in rows if r[0].startswith("Average Length of Stay"))
    return [[y, float(v)] for y, v in zip(years, row[1:]) if y >= "2019" and re.fullmatch(r"[\d.]+", v)]


def main():
    if "--no-download" not in sys.argv:
        download()
    m, ref_m = load("M")
    a, ref_a = load("A")
    arr = series(m, "Stay-Over Arrivals", "M", True)
    exp_m = series(m, "Total Visitor Expenditure (EC$M)", "M", False)
    exp_a = series(a, "Total Visitor Expenditure (EC$M)", "A", False)
    alos = cso_alos()
    eccb_src = lambda title, last: {
        "org": "Eastern Caribbean Central Bank (ECCB)", "title": title, "page_url": PAGE,
        "file_url": "https://www.eccb-centralbank.org/statistics/csv-export (POST; parámetros cifrados generados por el formulario)",
        "format": "csv", "update_frequency": "mensual",
        "release_lag": f"~2-3 meses (período de referencia publicado por ECCB: mensual '{ref_m}', anual '{ref_a}')",
        "last_period": last, "retrieved": RETRIEVED,
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
            f"Período de referencia de la descarga ECCB del {RETRIEVED}: mensual '{ref_m}', anual '{ref_a}'. Los meses posteriores no están publicados (ECCB no informa fecha 'as at' en el CSV).",
            "Estancia media: CSO no especifica unidad (se asume noches); serie anual detenida en 2023 (2020 = 8.8 pese a pandemia). ECCB no publica estancia media ni gasto medio por visitante.",
            "Gasto medio por visitante: sin serie oficial publicada (no se calcula dividiendo series).",
            "CSO Santa Lucía publica también llegadas mensuales 2010-2023 (tabla HTML), sin actualizar desde mayo 2024.",
        ],
    }
    guard(doc, WD / "LC.json")
    (WD / "LC.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1))
    for s in doc["series"]:
        print(s["key"], len(s["data"]), s["data"][0], s["data"][-1])


if __name__ == "__main__":
    main()
