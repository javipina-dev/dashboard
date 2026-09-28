"""Barbados (BB): stay-over arrivals (BSS HTML tables), intended avg length of stay (BSS monthly
bulletin PDFs), travel credits (Central Bank of Barbados Economic Review PDF appendix).
Usage: python BB_extract.py [--no-download]
  By default downloads on every run: the BSS HTML tables (always), the BSS monthly bulletins
  VAYYYYMM.pdf linked from the BSS index page (2023+; cached once downloaded, re-downloaded if
  BSS links a different file), and the latest CBB 'Review of Barbados' Economy' annual and
  January-June PDFs located from the CBB economic-reviews page (cached by file name).
  --no-download reuses the files already in raw/. Any download/parse failure exits != 0
  without writing BB.json; so does a series that would lose periods vs the current BB.json.
"""
import re, sys, json, glob, html, datetime, pathlib, warnings
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
BULLETINS = RAW / "bulletins"
BULLETIN_SOURCES = BULLETINS / "_sources.json"  # file name -> URL it was downloaded from
BULLETIN_FROM = "2023"  # avg_stay() reads 2023 onwards
CBB_REVIEWS = "https://www.centralbank.org.bb/news/economic-reviews"
REVIEWS_FILE = RAW / "_reviews.json"  # which CBB Review PDFs the last download located
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september",
          "october", "november", "december"]
RETRIEVED = datetime.date.today().isoformat()  # date of this run's download


def fetch(url, timeout=120):
    r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
    r.raise_for_status()
    return r


def save(content, dest):
    dest = pathlib.Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    tmp.write_bytes(content)
    tmp.replace(dest)


def pdf_ok(path):
    try:
        if pathlib.Path(path).read_bytes()[:5] != b"%PDF-":
            return False
        with pdfplumber.open(path) as p:
            return len(p.pages) > 0
    except Exception:
        return False


def get_pdf(url, dest):
    r = fetch(url)
    if not r.content.startswith(b"%PDF-"):
        raise RuntimeError(f"{url} did not return a PDF (content-type {r.headers.get('content-type')!r})")
    save(r.content, dest)
    if not pdf_ok(dest):
        raise RuntimeError(f"{url} downloaded but pdfplumber cannot open it")


def download_bss():
    """BSS HTML tables (always re-downloaded: current year fills in and BSS revises past years)
    and the monthly bulletins linked from the index page (historical PDFs cached)."""
    idx = fetch(BSS + "/").text
    for slug in BSS_PAGES:
        if f"/{slug}/" not in idx:
            raise RuntimeError(f"BSS index {BSS}/ no longer links page '{slug}'")
        r = fetch(f"{BSS}/{slug}/")
        if "<table" not in r.text:
            raise RuntimeError(f"BSS page {slug} has no <table>")
        save(r.content, RAW / f"bss_{slug}.html")
    newer = sorted({int(y) for y in re.findall(r"visitor-market-by-month-(20\d\d)", idx)} - set(BSS_PAGES.values()))
    newer = [y for y in newer if y > max(v for v in BSS_PAGES.values() if isinstance(v, int))]
    if newer:  # not read yet: needs a new BSS_PAGES entry (slug checked by hand, may carry '-2')
        print(f"WARNING: BSS index links visitor-market-by-month pages for {newer}, not in BSS_PAGES", file=sys.stderr)

    links = {}
    for u in re.findall(r'href="(https?://stats\.gov\.bb/wp-content/uploads/[^"]*/(VA(\d{6})\.pdf))"', idx):
        url, name, ym = u
        if ym[:4] >= BULLETIN_FROM:
            if name in links and links[name] != url:
                raise RuntimeError(f"BSS index links two different files for {name}: {links[name]} / {url}")
            links[name] = url
    if not links:
        raise RuntimeError(f"no VAYYYYMM.pdf bulletin links found on {BSS}/")
    BULLETINS.mkdir(parents=True, exist_ok=True)
    src = json.loads(BULLETIN_SOURCES.read_text()) if BULLETIN_SOURCES.exists() else {}
    got = 0
    for name, url in sorted(links.items()):
        dest = BULLETINS / name
        if src.get(name) == url and pdf_ok(dest):
            continue  # already have this exact published file
        get_pdf(url, dest)
        src[name] = url
        got += 1
        BULLETIN_SOURCES.write_text(json.dumps(src, indent=1, sort_keys=True))
    print(f"BSS bulletins: {len(links)} linked (>= {BULLETIN_FROM}), {got} downloaded, {len(links) - got} cached")
    return links


def review_pdf(page_url):
    """The Review PDF linked from a CBB economic-review page (excludes media presentations)."""
    t = fetch(page_url).text
    c = sorted({u for u in re.findall(r'href="(https://cdn\.centralbank\.org\.bb/documents/[^"]+\.pdf)"', t)
                if re.search(r"review", u, re.I) and not re.search(r"presentation|media", u, re.I)})
    if len(c) != 1:
        raise RuntimeError(f"expected 1 Review PDF on {page_url}, found {c}")
    return c[0]


