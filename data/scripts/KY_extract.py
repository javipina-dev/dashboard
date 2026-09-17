"""Cayman Islands (KY).
1) Monthly stay-over (air) arrivals: Cayman Islands Department of Tourism (DoT) Tableau Public viz
   'Air Arrivals by Month' (embedded at https://www.ourcayman.ky/statistics/air-arrivals-by-month;
   https://public.tableau.com/views/AirArrivalsbyMonth/AirArrivalsbyMonth_2). CSV export is disabled and
   direct HTTP replay of vizql bootstrapSession returns 410 (AWS WAF token), so the data was captured from a
   real browser session: the bootstrapSession JSON response was intercepted in-page and the text table
   (Year | Month | Measure | Value) decoded from dataDictionary + paneColumnsData -> raw/KY/tableau_air_arrivals_by_month_extract.txt.
   Browser JS used (run on public.tableau.com in a same-origin iframe hooking XHR): see BROWSER_JS below.
2) Travel credits (BoP, annual, CI$ million): ESO 'BOP & IIP Report Tables' xlsx, Table 2a row '2. Travel'.
3) Stay-over visitor expenditure / ALOS (DoT exit survey via ESO Compendium ch.15 table 15.06), 2019 only."""
import os, json, re
from collections import defaultdict
import pandas as pd

BASE = os.path.join(os.path.dirname(__file__), "..")
RAW = os.path.join(BASE, "raw", "KY")
MON = {m: i + 1 for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split())}
BROWSER_JS = """(1) hook XMLHttpRequest in an iframe document written with the embed HTML; capture responseText of */bootstrapSession/*;
(2) parse '<len>;{json}<len>;{json}', take secondaryInfo.presModelMap.dataDictionary...dataSegments and
vizData...presModelMap['Air Arrivals by Month'].presModelHolder.genVizDataPresModel.paneColumnsData;
(3) for each vizDataColumn map aliasIndices/valueIndices (negative -> cstring[-i-1])."""

def arrivals():
    rows = [l.strip().split("|") for l in open(os.path.join(RAW, "tableau_air_arrivals_by_month_extract.txt")) if l.strip()]
    monthly, annual = {}, {}
    for y, m, _, v in rows:
        v = int(v.replace(",", ""))
        if m == "%all%": annual[int(y)] = v
        else: monthly[f"{y}-{MON[m]:02d}"] = v
    s = defaultdict(int)
    for k, v in monthly.items(): s[int(k[:4])] += v
    for y, tot in annual.items():  # integrity check against the viz's own annual totals
        assert s[y] == tot, (y, s[y], tot)
    return [[k, monthly[k]] for k in sorted(monthly)]

def travel_credits():
    out = {}
    for fn in ["eso_bop_iip_2022_tables.xlsx", "eso_bop_iip_2024_tables.xlsx"]:  # later file overrides (revisions)
        df = pd.read_excel(os.path.join(RAW, fn), sheet_name=[s for s in pd.ExcelFile(os.path.join(RAW, fn)).sheet_names if s.startswith("Table 2a")][0], header=None)
        yrow = next(r for r in df.itertuples(index=False) if sum(bool(re.match(r"^20\d\d[RP]?$", str(c))) for c in r) >= 3)
        years = [(i, str(c)[:4]) for i, c in enumerate(yrow) if re.match(r"^20\d\d[RP]?$", str(c))]
        trow = next(r for r in df.itertuples(index=False) if any(str(c).strip() == "2. Travel" for c in r))
        for i, y in years:  # 'Credit' is the first column under each year header
            out[y] = round(float(trow[i]), 1)
    return [[y, out[y]] for y in sorted(out)]

