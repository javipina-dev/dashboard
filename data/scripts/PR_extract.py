#!/usr/bin/env python3
"""Puerto Rico (PR) tourism extraction.

Sources (all official):
 * Compañía de Turismo de PR (CTPR/PRTC) "Monthly Statistics Report" (Registration and Occupancy
   in lodgings endorsed by the PRTC), published via Instituto de Estadísticas de PR:
     - old portal  https://estadisticas.pr/en/inventario-de-estadisticas/puerto_rico_tourism_company
     - new portal  https://www.estadisticas.pr.gov/en-us/productos/puerto-rico-tourism-company-monthly-statistics-report
 * Junta de Planificación, Apéndice Estadístico del Informe Económico a la Gobernadora 2025,
   Tabla 19 (Número y gastos de visitantes, años fiscales) -- xlsx on estadisticas.pr.gov.

Run: .venv/bin/python scripts/PR_extract.py   (expects files already downloaded in raw/PR; see DOWNLOADS)
"""
import json, os, re, subprocess, zipfile
import openpyxl, pdfplumber

WD = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(WD, "raw", "PR")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
OLD = "https://www.estadisticas.pr/files/inventario/puerto_rico_tourism_company/"
CDN = "https://cdn.prod.website-files.com/68f911f0119e84487100c03a/"

DOWNLOADS = {f"CT_MonthlyStatisticsReport_2019{m:02d}-revisado.pdf": OLD + f"2020-09-15/CT_MonthlyStatisticsReport_2019{m:02d}-revisado.pdf" for m in range(1, 12)}
DOWNLOADS.update({
    "CT_MonthlyStatisticsReport_201912-revisado.pdf": OLD + "2020-10-14/CT_MonthlyStatisticsReport_201912-revisado.pdf",
    "CT_MonthlyStatisticsReport_2020-revised.pdf": OLD + "2021-06-02/CT_MonthlyStatisticsReport_2020-revised.pdf",
    "CT_MonthlyStatisticsReport_202101-revised.pdf": OLD + "2021-11-02/CT_MonthlyStatisticsReport_202101-revised.pdf",
    "CT_MonthlyStatisticsReport_202102-revised.pdf": OLD + "2021-11-02/CT_MonthlyStatisticsReport_202102-revised.pdf",
    "CT_MonthlyStatisticsReport_202103-revised.pdf": OLD + "2021-11-02/CT_MonthlyStatisticsReport_202103-revised.pdf",
    "CT_MonthlyStatisticsReport_202104.pdf": OLD + "2021-11-02/CT_MonthlyStatisticsReport_202104.pdf",
    "CT_MonthlyStatisticsReport_202105.pdf": OLD + "2021-11-03/CT_MonthlyStatisticsReport_202105.pdf",
    "CT_MonthlyStatisticsReport_202106.pdf": OLD + "2021-11-04/CT_MonthlyStatisticsReport_202106.pdf",
    "CT_MonthlyStatisticsReport_202107.pdf": OLD + "2021-11-05/CT_MonthlyStatisticsReport_202107.pdf",
    "CT_MonthlyStatisticsReport_202108.pdf": OLD + "2021-11-08/CT_MonthlyStatisticsReport_202108.pdf",
    "CT_MonthlyStatisticsReport_202109.pdf": OLD + "2021-12-20/CT_MonthlyStatisticsReport_202109.pdf",
    "CT_MonthlyStatisticsReport_202110.pdf": OLD + "2021-12-21/CT_MonthlyStatisticsReport_202110.pdf",
    "CT_MonthlyStatisticsReport_202111.pdf": OLD + "2022-01-03/CT_MonthlyStatisticsReport_202111.pdf",
    "CT-MonthlyStatisticsReport-2021-12_0.pdf": OLD + "2022-02-11/CT-MonthlyStatisticsReport-2021-12_0.pdf",
    "CT-MonthlyStatisticsReport-2022-12-revised.xlsx": OLD + "2024-02-05/CT-MonthlyStatisticsReport-2022-12-revised.xlsx",
    "CT-MonthlyStatisticsReport-2023-12.xlsx": OLD + "2024-02-02/CT-MonthlyStatisticsReport-2023-12.xlsx",
    "69b40aa18aa2f946f5e32d28_CT-MonthlyStatisticsReport-2025-12.pdf": CDN + "69b40aa18aa2f946f5e32d28_CT-MonthlyStatisticsReport-2025-12.pdf",
    "6a2ad7e648d9163057b5c45a_CT-MonthlyStatisticsReport-2026-03.zip": CDN + "6a2ad7e648d9163057b5c45a_CT-MonthlyStatisticsReport-2026-03.zip",
    "6a0329de9246c0d5df28409b_Apendice-Estadistico-Informe-Economico-a-la-Gobernadora-2025.xlsx": CDN + "6a0329de9246c0d5df28409b_Apendice-Estadistico-Informe-Economico-a-la-Gobernadora-2025.xlsx",
})