def download_cbb():
    """Locate the latest annual ('... economy in YYYY') and January-June Reviews on the CBB
    economic-reviews page and download their PDFs (cached by file name: a new review = new name)."""
    t = fetch(CBB_REVIEWS).text
    pages = set(re.findall(r'href="(https://www\.centralbank\.org\.bb/news/economic-reviews/[a-z0-9-]+)"', t))
    fy = {int(m.group(1)): u for u in pages if (m := re.search(r"economy-in-(\d{4})$", u))}
    h1 = {int(m.group(1)): u for u in pages if (m := re.search(r"january-june-(\d{4})$", u))}
    if not fy or not h1:
        raise RuntimeError(f"annual / January-June reviews not found on {CBB_REVIEWS}")
    out = {}
    for kind, d in (("FY", fy), ("H1", h1)):
        year = max(d)
        pdf = review_pdf(d[year])
        dest = RAW / pathlib.Path(pdf).name
        if not pdf_ok(dest):
            get_pdf(pdf, dest)
        out[kind] = {"year": year, "page_url": d[year], "pdf_url": pdf}
    REVIEWS_FILE.write_text(json.dumps(out, indent=1))
    print("CBB reviews:", {k: (v["year"], pathlib.Path(v["pdf_url"]).name) for k, v in out.items()})
    return out


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
    files = sorted(glob.glob(str(BULLETINS / "VA20[2-9][0-9][01][0-9].pdf")))
    files = [f for f in files if re.search(r"VA(\d{4})", f).group(1) >= BULLETIN_FROM]
    if not files:
        raise RuntimeError(f"no bulletins VAYYYYMM.pdf (>= {BULLETIN_FROM}) in {BULLETINS}")
    for f in files:
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
        if key not in out:
            raise RuntimeError(f"{pathlib.Path(f).name}: table 'Intended Length of Stay' / 'Average' row not found")
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


def reviews():
    if not REVIEWS_FILE.exists():
        raise RuntimeError(f"{REVIEWS_FILE} missing: run without --no-download first")
    return json.loads(REVIEWS_FILE.read_text())


def travel_credits(label="o/w: Travel"):
    rv = reviews()
    fy = bop_row(str(RAW / pathlib.Path(rv["FY"]["pdf_url"]).name), label)
    h1 = bop_row(str(RAW / pathlib.Path(rv["H1"]["pdf_url"]).name), label)
    # latest vintage wins: the review 'in Y' is published ~Jan Y+1, the 'January-June Y' one ~Jul Y
    h1_annual = {k: v for k, v in h1.items() if not k.startswith("Jun")}
    if rv["FY"]["year"] >= rv["H1"]["year"]:
        annual = {**h1_annual, **fy}
    else:
        annual = {**fy, **h1_annual}
    annual_series = [[k, annual[k]] for k in sorted(annual) if k >= "2019"]
    h1_series = [[k.replace("Jun ", "") + "-H1", v] for k, v in sorted(h1.items()) if k.startswith("Jun")]
    return annual_series, h1_series, fy, h1


def main():
    if "--no-download" not in sys.argv:
        RAW.mkdir(parents=True, exist_ok=True)
        download_bss()
        download_cbb()
    rv = reviews()
    REVIEW_FY, REVIEW_H1 = rv["FY"]["pdf_url"], rv["H1"]["pdf_url"]
    src = json.loads(BULLETIN_SOURCES.read_text()) if BULLETIN_SOURCES.exists() else {}
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
                        "file_url": f"{BSS}/{list(BSS_PAGES)[-1]}/",
                        "format": "html", "update_frequency": "mensual (tabla HTML anual que se va completando)",
                        "release_lag": f"~3-5 meses (descarga {RETRIEVED}: último dato {arr[-1][0]})",
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
                        "release_lag": f"~1 mes tras cierre del periodo (reviews usadas: anual {rv['FY']['year']}, ene-jun {rv['H1']['year']})",
                        "last_period": tc[-1][0], "retrieved": RETRIEVED,
                        "access_method": f"Descargar PDF del Economic Review más reciente (enlace cdn.centralbank.org.bb en la página de la review) y leer tabla del apéndice con pdfplumber; años ausentes de la review ene-jun se toman de la review anual {rv['FY']['year']}; review más reciente prevalece. Reviews localizadas en " + CBB_REVIEWS + ". Cifras 2022-2025 marcadas (e) estimadas."}},
            {"key": "spending_tourism_receipts_h1", "category": "spending",
             "label": "Ingresos por viajes, enero-junio (acumulado)",
             "unit": "BDS$ millones (paridad fija 2 BDS$ = 1 US$)", "frequency": "semiannual", "data": tc_h1,
             "source": {"org": "Central Bank of Barbados (CBB)",
                        "title": f"Review of Barbados' Economy January to June {rv['H1']['year']} – Appendix 3 Balance of Payments",
                        "page_url": rv["H1"]["page_url"],
                        "file_url": REVIEW_H1, "format": "pdf", "update_frequency": "trimestral (acumulado del año)",
                        "release_lag": "~1 mes", "last_period": tc_h1[-1][0], "retrieved": RETRIEVED,
                        "access_method": "Igual que la serie anual; columnas 'Jun YYYY(e)'."}},
            {"key": "spending_avg_stay", "category": "spending",
             "label": "Estancia media prevista de turistas stopover",
             "unit": "días (duración de estancia declarada/prevista)", "frequency": "monthly", "data": stay,
             "source": {"org": "Barbados Statistical Service (BSS)",
                        "title": "Visitor Arrivals Monthly Statistical Bulletin – Table 5 'Stay-Over Visitor Arrivals by Intended Length of Stay' (fila 'Average')",
                        "page_url": BSS + "/",
                        "file_url": src.get(f"VA{stay[-1][0].replace('-', '')}.pdf") if stay else None, "format": "pdf",
                        "update_frequency": "mensual (boletín PDF)",
                        "release_lag": f"~2-3 meses (dic-2025 subido mar-2026); a {RETRIEVED} el último boletín enlazado por BSS es {stay[-1][0] if stay else '-'}",
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
    guard(doc, WD / "BB.json")
    (WD / "BB.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1))
    print("arrivals", arr[0], arr[-1], len(arr))
    print("travel", tc, tc_h1)
    print("fdi", fdi, fdi_h1)
    print("stay", stay[:2], stay[-2:], len(stay))


if __name__ == "__main__":
    main()
