#!/usr/bin/env python
"""
República Dominicana (DO) - official tourism series extractor.

Downloads the official Excel files from their stable URLs (Banco Central de la
República Dominicana - BCRD - and Ministerio de Turismo - MITUR/SITUR), parses
them and writes data/DO.json.

Usage:  python data/scripts/DO_extract.py [--no-download]
  --no-download  re-parse the files already present in data/raw/DO/

No values are estimated: every number comes from a cell of an official file.
The only transformations are: rounding BCRD weighted (fractional) passenger
counts to integers, and summing MITUR port / zone columns into totals
(documented in notes).
"""
import datetime as dt
import json
import re
import sys
import unicodedata
from pathlib import Path

import pandas as pd
import requests

BASE = Path(__file__).resolve().parents[1]          # .../data
RAW = BASE / "raw" / "DO"
OUT = BASE / "DO.json"
RAW.mkdir(parents=True, exist_ok=True)

START_YEAR = 2019
TODAY = dt.date.today()
RETRIEVED = TODAY.isoformat()
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}

BCRD_TUR = "https://cdn.bancentral.gov.do/documents/estadisticas/sector-turismo/documents/"
BCRD_EXT = "https://cdn.bancentral.gov.do/documents/estadisticas/sector-externo/documents/"
BCRD_TUR_PAGE = "https://www.bancentral.gov.do/a/d/2537-sector-turismo"
BCRD_EXT_PAGE = "https://www.bancentral.gov.do/a/d/2532-sector-externo"
SITUR = "https://situr.mitur.gob.do/wp-content/uploads/estadisticas/mensual/"
SITUR_PAGE = "https://situr.mitur.gob.do/estadisticas/descargas/"

MONTHS = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
          "septiembre", "octubre", "noviembre", "diciembre"]
Q_MAP = {"E-M": 1, "A-J": 2, "J-S": 3, "O-D": 4}

DOWNLOAD = "--no-download" not in sys.argv


def norm(s):
    s = "" if s is None or (isinstance(s, float) and pd.isna(s)) else str(s)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().lower()


MON3 = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def month_idx(v):
    """1-12 for 'Enero', 'ENE.', 'SEPT.', 'septiembre'...; None otherwise."""
    t = norm(v).rstrip(".")
    if t in MONTHS:
        return MONTHS.index(t) + 1
    if len(t) in (3, 4) and t[:3] in MON3 and (len(t) == 3 or t == "sept"):
        return MON3.index(t[:3]) + 1
    return None


def fetch(url, name):
    path = RAW / name
    if DOWNLOAD:
        r = requests.get(url, headers=UA, timeout=120)
        if r.status_code != 200:
            print(f"  ! {r.status_code} {url}")
            return None
        path.write_bytes(r.content)
        print(f"  downloaded {name} ({len(r.content)} bytes)")
    return path if path.exists() else None


def num(v):
    if v is None:
        return None
    if isinstance(v, str):
        v = v.strip().replace(",", "")
        if v in ("", "-", "n.d.", "nd"):
            return None
        try:
            return float(v)
        except ValueError:
            return None
    if pd.isna(v):
        return None
    return float(v)


# ----------------------------------------------------------------------------
# 1. BCRD monthly non-resident air arrivals 1978-latest (lleg_total.xls, sheet 0)
# ----------------------------------------------------------------------------
def bcrd_nonresident_monthly():
    p = fetch(BCRD_TUR + "lleg_total.xls", "lleg_total.xls")
    df = pd.read_excel(p, sheet_name=0, header=None)
    # header row with 'Mensual' labels -> columns for Total / Dominicanos / Extranjeros
    hdr6 = {j: norm(v) for j, v in enumerate(df.iloc[6].tolist())}
    col_total = next(j for j, v in hdr6.items() if v == "total")
    col_dom = next(j for j, v in hdr6.items() if v == "dominicanos")
    col_ext = next(j for j, v in hdr6.items() if v == "extranjeros")
    out = {"total": [], "dom": [], "ext": []}
    year = None
    for _, row in df.iterrows():
        c0 = row.iloc[0]
        m = re.fullmatch(r"(\d{4})\*?", str(c0).strip()) if pd.notna(c0) else None
        if m:
            year = int(m.group(1))
            continue
        if year is None or norm(c0) not in MONTHS:
            continue
        mi = MONTHS.index(norm(c0)) + 1
        if year < START_YEAR:
            continue
        per = f"{year}-{mi:02d}"
        for k, c in (("total", col_total), ("dom", col_dom), ("ext", col_ext)):
            v = num(row.iloc[c])
            if v is not None:
                out[k].append([per, int(round(v))])
    return out