def fetch():
    os.makedirs(RAW, exist_ok=True)
    for fn, url in DOWNLOADS.items():
        p = os.path.join(RAW, fn)
        if not os.path.exists(p) or os.path.getsize(p) < 1000:
            subprocess.run(["curl", "-sL", "-m", "180", "-A", UA, "-o", p, url], check=True)


fetch()


def num(tok):
    t = tok.replace("$", "").replace(",", "").replace("%", "")
    if t.startswith("(") and t.endswith(")"):
        t = "-" + t[1:-1]
    return float(t)


def pdf_lines(fn, page):
    with pdfplumber.open(os.path.join(RAW, fn)) as pdf:
        return (pdf.pages[page].extract_text() or "").split("\n")


def values_after(line, label):
    return [num(x) for x in line[len(label):].split()]


nonres, total, alos = {}, {}, {}

# ---------- 2019: monthly PDFs, Metro + Non-Metro (incl. Paradores, Vieques, Culebra) -----------
for m in range(1, 13):
    fn = f"CT_MonthlyStatisticsReport_2019{m:02d}-revisado.pdf"
    L = pdf_lines(fn, 0)
    i_metro = next(i for i, l in enumerate(L) if l.startswith("Metropolitan Area Lodgings"))
    i_nm = next(i for i, l in enumerate(L) if l.startswith("Includes: Paradores, Vieques, Culebra"))
    def first(label, start):
        l = next(l for l in L[start:] if l.startswith(label))
        return values_after(l, label)[0]
    nr = first("Non Residents", i_metro) + first("Non Residents", i_nm)
    tt = first("Total Arrival Persons (Registrations)", i_metro) + first("Total Arrival Persons (Registrations)", i_nm)
    k = f"2019-{m:02d}"
    nonres[k], total[k] = int(nr), int(tt)

# ---------- 2020: revised annual PDF, TOTAL rows (page 4) -----------
L = pdf_lines("CT_MonthlyStatisticsReport_2020-revised.pdf", 3)
for label, dest, conv in [("Total Non Residents (inbound tourism)", nonres, int),
                          ("Total Total Arrivals (persons)", total, int),
                          ("Total Average Stay", alos, float)]:
    l = next(l for l in L if l.startswith(label))
    v = values_after(l, label)
    assert len(v) == 13, (label, v)
    assert abs(sum(v[:12]) - v[12]) <= 2 or conv is float, (label, sum(v[:12]), v[12])
    for m in range(12):
        dest[f"2020-{m+1:02d}"] = conv(v[m])

# ---------- 2021: monthly PDFs, "TOTAL (all regions)" block, first column = month -----------
f2021 = ["CT_MonthlyStatisticsReport_202101-revised.pdf", "CT_MonthlyStatisticsReport_202102-revised.pdf",
         "CT_MonthlyStatisticsReport_202103-revised.pdf", "CT_MonthlyStatisticsReport_202104.pdf",
         "CT_MonthlyStatisticsReport_202105.pdf", "CT_MonthlyStatisticsReport_202106.pdf",
         "CT_MonthlyStatisticsReport_202107.pdf", "CT_MonthlyStatisticsReport_202108.pdf",
         "CT_MonthlyStatisticsReport_202109.pdf", "CT_MonthlyStatisticsReport_202110.pdf",
         "CT_MonthlyStatisticsReport_202111.pdf", "CT-MonthlyStatisticsReport-2021-12_0.pdf"]