def main():
    arr = arrivals()
    cred = travel_credits()
    today = "2026-09-17"
    doc = {"id": "KY", "name": "Islas Caimán", "type": "territory", "lat": 19.29, "lon": -81.37,
      "series": [
        {"key": "arrivals_stopover", "category": "arrivals", "label": "Llegadas de turistas stopover (vía aérea)",
         "unit": "personas", "frequency": "monthly", "data": arr,
         "source": {"org": "Cayman Islands Department of Tourism (DoT); datos de Customs & Border Control",
                    "title": "Air Arrivals by Month (Tableau Public)", "page_url": "https://www.ourcayman.ky/statistics/air-arrivals-by-month",
                    "file_url": "https://public.tableau.com/views/AirArrivalsbyMonth/AirArrivalsbyMonth_2", "format": "html",
                    "update_frequency": "mensual", "release_lag": "~2-5 semanas", "last_period": arr[-1][0], "retrieved": today,
                    "access_method": "Tableau Public sin descarga CSV (allow_export_data=false) y con AWS WAF: requiere navegador real; "
                                     "interceptar respuesta bootstrapSession y decodificar dataDictionary/paneColumnsData (ver scripts/KY_extract.py). "
                                     "Alternativa anual/mensual en miles: ESO Compendium of Statistics cap. 15 (xlsx)."}},
        {"key": "spending_tourism_receipts", "category": "spending", "label": "Ingresos por viajes (créditos de 'Viajes', balanza de pagos)",
         "unit": "CI$ millones", "frequency": "annual", "data": cred,
         "source": {"org": "Economics and Statistics Office (ESO), Cayman Islands Government",
                    "title": "The Cayman Islands' BOP and IIP Report – Tables (Table 2a, 2. Travel, Credit)",
                    "page_url": "https://www.eso.ky/balanceofpaymentsreport.html",
                    "file_url": "https://www.eso.ky/storage/page_docums/uploadFileXls/938/2024%20BOP%20&%20IIP%20Report%20Tables.xlsx",
                    "format": "xlsx", "update_frequency": "anual", "release_lag": "~12-18 meses", "last_period": cred[-1][0], "retrieved": today,
                    "access_method": "Descargar xlsx anual desde la página de BOP; hoja 'Table 2a', fila '2. Travel', primera columna (Credit) de cada año."}},
        {"key": "spending_avg_per_visitor_night", "category": "spending", "label": "Gasto promedio por persona por noche (turistas stopover, encuesta de salida)",
         "unit": "CI$", "frequency": "annual", "data": [["2019", 201.70]],
         "source": {"org": "Cayman Islands Department of Tourism – Visitor Exit Survey (publicado por ESO)",
                    "title": "Compendium of Statistics 2024 – Table 15.06 Visitor Expenditure 2016-2019",
                    "page_url": "https://www.eso.ky/tourismstatistics.html",
                    "file_url": "https://www.eso.ky/storage/right_page_docums/uploadFilePdf/457/Chapter%2015%20-%20Tourism%202024.xlsx",
                    "format": "xlsx", "update_frequency": "anual (discontinuado 2020-2022; 2024 no disponible)", "release_lag": "~12+ meses",
                    "last_period": "2019", "retrieved": today, "access_method": "xlsx del Compendium, hoja '.06'."}},
        {"key": "spending_avg_stay", "category": "spending", "label": "Estadía promedio de turistas stopover (encuesta de salida)",
         "unit": "noches", "frequency": "annual", "data": [["2019", 6.09]],
         "source": {"org": "Cayman Islands Department of Tourism – Visitor Exit Survey (publicado por ESO)",
                    "title": "Compendium of Statistics 2024 – Table 15.06 Visitor Expenditure 2016-2019",
                    "page_url": "https://www.eso.ky/tourismstatistics.html",
                    "file_url": "https://www.eso.ky/storage/right_page_docums/uploadFilePdf/457/Chapter%2015%20-%20Tourism%202024.xlsx",
                    "format": "xlsx", "update_frequency": "anual (discontinuado 2020-2022; 2024 no disponible)", "release_lag": "~12+ meses",
                    "last_period": "2019", "retrieved": today, "access_method": "xlsx del Compendium, hoja '.06'."}}],
      "notes": [
        "Llegadas aéreas de turistas (stayover) según DoT; totales mensuales verificados contra los totales anuales del mismo tablero (2019-2025 y ene-jul 2026 = 333.747).",
        "La extracción del tablero Tableau requiere navegador (sin CSV público, WAF bloquea replay HTTP); para automatizar usar navegador headless (Playwright) interceptando la respuesta bootstrapSession.",
        "Créditos de 'Viajes' en dólares de Caimán (CI$ 1 = US$ 1,20 tipo fijo; no convertido). 2023 revisado (R), 2024 preliminar (P). 2019 no está en los cuadros xlsx descargados (reportes 2019-2021 sólo en PDF).",
        "Gasto por encuesta de salida: DoT no estimó gasto 2020-2022 (COVID-19) y ESO indica que las respuestas 2024 no están disponibles; sólo se incluye 2019 dentro del rango solicitado (2016-2018 también publicados).",
        "Gasto total estimado de turistas stopover 2019 (encuesta de salida): CI$ 617,6 millones (no incluido como serie por ser un solo punto)."]}
    json.dump(doc, open(os.path.join(BASE, "KY.json"), "w"), ensure_ascii=False, indent=1)
    print(len(arr), arr[0], arr[-1], cred)

if __name__ == "__main__":
    main()