# ----------------------------------------------------------------------------
# 2. BCRD per-airport monthly arrivals (lleg_total_YYYY.xls) - NO RESIDENTES block
# ----------------------------------------------------------------------------
def bcrd_airport_monthly(airport="PUNTA CANA", block="NO RESIDENTES", last_period=None):
    data, checks = [], []
    for year in range(START_YEAR, TODAY.year + 1):
        p = fetch(BCRD_TUR + f"lleg_total_{year}.xls", f"lleg_total_{year}.xls")
        if p is None:
            continue
        df = pd.read_excel(p, sheet_name=0, header=None)
        # month header row
        hr = next(i for i in range(10) if any(month_idx(v) == 1 for v in df.iloc[i].tolist()))
        mcols = {month_idx(v): j for j, v in enumerate(df.iloc[hr].tolist()) if month_idx(v)}
        # label column is 0 in recent files, 1 in 2019-2020 files
        lab = next(c for c in range(3) if (df.iloc[:, c].map(norm) == norm(block)).any())
        col0 = df.iloc[:, lab].map(norm)
        start = next(i for i, v in col0.items() if v == norm(block))
        # airport rows follow the block header until a blank row
        i = start + 1
        target = None
        while i < len(df) and col0[i] != "":
            if col0[i] == norm(airport):
                target = i
            i += 1
        if target is None:
            raise RuntimeError(f"{airport} not found in {block} block of {year}")
        for mi, j in sorted(mcols.items()):
            per = f"{year}-{mi:02d}"
            if last_period and per > last_period:
                continue  # months not yet published appear as 0 in the current-year file
            v = num(df.iat[target, j])
            if v is not None:
                data.append([per, int(round(v))])
        checks.append(year)
    return data


# ----------------------------------------------------------------------------
# 3. BCRD maritime passengers by port (APD) - TOTAL row, monthly
# ----------------------------------------------------------------------------
def parse_maritime_sheet(df, year):
    hr = next(i for i in range(8) if any(month_idx(v) == 1 for v in df.iloc[i].tolist()))
    # BUQUES col; PASAJEROS is j+1
    mcols = {month_idx(v): j for j, v in enumerate(df.iloc[hr].tolist()) if month_idx(v)}
    col0 = df.iloc[:, 0].map(norm)
    tr = next(i for i, v in col0.items() if v == "total")
    rows = []
    for mi, j in sorted(mcols.items()):
        ships, pax = num(df.iat[tr, j]), num(df.iat[tr, j + 1])
        if pax is None:
            continue
        rows.append((f"{year}-{mi:02d}", ships, int(round(pax))))
    return rows


def bcrd_maritime_monthly():
    rows = []
    hist_name = "lleg_maritima_1994-2025.xls"
    if DOWNLOAD:  # the historical file is renamed every year -> discover current name from the page
        try:
            html = requests.get("https://www.bancentral.gov.do/a/CustomView/2537-sector-turismo",
                                headers=UA, timeout=60).text
            found = sorted(set(re.findall(r"lleg_maritima_1994-\d{4}\.xls", html)))
            if found:
                hist_name = found[-1]
        except requests.RequestException:
            pass
    p = fetch(BCRD_TUR + hist_name, hist_name)
    x = pd.ExcelFile(p)
    hist_years = sorted(int(s) for s in x.sheet_names if re.fullmatch(r"\d{4}", s) and int(s) >= START_YEAR)
    for y in hist_years:
        rows += parse_maritime_sheet(pd.read_excel(x, str(y), header=None), y)
    for y in range(max(hist_years) + 1, TODAY.year + 1):
        p = fetch(BCRD_TUR + f"lleg_maritima_mensual_{y}.xls", f"lleg_maritima_mensual_{y}.xls")
        if p is None:
            continue
        cur = parse_maritime_sheet(pd.read_excel(p, sheet_name=0, header=None), y)
        # in the current-year file unpublished months are 0 ships / 0 pax: trim trailing zeros
        while cur and cur[-1][1] == 0 and cur[-1][2] == 0:
            cur.pop()
        rows += cur
    return [[per, pax] for per, _, pax in rows]


