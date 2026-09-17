"""Turks & Caicos (TC): monthly stay-over arrivals from the TCI Statistics Authority (Dept. of Economic
Planning & Statistics) web table 'Stayover Arrivals January to December 2015 - 2025p'
(https://www.gov.tc/stats/statistics/economic/41-tourism). The Joomla/ArtData table embeds its data as JSON
in the HTML: window.ArtDataData57 = [...]."""
import os, re, json, requests
BASE = os.path.join(os.path.dirname(__file__), "..")
RAW = os.path.join(BASE, "raw", "TC")
URL = "https://www.gov.tc/stats/statistics/economic/41-tourism"
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]

def main():
    fn = os.path.join(RAW, "gov_tc_stats_41-tourism.html")
    try:
        html = requests.get(URL, headers={"User-Agent": "Mozilla/5.0 Chrome/126"}, timeout=60).text
        open(fn, "w").write(html)
    except Exception:
        html = open(fn, encoding="utf-8", errors="ignore").read()
    tables = {m.group(1): json.loads(m.group(2)) for m in re.finditer(r"window\.ArtDataData(\d+)\s*=\s*(\[.*?\]);", html, re.S)}
    # pick the monthly stayover table: first key 'mONTH' and Jan-2019 around 40k (cruise tables are ~90k / ship counts)
    t = tables["57"]
    rows = {r["mONTH"].strip(): r for r in t if r.get("mONTH")}
    data, prelim = [], []
    for col in [c for c in t[0].keys() if c.startswith("yR")]:
        y = int(col[2:6])
        if y < 2019: continue
        if col.endswith("p"): prelim.append(str(y))
        vals = []
        for i, mname in enumerate(MONTHS):
            v = rows[mname][col].replace(",", "").strip()
            if v and v != "-": vals.append([f"{y}-{i+1:02d}", int(v)])
        tot = int(rows["TOTAL"][col].replace(",", ""))
        assert sum(v for _, v in vals) == tot, (y, sum(v for _, v in vals), tot)
        data += vals
    data.sort()
    doc = {"id": "TC", "name": "Islas Turcas y Caicos", "type": "territory", "lat": 21.77, "lon": -72.27,
           "series": [{"key": "arrivals_stopover", "category": "arrivals", "label": "Llegadas de turistas stopover",
                       "unit": "personas", "frequency": "monthly", "data": data,
                       "source": {"org": "Turks and Caicos Islands Statistics Authority (Department of Economic Planning & Statistics)",
                                  "title": "Stayover Arrivals January to December 2015 - 2025p", "page_url": URL, "file_url": URL,
                                  "format": "html", "update_frequency": "anual (tabla web actualizada por año; comunicados trimestrales del Tourist Board)",
                                  "release_lag": "varios meses (2025 aún preliminar en sept-2026)", "last_period": data[-1][0], "retrieved": "2026-09-17",
                                  "access_method": "GET de la página; extraer JSON embebido 'window.ArtDataData57 = [...]' (columnas yRYYYY, filas por mes + TOTAL). "
                                                   "Descarga histórica 2012-2022 en Google Drive enlazado desde la página."}}],
           "notes": [f"Años preliminares (p): {', '.join(prelim)}. Suma mensual verificada contra la fila TOTAL publicada para cada año.",
                     "No hay datos mensuales oficiales 2026 en la tabla del Statistics Authority; Experience Turks & Caicos (Tourist Board) publica comunicados trimestrales (p.ej. T1-2026: 203.587 stayover) sin tabla mensual descargable.",
                     "Gasto turístico: la balanza de pagos anual (BPM6, 2014-2024, xlsx en Google Drive enlazado desde gov.tc/stats .../46-balance-of-payments) sólo publica exportaciones totales de servicios (2024: US$ 1.688,5 millones), sin desglose de 'Viajes'; no se incluye como gasto turístico. No se encontró encuesta oficial de gasto por visitante publicada."]}
    json.dump(doc, open(os.path.join(BASE, "TC.json"), "w"), ensure_ascii=False, indent=1)
    print(len(data), data[0], data[-1])

if __name__ == "__main__":
    main()
