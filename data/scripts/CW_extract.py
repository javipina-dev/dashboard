"""Curaçao (CW): monthly stay-over arrivals from Curaçao Tourist Board (CTB) monthly
'Stayover Visitor Arrivals' PDFs (https://www.curacaotouristboard.com/monthly-statistics/).
Each PDF page 1 has 'Total Stayover Arrivals by Region' -> Total row: month, same month prior year, YTD, prior YTD.
Values for month m of year Y are taken from the (Y+1) report's prior-year column when available (revised),
otherwise from the Y report itself."""
import os, re, json, glob, requests, urllib3
import pdfplumber
urllib3.disable_warnings()
BASE = os.path.join(os.path.dirname(__file__), "..")
RAW = os.path.join(BASE, "raw", "CW")
PAGE = "https://www.curacaotouristboard.com/monthly-statistics/"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"}
MON = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
# PDFs without a readable period header (identified by upload folder + YTD consistency check)
MANUAL = {"Visitor-Arrivals-V01_final.pdf": "2025-10"}

def download():
    html = requests.get(PAGE, headers=UA, timeout=60, verify=False).text
    open(os.path.join(RAW, "ctb_monthly_statistics.html"), "w").write(html)
    urls = sorted(set(re.findall(r'(?:href|src)="([^"]+\.pdf)"', html)))
    urls = [u for u in urls if re.search(r"/20(19|2\d)/", u) or re.search(r"2019", u)]
    urls = [u for u in urls if not re.search(r"State|Annual|annual|TOURISM-PERFORMANCE-2025|CTB-Performance|MAR18|first_6|Quarter|FirstHalf|9_months", u)]
    os.makedirs(os.path.join(RAW, "monthly"), exist_ok=True)
    out = {}
    for u in urls:
        fn = os.path.join(RAW, "monthly", os.path.basename(u))
        if not os.path.exists(fn):
            open(fn, "wb").write(requests.get(u.replace("http://", "https://"), headers=UA, timeout=60, verify=False).content)
        out[fn] = u.replace("http://", "https://")
    return out

def nums(line):
    line = re.sub(r"-?[\d,]+(\.\d+)?\s*%", " ", line)
    return [int(x.replace(",", "")) for x in re.findall(r"\b\d{1,3}(?:,\d{3})*\b", line)]

def parse(fn):
    with pdfplumber.open(fn) as pdf:
        tx = pdf.pages[0].extract_text() or ""
    if not tx.strip(): return None
    m = re.search(r"\(\s*([A-Za-z]{3})[a-z]*\s*/\s*(\d{4})\s*\)", tx)
    period = f"{m.group(2)}-{MON[m.group(1).lower()]:02d}" if m else MANUAL.get(os.path.basename(fn))
    if not period: return None
    sec = tx.split("Total Stayover Arrivals by Region", 1)[1]
    row = next(l for l in sec.splitlines() if l.startswith("Total "))
    n = nums(row[len("Total "):])
    return {"period": period, "cur": n[0], "prev": n[1], "ytd": n[2], "prev_ytd": n[3]}

def main():
    files = download()
    recs = {}
    for fn, url in files.items():
        r = parse(fn)
        if r: r["url"] = url; recs[r["period"]] = r
    val, src = {}, {}
    for p, r in sorted(recs.items()):
        val.setdefault(p, r["cur"]); src.setdefault(p, r["url"])
    for p, r in sorted(recs.items()):            # override with revised prior-year column
        y, m = p.split("-"); py = f"{int(y)-1}-{m}"
        if py >= "2019-01": val[py] = r["prev"]; src[py] = r["url"]
    # consistency check: YTD(m) - YTD(m-1) vs monthly value, per report year
    issues = []
    for p, r in recs.items():
        y, m = map(int, p.split("-"))
        if y >= 2019 and m > 1 and f"{y}-{m-1:02d}" in recs:
            d = r["ytd"] - recs[f"{y}-{m-1:02d}"]["ytd"]
            if abs(d - r["cur"]) > max(50, 0.01 * r["cur"]): issues.append((p, r["cur"], d))
    data = [[k, val[k]] for k in sorted(val) if k >= "2019-01"]
    last = data[-1][0]
    doc = {"id": "CW", "name": "Curaçao", "type": "country", "lat": 12.12, "lon": -68.93,
           "series": [{"key": "arrivals_stopover", "category": "arrivals", "label": "Llegadas de turistas stopover",
                       "unit": "personas", "frequency": "monthly", "data": data,
                       "source": {"org": "Curaçao Tourist Board (CTB) – Tourism Research",
                                  "title": "Stayover Visitor Arrivals – informe mensual (Total Stayover Arrivals by Region)",
                                  "page_url": PAGE, "file_url": recs[last]["url"], "format": "pdf",
                                  "update_frequency": "mensual", "release_lag": "~2-4 semanas",
                                  "last_period": last, "retrieved": "2026-09-17",
                                  "access_method": "Scrapear enlaces .pdf de la página monthly-statistics (nombres de archivo irregulares); "
                                                   "pdfplumber página 1, fila 'Total' de 'Total Stayover Arrivals by Region' (mes, mismo mes año previo, YTD). "
                                                   "Período desde el encabezado '(Mon/ YYYY)'."}}],
           "notes": [
               "Cada mes se toma de la columna 'año previo' del informe del año siguiente cuando existe (cifras revisadas); los meses del último año son preliminares.",
               "Abr-jun 2020 no tienen informe propio publicado; sus valores provienen de la columna año previo de los informes abr-jun 2021.",
               "Feb-2025 es un PDF sólo imagen; su valor proviene del informe feb-2026. El informe oct-2025 ('Visitor-Arrivals-V01_final.pdf') no trae encabezado de período: asignado por carpeta de carga 2025/11 y verificado con el acumulado YTD.",
               "Gasto turístico: no se extrajo. La fuente oficial es la balanza de pagos del Centrale Bank van Curaçao en Sint Maarten (CBCS, 'Travel' credits, trimestral) en centralbank.cw, que bloquea descargas automatizadas (HTTP 403 / protección anti-bot). CTB publica gasto/estadía en encuestas de salida y 'State of the Industry' (PDF, irregular).",
               f"Chequeo YTD con diferencias >1%: {issues[:10]}" if issues else "Chequeo de consistencia mensual vs. diferencias YTD sin discrepancias >1%."]}
    json.dump(doc, open(os.path.join(BASE, "CW.json"), "w"), ensure_ascii=False, indent=1)
    missing = [f"{y}-{m:02d}" for y in range(2019, 2027) for m in range(1, 13) if f"{y}-{m:02d}" <= last and f"{y}-{m:02d}" not in val]
    print(len(data), data[0], data[-1], "missing:", missing, "issues:", issues)

if __name__ == "__main__":
    main()