# ----------------------------------------------------------------------------
# 4. BCRD hotel occupancy (ASONAHORES data published by BCRD), monthly
# ----------------------------------------------------------------------------
def bcrd_occupancy():
    total, puj = [], []
    for y in range(START_YEAR, TODAY.year + 1):
        p = fetch(BCRD_TUR + f"turismo_ocupacion_{y}.xls", f"turismo_ocupacion_{y}.xls")
        if p is None:
            continue
        df = pd.read_excel(p, sheet_name=0, header=None)
        hr = next(i for i in range(8) if norm(df.iat[i, 0]).startswith("meses"))
        hdr = [norm(v) for v in df.iloc[hr].tolist()]
        c_tot = hdr.index("total")
        c_puj = next(j for j, v in enumerate(hdr) if v.startswith("punta cana"))
        for i in range(hr + 1, len(df)):
            mi = month_idx(df.iat[i, 0])
            if not mi:
                continue
            per = f"{y}-{mi:02d}"
            for arr, c in ((total, c_tot), (puj, c_puj)):
                v = num(df.iat[i, c])
                if v is not None:
                    arr.append([per, round(v, 1)])
    return total, puj


# ----------------------------------------------------------------------------
# 5. BCRD balance of services: travel (Viajes) credit, quarterly, US$ millions
# ----------------------------------------------------------------------------
def bcrd_travel_credit():
    p = fetch(BCRD_EXT + "Balanza-de-Servicios-trimestral.xlsx", "Balanza-de-Servicios-trimestral.xlsx")
    df = pd.read_excel(p, sheet_name="Trim", header=None)
    col0 = df.iloc[:, 0].map(norm)
    yr_row = next(i for i, v in col0.items() if v == "conceptos")
    q_row = yr_row + 1
    cred = next(i for i, v in col0.items() if v.startswith("i. credito"))
    deb = next(i for i, v in col0.items() if v.startswith("ii. debito"))
    viajes = next(i for i in range(cred, deb) if col0[i].endswith("viajes"))
    title = str(df.iat[2, 0])
    data = []
    for j in range(1, df.shape[1]):
        y = num(df.iat[yr_row, j])
        q = Q_MAP.get(str(df.iat[q_row, j]).strip())
        v = num(df.iat[viajes, j])
        if y is None or q is None or v is None or int(y) < START_YEAR:
            continue
        data.append([f"{int(y)}-Q{q}", round(v, 1)])
    return data, title


# ----------------------------------------------------------------------------
# 6. BCRD average daily spending & average stay of non-resident foreigners, quarterly
# ----------------------------------------------------------------------------
def bcrd_spend_stay():
    p = fetch(BCRD_TUR + "turismo_gasto_estadia.xls", "turismo_gasto_estadia.xls")
    df = pd.read_excel(p, sheet_name=0, header=None)
    spend, stay = [], []
    year = None
    for i in range(len(df)):
        c0 = str(df.iat[i, 0]).strip() if pd.notna(df.iat[i, 0]) else ""
        m = re.fullmatch(r"(\d{4})\*?", c0) or (re.fullmatch(r"(\d{4})\.0", c0))
        if m:
            year = int(m.group(1))
            continue
        mq = re.fullmatch(r"t([1-4])", c0.lower())
        if not (mq and year and year >= START_YEAR):
            continue
        per = f"{year}-Q{mq.group(1)}"
        g, e = num(df.iat[i, 1]), num(df.iat[i, 2])
        if g is not None:
            spend.append([per, round(g, 2)])
        if e is not None:
            stay.append([per, round(e, 2)])
    return spend, stay


# ----------------------------------------------------------------------------
# 7. BCRD FDI flows by activity - Turismo, quarterly, US$ millions
# ----------------------------------------------------------------------------
def bcrd_fdi_tourism():
    p = fetch(BCRD_EXT + "inversion_ext_sector_6.xls", "inversion_ext_sector_6.xls")
    df = pd.read_excel(p, sheet_name="IED por actividad trim", header=None)
    col0 = df.iloc[:, 0].map(norm)
    yr_row = next(i for i, v in col0.items() if v.startswith("actividad"))
    tur = next(i for i, v in col0.items() if v == "turismo")
    title = str(df.iat[3, 0])
    data = []
    for j in range(1, df.shape[1]):
        y = re.match(r"(\d{4})", str(df.iat[yr_row, j]).strip())
        q = Q_MAP.get(str(df.iat[yr_row + 1, j]).strip())
        v = num(df.iat[tur, j])
        if not y or q is None or v is None or int(y.group(1)) < START_YEAR:
            continue
        data.append([f"{y.group(1)}-Q{q}", round(v, 1)])
    return data, title