for m, fn in enumerate(f2021, 1):
    L = pdf_lines(fn, 0)
    i0 = next(i for i, l in enumerate(L) if "TOTAL (all regions)" in l)
    def first(label):
        l = next(l for l in L[i0:] if l.startswith(label) and not l.strip().endswith("%") and "%" not in l)
        return values_after(l, label)[0]
    k = f"2021-{m:02d}"
    nonres[k] = int(first("Non Residents (inbound tourism)"))
    total[k] = int(first("Total Arrivals (persons)"))
    alos[k] = first("Average Stay")

# ---------- 2022: revised xlsx -----------
ws = openpyxl.load_workbook(os.path.join(RAW, "CT-MonthlyStatisticsReport-2022-12-revised.xlsx"), data_only=True)["By Region-CY"]
rows = {str(r[0]).strip(): r for r in ws.iter_rows(values_only=True) if r[0]}
for label, dest, conv in [("Total Non Residents (inbound tourism)", nonres, lambda x: int(round(x))),
                          ("Total Total Arrivals (persons)", total, lambda x: int(round(x))),
                          ("Total Average Length of Stay", alos, lambda x: round(x, 2))]:
    r = rows[label]
    for m in range(12):
        dest[f"2022-{m+1:02d}"] = conv(r[m + 1])

# ---------- 2023: xlsx, CY by Region, TOTAL block -----------
ws = openpyxl.load_workbook(os.path.join(RAW, "CT-MonthlyStatisticsReport-2023-12.xlsx"), data_only=True)["CY by Region"]
allrows = list(ws.iter_rows(values_only=True))
i0 = max(i for i, r in enumerate(allrows) if r[0] and str(r[0]).strip() == "TOTAL")
blk = {str(r[0]).strip(): r for r in allrows[i0:i0 + 15] if r[0]}
for label, dest, conv in [("Non Residents (inbound tourism)", nonres, lambda x: int(round(x))),
                          ("Total Arrivals (persons)", total, lambda x: int(round(x))),
                          ("Average Length of Stay", alos, lambda x: round(x, 2))]:
    r = blk[label]
    for m in range(12):
        dest[f"2023-{m+1:02d}"] = conv(r[m + 1])


# ---------- 2024-2025 (Dec-2025 PDF) and 2026 (Mar-2026 zip PDF): page 2, 4th block = TOTAL -----------
def total_block(fn, n_months, cur_year):
    with pdfplumber.open(fn) as pdf:
        pg = pdf.pages[1]
        words = pg.extract_words(extra_attrs=["upright"])
        assert any(w["text"] == "LATOT" for w in words if not w["upright"]), "TOTAL label not found"
        L = (pg.extract_text() or "").split("\n")
    idx = [i for i, l in enumerate(L) if l.startswith("Total Arrival Persons")]
    assert len(idx) == 4
    B = L[idx[3]: idx[3] + 12]
    out = {}
    for label in ["Total Arrival Persons", "Inbound Tourism (non-residents)", "Average Length of Stay (alos)"]:
        l = next(x for x in B if x.startswith(label))
        v = values_after(l, label)
        assert len(v) == 2 * n_months + 3, (label, len(v))
        cur, prev = v[:n_months], v[n_months + 3:]
        if "alos" not in label:
            assert abs(sum(cur) - v[n_months]) <= 2 and abs(sum(prev) - v[n_months + 2]) <= 2, label
        out[label] = {**{f"{cur_year}-{m+1:02d}": x for m, x in enumerate(cur)},
                      **{f"{cur_year-1}-{m+1:02d}": x for m, x in enumerate(prev)}}
    return out


b25 = total_block(os.path.join(RAW, "69b40aa18aa2f946f5e32d28_CT-MonthlyStatisticsReport-2025-12.pdf"), 12, 2025)
zpath = os.path.join(RAW, "6a2ad7e648d9163057b5c45a_CT-MonthlyStatisticsReport-2026-03.zip")
with zipfile.ZipFile(zpath) as z:
    name = next(n for n in z.namelist() if n.upper().startswith("MONTHLY REPORT"))
    z.extract(name, os.path.join(RAW, "z2603"))
b26 = total_block(os.path.join(RAW, "z2603", name), 3, 2026)
for b in (b25, b26):  # later release overwrites earlier (latest revision wins)
    for k, v in b["Inbound Tourism (non-residents)"].items(): nonres[k] = int(v)
    for k, v in b["Total Arrival Persons"].items(): total[k] = int(v)
    for k, v in b["Average Length of Stay (alos)"].items(): alos[k] = v