# ----------------------------------------------------------------------------
# 8. MITUR SITUR: cruise passengers (monthly, 2022+) and hotel rooms (quarterly, 2022+)
# ----------------------------------------------------------------------------
def situr_update_date(df):
    for i in range(6):
        for v in df.iloc[i].tolist():
            if isinstance(v, str) and "actualiz" in v.lower():
                return v.strip()
    return None


def mitur_cruise():
    p = fetch(SITUR + "1.%20Llegadas%20mar%C3%ADtimas.xlsx", "mitur_llegadas_maritimas.xlsx")
    tot = pd.read_excel(p, sheet_name="Cruceristas", header=None)
    port = pd.read_excel(p, sheet_name="Cruceristas por puerto", header=None)
    upd = situr_update_date(port)
    # header rows: type row ('Cruceros' / 'Ferry') and port-name row starting with 'Año'
    hr = next(i for i in range(15) if norm(port.iat[i, 0]) == "ano")
    tr = hr - 1
    ferry_col = next(j for j, v in enumerate(port.iloc[tr].tolist()) if norm(v) == "ferry")
    cruise_start = next(j for j, v in enumerate(port.iloc[tr].tolist()) if norm(v) == "cruceros")
    cruise_cols = list(range(cruise_start, ferry_col))
    # totals sheet
    thr = next(i for i in range(15) if norm(tot.iat[i, 0]) == "ano")
    pax_col = [norm(v) for v in tot.iloc[thr].tolist()].index("pasajeros")
    totals = {}
    year = None
    for i in range(thr + 1, len(tot)):
        if re.fullmatch(r"\d{4}", str(tot.iat[i, 0]).strip().split(".")[0]):
            year = int(str(tot.iat[i, 0]).split(".")[0])
        m = norm(tot.iat[i, 1])
        v = num(tot.iat[i, pax_col])
        if year and m in MONTHS and v is not None:
            totals[f"{year}-{MONTHS.index(m) + 1:02d}"] = int(v)
    cruise = []
    year = None
    for i in range(hr + 1, len(port)):
        if re.fullmatch(r"\d{4}", str(port.iat[i, 0]).strip().split(".")[0]):
            year = int(str(port.iat[i, 0]).split(".")[0])
        m = norm(port.iat[i, 1])
        if not (year and m in MONTHS):
            continue
        per = f"{year}-{MONTHS.index(m) + 1:02d}"
        vals = [num(port.iat[i, j]) for j in cruise_cols]
        ferry = num(port.iat[i, ferry_col]) or 0
        if per not in totals:
            continue  # month not yet published
        c = int(sum(v for v in vals if v is not None))
        if c + int(ferry) != totals[per]:
            print(f"  ! cruise+ferry != total for {per}: {c}+{ferry} vs {totals[per]}")
        cruise.append([per, c])
    return cruise, upd


def mitur_rooms():
    p = fetch(SITUR + "3.%20Hoteles%20y%20habitaciones%20hoteleras%20-%20zonas%20MITUR.xlsx",
              "mitur_hoteles_habitaciones.xlsx")
    df = pd.read_excel(p, sheet_name="Habitaciones por zona", header=None)
    upd = situr_update_date(df)
    hr = next(i for i in range(15) if norm(df.iat[i, 0]) == "ano")
    hdr = [norm(v) for v in df.iloc[hr].tolist()]
    zone_cols = [j for j in range(2, len(hdr)) if hdr[j]]
    puj_col = next(j for j in zone_cols if hdr[j].startswith("bavaro"))
    total, puj = [], []
    year = None
    for i in range(hr + 1, len(df)):
        if re.fullmatch(r"\d{4}", str(df.iat[i, 0]).strip().split(".")[0]):
            year = int(str(df.iat[i, 0]).split(".")[0])
        q = str(df.iat[i, 1]).strip()
        if not (year and re.fullmatch(r"T[1-4]", q)):
            continue
        vals = [num(df.iat[i, j]) for j in zone_cols]
        if all(v is None for v in vals):
            continue
        per = f"{year}-Q{q[1]}"
        total.append([per, int(sum(v for v in vals if v is not None))])
        v = num(df.iat[i, puj_col])
        if v is not None:
            puj.append([per, int(v)])
    return total, puj, upd


# ----------------------------------------------------------------------------
def src(**kw):
    kw.setdefault("retrieved", RETRIEVED)
    return kw


def main():
    series, notes = [], []

    print("BCRD arrivals (lleg_total.xls)")
    nr = bcrd_nonresident_monthly()
    last = nr["total"][-1][0]
    arrivals_src = dict(org="Banco Central de la República Dominicana (BCRD); compilación MITUR desde sep-2021",
                        page_url=BCRD_TUR_PAGE, format="xls",
                        update_frequency="mensual",
                        release_lag="~4-8 semanas tras el cierre del mes (julio 2026 publicado en agosto 2026)",
                        last_period=last)
    series.append(dict(key="arrivals_air_nonresident", category="arrivals",
                       label="Llegadas de no residentes vía aérea", unit="personas", frequency="monthly",
                       data=nr["total"],
                       source=src(title="Llegada total de pasajeros no residentes vía aérea 1978-2026 (hoja 'No Residentes'), columna Total/Mensual",
                                  file_url=BCRD_TUR + "lleg_total.xls",
                                  access_method="GET directo al .xls (URL estable, sin auth; usar User-Agent de navegador). Hoja 0: filas año -> meses; columna 'Total' 'Mensual'.",
                                  **arrivals_src)))
    series.append(dict(key="arrivals_air_nonresident_foreign", category="arrivals",
                       label="Llegadas de extranjeros no residentes vía aérea", unit="personas", frequency="monthly",
                       data=nr["ext"],
                       source=src(title="Llegada total de pasajeros no residentes vía aérea 1978-2026, columna Extranjeros/Mensual",
                                  file_url=BCRD_TUR + "lleg_total.xls",
                                  access_method="Mismo archivo que arrivals_air_nonresident; columna 'Extranjeros' 'Mensual'.",
                                  **arrivals_src)))
    series.append(dict(key="arrivals_air_nonresident_dominican", category="arrivals",
                       label="Llegadas de dominicanos no residentes vía aérea", unit="personas", frequency="monthly",
                       data=nr["dom"],
                       source=src(title="Llegada total de pasajeros no residentes vía aérea 1978-2026, columna Dominicanos/Mensual",
                                  file_url=BCRD_TUR + "lleg_total.xls",
                                  access_method="Mismo archivo que arrivals_air_nonresident; columna 'Dominicanos' 'Mensual'.",
                                  **arrivals_src)))

    print("BCRD per-airport (lleg_total_YYYY.xls)")
    puj = bcrd_airport_monthly("PUNTA CANA", "NO RESIDENTES", last_period=last)
    series.append(dict(key="arrivals_air_nonresident_puj", category="arrivals",
                       label="Llegadas de no residentes vía aérea - Aeropuerto Internacional de Punta Cana",
                       unit="personas", frequency="monthly", data=puj,
                       source=src(title="Llegada mensual de pasajeros según residencia y aeropuerto utilizado, vía aérea (un archivo por año)",
                                  file_url=BCRD_TUR + "lleg_total_{YYYY}.xls",
                                  access_method="GET a lleg_total_{YYYY}.xls para cada año (2019..año actual). Bloque 'NO RESIDENTES' -> fila 'PUNTA CANA'; columnas ENERO..DICIEMBRE. En el año en curso los meses no publicados aparecen como 0 (se recortan con el último periodo de lleg_total.xls).",
                                  **{**arrivals_src, "last_period": puj[-1][0]})))

    print("BCRD maritime (APD)")
    mar = bcrd_maritime_monthly()
    series.append(dict(key="arrivals_maritime_passengers", category="arrivals",
                       label="Pasajeros llegados por vía marítima (todos los puertos, incluye ferry)",
                       unit="personas", frequency="monthly", data=mar,
                       source=src(org="BCRD, con datos de la Autoridad Portuaria Dominicana (APD)",
                                  title="Número de buques y pasajeros llegados al país por puertos (histórico 1994-2025 por hoja anual + archivo mensual del año en curso)",
                                  page_url=BCRD_TUR_PAGE,
                                  file_url=BCRD_TUR + "lleg_maritima_1994-2025.xls ; " + BCRD_TUR + "lleg_maritima_mensual_{YYYY}.xls",
                                  format="xls", update_frequency="mensual",
                                  release_lag="~4-8 semanas tras el cierre del mes",
                                  last_period=mar[-1][0],
                                  access_method="GET a lleg_maritima_1994-2025.xls (hojas por año) y lleg_maritima_mensual_{YYYY}.xls (año en curso). Fila 'TOTAL', columna PASAJEROS de cada mes. OJO: el nombre del histórico cambia cada año (1994-YYYY).")))

    print("MITUR cruise passengers")
    cruise, upd_c = mitur_cruise()
    series.append(dict(key="arrivals_cruise_passengers", category="arrivals",
                       label="Cruceristas (pasajeros de cruceros, excluye ferry y tripulantes)",
                       unit="personas", frequency="monthly", data=cruise,
                       source=src(org="Ministerio de Turismo (MITUR) - SITUR",
                                  title="Llegadas marítimas - hoja 'Cruceristas por puerto' (suma de columnas 'Cruceros')",
                                  page_url=SITUR_PAGE,
                                  file_url=SITUR + "1.%20Llegadas%20mar%C3%ADtimas.xlsx",
                                  format="xlsx", update_frequency=f"mensual ({upd_c})",
                                  release_lag="~1 semana tras el cierre del mes",
                                  last_period=cruise[-1][0],
                                  access_method="GET directo al .xlsx (URL estable). Hoja 'Cruceristas por puerto': sumar columnas bajo 'Cruceros' (se verifica que Cruceros+Ferry = hoja 'Cruceristas' Pasajeros). Solo desde 2022.")))

    print("BCRD travel credit (BoP)")
    trav, trav_title = bcrd_travel_credit()
    series.append(dict(key="spending_tourism_receipts", category="spending",
                       label="Ingresos por turismo (Balanza de pagos, Viajes - crédito)",
                       unit="US$ millones", frequency="quarterly", data=trav,
                       source=src(org="Banco Central de la República Dominicana (BCRD) - Departamento Internacional",
                                  title=f"Balanza de servicios, datos trimestrales ({trav_title}) - I. Crédito, C. Viajes (MBP6)",
                                  page_url=BCRD_EXT_PAGE,
                                  file_url=BCRD_EXT + "Balanza-de-Servicios-trimestral.xlsx",
                                  format="xlsx", update_frequency="trimestral",
                                  release_lag="~3-4 meses tras el cierre del trimestre (T1-2026 publicado 9-jul-2026)",
                                  last_period=trav[-1][0],
                                  access_method="GET directo al .xlsx. Hoja 'Trim': fila 'C. Viajes' dentro del bloque 'I. CREDITO'; encabezados año (fila CONCEPTOS) y trimestre (E-M, A-J, J-S, O-D).")))

    print("BCRD daily spend / stay")
    spend, stay = bcrd_spend_stay()
    ss_src = dict(org="Banco Central de la República Dominicana (BCRD) - Encuesta de Opinión, Actitud y Motivación de Visitantes No Residentes",
                  title="Gasto diario y estadía promedio trimestral de los extranjeros no residentes 1993-2026",
                  page_url=BCRD_TUR_PAGE, file_url=BCRD_TUR + "turismo_gasto_estadia.xls",
                  format="xls", update_frequency="trimestral",
                  release_lag="~4 semanas tras el cierre del trimestre (T2-2026 publicado 28-jul-2026)",
                  access_method="GET directo al .xls. Hoja 0: fila año seguida de filas t1..t4; col 1 = gasto promedio (US$), col 2 = estadía promedio (noches). La fila del año es el promedio anual ponderado (no se usa).")
    series.append(dict(key="spending_avg_daily", category="spending",
                       label="Gasto promedio diario por turista extranjero no residente",
                       unit="US$ por día", frequency="quarterly", data=spend,
                       source=src(last_period=spend[-1][0], **ss_src)))
    series.append(dict(key="spending_avg_stay", category="spending",
                       label="Estadía promedio de extranjeros no residentes",
                       unit="noches", frequency="quarterly", data=stay,
                       source=src(last_period=stay[-1][0], **ss_src)))

    print("BCRD occupancy")
    occ_tot, occ_puj = bcrd_occupancy()
    occ_src = dict(org="BCRD (publica datos de la Asociación de Hoteles y Turismo de la Rep. Dom., ASONAHORES)",
                   page_url=BCRD_TUR_PAGE, file_url=BCRD_TUR + "turismo_ocupacion_{YYYY}.xls",
                   format="xls", update_frequency="mensual",
                   release_lag="~4-8 semanas tras el cierre del mes",
                   access_method="GET a turismo_ocupacion_{YYYY}.xls por año; columnas 'Total' y 'Punta Cana / Bávaro'; filas por mes (meses sin publicar vienen vacíos).")
    series.append(dict(key="hotel_occupancy_rate", category="hotels",
                       label="Tasa de ocupación hotelera promedio - total país", unit="%", frequency="monthly",
                       data=occ_tot, source=src(title="Tasa promedio de ocupación en establecimientos de alojamiento turístico según zonas - Total",
                                                last_period=occ_tot[-1][0], **occ_src)))
    series.append(dict(key="hotel_occupancy_rate_puj", category="hotels",
                       label="Tasa de ocupación hotelera promedio - Punta Cana / Bávaro", unit="%", frequency="monthly",
                       data=occ_puj, source=src(title="Tasa promedio de ocupación en establecimientos de alojamiento turístico según zonas - Punta Cana/Bávaro",
                                                last_period=occ_puj[-1][0], **occ_src)))

    print("MITUR rooms")
    rooms_tot, rooms_puj, upd_r = mitur_rooms()
    rooms_src = dict(org="Ministerio de Turismo (MITUR) - SITUR", page_url=SITUR_PAGE,
                     file_url=SITUR + "3.%20Hoteles%20y%20habitaciones%20hoteleras%20-%20zonas%20MITUR.xlsx",
                     format="xlsx", update_frequency=f"trimestral ({upd_r})",
                     release_lag="~5 semanas tras el cierre del trimestre (T2-2026 en archivo del 5-ago-2026)",
                     access_method="GET directo al .xlsx. Hoja 'Habitaciones por zona'; filas Año/Trimestre; columnas por zona turística MITUR. Solo desde 2022.")
    series.append(dict(key="hotel_rooms", category="hotels",
                       label="Habitaciones hoteleras (suma de zonas MITUR)", unit="habitaciones", frequency="quarterly",
                       data=rooms_tot, source=src(title="Hoteles y habitaciones hoteleras - Habitaciones por zona (suma de todas las columnas, incl. 'Resto')",
                                                  last_period=rooms_tot[-1][0], **rooms_src)))
    series.append(dict(key="hotel_rooms_puj", category="hotels",
                       label="Habitaciones hoteleras - Bávaro-Punta Cana", unit="habitaciones", frequency="quarterly",
                       data=rooms_puj, source=src(title="Hoteles y habitaciones hoteleras - Habitaciones por zona, columna 'Bávaro-Punta Cana'",
                                                  last_period=rooms_puj[-1][0], **rooms_src)))

    print("BCRD FDI tourism")
    fdi, fdi_title = bcrd_fdi_tourism()
    series.append(dict(key="investment_fdi_tourism", category="investment",
                       label="Inversión extranjera directa en turismo (flujos)", unit="US$ millones", frequency="quarterly",
                       data=fdi,
                       source=src(org="Banco Central de la República Dominicana (BCRD) - Departamento Internacional",
                                  title=f"Flujos de la inversión extranjera directa por actividad económica ({fdi_title}) - Turismo (MBP6)",
                                  page_url=BCRD_EXT_PAGE, file_url=BCRD_EXT + "inversion_ext_sector_6.xls",
                                  format="xls", update_frequency="trimestral",
                                  release_lag="~3-4 meses tras el cierre del trimestre (T1-2026 publicado 9-jul-2026)",
                                  last_period=fdi[-1][0],
                                  access_method="GET directo al .xls. Hoja 'IED por actividad trim': fila 'Turismo'; encabezados año (fila 'Actividad') y trimestre (E-M, A-J, J-S, O-D). Hoja 'IED por actividad anual' tiene totales anuales.")))

    notes += [
        "Llegadas aéreas (BCRD): hasta ene-2021 se basaban en formularios físicos de la Dirección General de Migración; feb-ago 2021 transición BCRD/MITUR; desde sep-2021 las compila MITUR a partir del e-ticket obligatorio (JAC Res. 178-2021) y las remite al BCRD. Por eso desde 2022 los valores del BCRD vienen con decimales (ponderados); aquí se redondean a enteros.",
        "No residentes = extranjeros no residentes + dominicanos no residentes (diáspora). arrivals_air_nonresident = foreign + dominican. Cifras del año en curso 'sujetas a rectificación'; el BCRD re-publicó los archivos anuales 2022-2025 en ago-2026 (revisiones posibles: volver a descargar todo en cada corrida).",
        "MITUR/SITUR publica las mismas llegadas (archivo '1. Flujo migratorio.xlsx', desde 2022, ~1 semana de rezago) con pequeñas diferencias frente al BCRD (p.ej. jul-2026 extranjeros no residentes: MITUR 754,413 vs BCRD 754,380). Se usa el BCRD por la serie larga y por ser la fuente de cuentas nacionales; MITUR sirve como indicador adelantado.",
        "arrivals_air_nonresident_puj: fila PUNTA CANA del bloque NO RESIDENTES (aeropuerto de llegada, no destino final de hospedaje).",
        "arrivals_maritime_passengers (BCRD/APD) incluye pasajeros de ferry (incluidos en el puerto Don Diego desde 2011) y no distingue cruceristas antes de 2022; para cruceristas puros usar arrivals_cruise_passengers (MITUR, solo desde ene-2022). En 2020-2021 los valores 0 son reales (suspensión de cruceros por COVID).",
        "spending_tourism_receipts = Balanza de pagos, cuenta Viajes (crédito), MBP6. No hay serie mensual oficial descargable; solo trimestral/anual. El archivo 'turismo_valor.xlsx' (Indicadores de turismo) trae la misma variable como acumulado del año (Ene-Mar, Ene-Jun...) con diferencias de vintage menores (p.ej. 2025: 11,318.5 vs 11,321.4 en la balanza de servicios).",
        "spending_avg_daily y spending_avg_stay corresponden a extranjeros no residentes (Encuesta de Opinión, Actitud y Motivación de Visitantes No Residentes del BCRD). Existe archivo análogo para dominicanos no residentes (turismo_gasto_estadia_dom_no_res.xls), no incluido. Estos indicadores salen antes (T2-2026 disponible) que los ingresos de la balanza de pagos (T1-2026).",
        "Ocupación hotelera: dato de ASONAHORES (gremio privado) republicado por el BCRD en su sección oficial de turismo; interpretar como fuente semi-oficial. Abr-jun 2020 no tienen dato en el archivo (hoteles cerrados por COVID) y se omiten. MITUR/SITUR publica una serie propia de actividad hotelera (desde 2022) no incluida.",
        "hotel_rooms: suma de las columnas de zona del archivo MITUR (incluye 'Resto'); inventario 'según reportado al Ministerio de Turismo', solo desde 2022-T1. Hay caídas trimestrales que reflejan cambios de reporte (p.ej. Puerto Plata 2026-T2), no necesariamente cierres. No es comparable con las 'Habitaciones hoteleras' de ASONAHORES en turismo_valor.xlsx del BCRD (94,482 en ene-mar 2026 vs 82,225 MITUR en 2026-T1).",
        "investment_fdi_tourism: flujos de IED (MBP6) de la actividad Turismo; incluye reinversión de utilidades, no equivale a inversión total en el sector. Serie secundaria.",
        "CONFOTUR (Consejo de Fomento Turístico, MITUR): los montos de proyectos aprobados solo se divulgan en notas de prensa de mitur.gob.do (p.ej. 'Confotur aprobó 42 proyectos inmobiliarios por US$1,567 millones'); no existe serie numérica descargable, por lo que no se incluye.",
        "El BCRD también ofrece una API (apibcrd.bancentral.gov.do) que no fue evaluada; los .xls de cdn.bancentral.gov.do son estables y no requieren autenticación. Los nombres con año (lleg_total_YYYY, lleg_maritima_mensual_YYYY, turismo_ocupacion_YYYY, lleg_maritima_1994-YYYY) cambian cada enero.",
    ]

    out = {"id": "DO", "name": "República Dominicana", "type": "country", "lat": 18.7, "lon": -70.2,
           "series": series, "notes": notes}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    for s in series:
        print(f"  {s['key']:40s} {s['frequency']:9s} {s['data'][0][0]} -> {s['data'][-1][0]}  n={len(s['data'])}")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