# ---------- Spending: JP Apéndice Estadístico 2025, Tabla 19 -----------
ws = openpyxl.load_workbook(os.path.join(RAW, "6a0329de9246c0d5df28409b_Apendice-Estadistico-Informe-Economico-a-la-Gobernadora-2025.xlsx"), data_only=True)["19"]
t19 = [[c for c in r if c is not None] for r in ws.iter_rows(values_only=True)]
years = next(r for r in t19 if r and r[0] == 2016)
years = [str(y).strip().rstrip("rp") for y in years if str(y).strip()]
gasto = next(r for r in t19 if r and str(r[0]).strip().startswith("Gastos de visitantes, total"))
gasto_vals = gasto[1:1 + len(years)]
spending = [[y, float(v)] for y, v in zip(years, gasto_vals) if int(y) >= 2019]

def ser(d):
    return [[k, d[k]] for k in sorted(d)]

RET = "2026-09-17"
prtc_src = {
    "org": "Compañía de Turismo de Puerto Rico (CTPR), Oficina de Estudios de Mercado; publicado por el Instituto de Estadísticas de PR",
    "title": "Puerto Rico Tourism Company Monthly Statistics Report – Registration and Occupancy in lodgings endorsed by the PRTC",
    "page_url": "https://www.estadisticas.pr.gov/en-us/productos/puerto-rico-tourism-company-monthly-statistics-report",
    "file_url": CDN + "6a2ad7e648d9163057b5c45a_CT-MonthlyStatisticsReport-2026-03.zip",
    "format": "pdf",
    "update_frequency": "mensual (en la práctica publicación irregular/por lotes: p.ej. ene-mar 2026 publicado ~jun 2026)",
    "release_lag": "~2,5-3 meses (histórico en estadisticas.pr: hasta 5-12 meses)",
    "last_period": max(nonres),
    "retrieved": RET,
    "access_method": "Scrape product page on estadisticas.pr.gov for cdn.prod.website-files.com links named CT-MonthlyStatisticsReport-YYYY-MM (.pdf/.zip/.xlsx); in the PDF, page 2 ('Calendar Year'), 4th block (vertical label TOTAL) rows 'Inbound Tourism (non-residents)', 'Total Arrival Persons', 'Average Length of Stay (alos)'. History 2019-2024 from estadisticas.pr/files/inventario/puerto_rico_tourism_company/* (PDF 2019-2021, xlsx 2022-2024). Mirror/primary also on tourism.pr.gov/statistics (unreachable 2026-09-17).",
}
out = {
    "id": "PR", "name": "Puerto Rico", "type": "territory", "lat": 18.47, "lon": -66.11,
    "series": [
        {"key": "arrivals_hotel_nonresident_registrations", "category": "arrivals",
         "label": "Registros de no residentes en hospederías endosadas por la Compañía de Turismo (turismo receptor)",
         "unit": "personas", "frequency": "monthly", "data": ser(nonres), "source": dict(prtc_src)},
        {"key": "arrivals_hotel_total_registrations", "category": "arrivals",
         "label": "Registros totales (residentes + no residentes) en hospederías endosadas por la Compañía de Turismo",
         "unit": "personas", "frequency": "monthly", "data": ser(total), "source": dict(prtc_src)},
        {"key": "spending_avg_stay", "category": "spending",
         "label": "Estadía promedio en hospederías endosadas por la Compañía de Turismo (todos los huéspedes)",
         "unit": "noches", "frequency": "monthly", "data": ser(alos), "source": {**prtc_src, "last_period": max(alos)}},
        {"key": "spending_tourism_receipts", "category": "spending",
         "label": "Gastos de visitantes en Puerto Rico, total (Balanza de pagos: Viajes – crédito), años fiscales (jul-jun)",
         "unit": "US$ millones", "frequency": "annual", "data": spending,
         "source": {
             "org": "Junta de Planificación de Puerto Rico, Programa de Planificación Económica y Social",
             "title": "Apéndice Estadístico del Informe Económico a la Gobernadora 2025 – Tabla 19: Número y gastos de visitantes en Puerto Rico (también Tabla 18, Viajes: Crédito)",
             "page_url": "https://www.estadisticas.pr.gov/en-us/productos/apendice-estadistico-del-informe-economico-al-gobernador",
             "file_url": CDN + "6a0329de9246c0d5df28409b_Apendice-Estadistico-Informe-Economico-a-la-Gobernadora-2025.xlsx",
             "format": "xlsx", "update_frequency": "anual (año fiscal jul-jun)",
             "release_lag": "~10-11 meses tras cierre del año fiscal (AF2025 preliminar publicado ~may 2026)",
             "last_period": spending[-1][0], "retrieved": RET,
             "access_method": "Download xlsx from estadisticas.pr.gov product page (Webflow CDN link changes each edition); sheet '19', row 'Gastos de visitantes, total'; year header row starting 2016 (strip r/p suffixes).",
         }},
    ],
    "notes": [
        "PR es territorio de EE.UU.: los visitantes del continente son tráfico doméstico y no hay estadística oficial mensual de 'llegadas de turistas/stopover'. Se usa como proxy los registros de no residentes en hospederías endosadas por la CTPR (cuenta registros/check-ins, no personas únicas; excluye alquileres a corto plazo tipo Airbnb, casas de familiares y cruceristas).",
        "2019: el informe no trae fila TOTAL; el total se obtiene sumando 'Metropolitan Area Lodgings' + 'Non Metropolitan Area Lodgings (Includes: Paradores, Vieques, Culebra)' de cada PDF mensual revisado. No hay estadía promedio total para 2019 (solo por área), por eso spending_avg_stay inicia en 2020-01.",
        "Revisiones: se usa la publicación más reciente disponible para cada mes (2025-01..03 del informe mar-2026; 2024 del informe dic-2025; 2023 del xlsx dic-2023; 2022 del xlsx 2022-revisado; 2021 de PDFs mensuales; 2020 del PDF 2020-revisado). Hay diferencias menores entre ediciones (p.ej. ene-2024 no residentes 163.249 en xlsx dic-2024 vs 162.794 en PDF dic-2025). Algunos valores del xlsx 2022 venían con decimales (imputaciones de la CTPR) y se redondearon.",
        "Control de calidad: sumas anuales de no residentes coinciden con los totales publicados en 2019 (1.613.066), 2020, 2022, 2023, 2024 y 2025 (2.110.437); en 2021 la suma de los informes mensuales (1.659.085) difiere 0,3% del acumulado ene-dic del informe dic-2021 (1.664.866) por revisiones no reflejadas mes a mes.",
        "2020-2021: cierres por COVID-19 (abr-may 2020 casi nulos); CTPR advierte información faltante para varias hospederías.",
        "Estadía promedio: valores de PDF con 1 decimal (2020-2021, 2024-2026) y del xlsx con 2 decimales (2022-2023). Mide noches por habitación registrada en hospederías endosadas, no la estadía de todos los visitantes.",
        "Gasto: serie anual por AF (jul-jun; '2025' = AF 2024-25, preliminar; 2023-2024 revisados). Incluye turistas en hoteles, en otros alojamientos (familia/amigos, alquileres a corto plazo) y excursionistas (cruceristas); visitantes mayormente de EE.UU. La misma tabla publica número de visitantes (AF2025: 7.077 mil) — gasto promedio por visitante NO se publica como tal, por lo que no se incluye spending_avg_per_visitor.",
        "Pasajeros aéreos SJU (Autoridad de los Puertos/Aerostar) se publican en tourism.pr.gov/statistics ('Passenger-Movement-CY.pdf'), sitio inaccesible (ECONNREFUSED) el 2026-09-17; Internet Archive también fuera de línea. Alternativa oficial automatizable: US DOT BTS T-100 Segment (TranStats), no extraída aquí. DDEC 'Puerto Rico Economic Indicators' (PDF mensual, docs.pr.gov) replica registros de hotel en miles.",
        "Inversión turística (FDI/proyectos) no investigada tras cambio de alcance a gasto turístico.",
    ],
}

if __name__ == "__main__":
    json.dump(out, open(os.path.join(WD, "PR.json"), "w"), ensure_ascii=False, indent=1)
    for s in out["series"]:
        d = s["data"]
        print(s["key"], len(d), d[0], d[-1])
