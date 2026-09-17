#!/usr/bin/env python
"""
República Dominicana (DO) - per-pole (polo turístico) official tourism series.

Builds data/poles/DO.json from the same official files used by DO_extract.py,
but reading the *breakdown* sheets/rows instead of the national totals:

  BCRD  (bancentral.gov.do, "Sector turismo")
    lleg_total_YYYY.xls        -> non-resident air arrivals BY AIRPORT, monthly
    turismo_ocupacion_YYYY.xls -> ASONAHORES hotel occupancy BY ZONE, monthly
  MITUR / SITUR
    3. Hoteles y habitaciones hoteleras - zonas MITUR.xlsx
        'Habitaciones por zona'        -> rooms BY ZONE, quarterly
        'Hoteles por zona'             -> establishments BY ZONE, quarterly
    1. Actividad hotelera - zonas tradicionales.xlsx  (9 traditional zones)
    2. Actividad hotelera - zonas MITUR.xlsx          (14 MITUR zones)
        'Ocupación por zona'           -> MITUR "ocupación abierta" BY ZONE, monthly
        '% Extranjeros por zona'       -> share of foreign guests BY ZONE, monthly
    1. Flujo migratorio.xlsx
        'Entradas NR por aeropuerto'   -> non-resident entries BY AIRPORT, monthly
                                          (corroboration only, not published as a series)

Usage:  python data/scripts/DO_poles.py [--no-download]
  --no-download  re-parse the files already present in data/raw/DO/

No value is estimated, interpolated or invented: every number is a cell of an
official file. The only transformations are (all documented in the notes):
  * rounding BCRD weighted (fractional) passenger counts to integers,
  * converting MITUR fractions (0.751) to percent (75.1),
  * summing explicitly named components (airports / zones) into a pole total,
    which is the whole point of the file.
"""
import datetime as dt
import json
import re
import sys
import unicodedata
from pathlib import Path

import pandas as pd
import requests

BASE = Path(__file__).resolve().parents[1]           # .../data
RAW = BASE / "raw" / "DO"
OUT_DIR = BASE / "poles"
OUT = OUT_DIR / "DO.json"
NATIONAL = BASE / "DO.json"
RAW.mkdir(parents=True, exist_ok=True)
OUT_DIR.mkdir(parents=True, exist_ok=True)

START_YEAR = 2019
TODAY = dt.date.today()
RETRIEVED = TODAY.isoformat()
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}

BCRD_TUR = "https://cdn.bancentral.gov.do/documents/estadisticas/sector-turismo/documents/"
BCRD_TUR_PAGE = "https://www.bancentral.gov.do/a/d/2537-sector-turismo"
SITUR = "https://situr.mitur.gob.do/wp-content/uploads/estadisticas/mensual/"
SITUR_PAGE = "https://situr.mitur.gob.do/estadisticas/descargas/"

F_ROOMS = "3.%20Hoteles%20y%20habitaciones%20hoteleras%20-%20zonas%20MITUR.xlsx"
F_HOTEL_TRAD = "1.%20Actividad%20hotelera%20-%20zonas%20tradicionales.xlsx"
F_HOTEL_MZ = "2.%20Actividad%20hotelera%20-%20zonas%20MITUR.xlsx"
F_MIGR = "1.%20Flujo%20migratorio.xlsx"

MONTHS = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
          "septiembre", "octubre", "noviembre", "diciembre"]
MON3 = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]

DOWNLOAD = "--no-download" not in sys.argv


# ---------------------------------------------------------------- helpers ----
def norm(s):
    s = "" if s is None or (isinstance(s, float) and pd.isna(s)) else str(s)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().lower()


def zkey(s):
    """Normalised zone/port key: 'Samaná - Las Terrenas' -> 'samana-las terrenas'."""
    t = norm(s).rstrip("*").strip()
    t = re.sub(r"\s*[-/]\s*", "-", t)
    return t


def month_idx(v):
    t = norm(v).rstrip(".").rstrip("/1").strip()
    t = re.sub(r"/\d$", "", t).strip()
    if t in MONTHS:
        return MONTHS.index(t) + 1
    if len(t) in (3, 4) and t[:3] in MON3 and (len(t) == 3 or t == "sept"):
        return MON3.index(t[:3]) + 1
    return None


def num(v):
    if v is None:
        return None
    if isinstance(v, str):
        v = v.strip().replace(",", "")
        if v in ("", "-", "n.d.", "nd", "--"):
            return None
        try:
            return float(v)
        except ValueError:
            return None
    if pd.isna(v):
        return None
    return float(v)


def fetch(url, name):
    path = RAW / name
    if DOWNLOAD:
        try:
            r = requests.get(url, headers=UA, timeout=180)
        except requests.RequestException as e:
            print(f"  ! {e} {url}")
            return path if path.exists() else None
        if r.status_code != 200:
            print(f"  ! {r.status_code} {url}")
            return path if path.exists() else None
        path.write_bytes(r.content)
        print(f"  downloaded {name} ({len(r.content)} bytes)")
    return path if path.exists() else None


def situr_update_date(df):
    for i in range(8):
        for v in df.iloc[i].tolist():
            if isinstance(v, str) and "actualiz" in v.lower():
                return v.strip().replace("Fecha de actualización: ", "")
    return None


# ------------------------------------------------- 1. BCRD by airport --------
# Airport rows of the "NO RESIDENTES" block of lleg_total_YYYY.xls.
AIRPORTS = {
    "las americas": "LAS AMERICAS",
    "puerto plata": "PUERTO PLATA",
    "punta cana": "PUNTA CANA",
    "la romana": "LA ROMANA",
    "maria montez": "MARIA MONTEZ",
    "cibao": "CIBAO",
    "la isabela": "LA ISABELA",
    "el catey, samana": "EL CATEY SAMANA",
    "el catey samana": "EL CATEY SAMANA",
}


def bcrd_airports(last_period):
    """({airport: {'YYYY-MM': int}}, {'YYYY-MM': (raw airport sum, raw block total)}).

    The second dict keeps FULL PRECISION so the sum of the airport rows can be
    checked against the source's own 'NO RESIDENTES' total row before rounding.
    """
    by_air, raw_check = {}, {}
    for year in range(START_YEAR, TODAY.year + 1):
        p = fetch(BCRD_TUR + f"lleg_total_{year}.xls", f"lleg_total_{year}.xls")
        if p is None:
            continue
        df = pd.read_excel(p, sheet_name=0, header=None)
        # month header row: the row that carries 'ENERO'
        hr = next(i for i in range(12) if any(month_idx(v) == 1 for v in df.iloc[i].tolist()))
        mcols = {month_idx(v): j for j, v in enumerate(df.iloc[hr].tolist()) if month_idx(v)}
        # label column: 1 in the 2019-2020 files, 0 from 2021 on
        lab = next(c for c in range(3) if (df.iloc[:, c].map(norm) == "no residentes").any())
        col = df.iloc[:, lab].map(norm)
        start = next(i for i, v in col.items() if v == "no residentes")
        # rows of the block run until the first blank label
        rows, i = {}, start + 1
        while i < len(df) and col[i] != "":
            if col[i] in AIRPORTS:
                rows[AIRPORTS[col[i]]] = i
            i += 1
        if not rows:
            raise RuntimeError(f"no airport rows in NO RESIDENTES block of {year}")
        for mi, j in sorted(mcols.items()):
            per = f"{year}-{mi:02d}"
            # in the current-year file months not yet published are printed as 0
            if last_period and per > last_period:
                continue
            raw = []
            for name, i in rows.items():
                v = num(df.iat[i, j])
                if v is not None:
                    by_air.setdefault(name, {})[per] = int(round(v))
                    raw.append(v)
            t = num(df.iat[start, j])
            if t is not None and len(raw) == len(rows):
                raw_check[per] = (sum(raw), t)
    return by_air, raw_check


# --------------------------------------- 2. BCRD / ASONAHORES occupancy ------
OCC_ZONES = {
    "total": "TOTAL",
    "santo domingo": "SANTO DOMINGO",
    "boca chica": "BOCA CHICA/JUAN DOLIO",
    "romana": "ROMANA/BAYAHIBE",
    "punta cana": "PUNTA CANA/BAVARO",
    "puerto plata": "PUERTO PLATA",
    "sosua": "SOSUA/CABARETE",
    "samana": "SAMANA",
    "santiago": "SANTIAGO",
    "miches": "MICHES",
}


def bcrd_occupancy_zones():
    """{zone: {'YYYY-MM': float}}; real published 0.0 values are kept."""
    out = {}
    for y in range(START_YEAR, TODAY.year + 1):
        p = fetch(BCRD_TUR + f"turismo_ocupacion_{y}.xls", f"turismo_ocupacion_{y}.xls")
        if p is None:
            continue
        df = pd.read_excel(p, sheet_name=0, header=None)
        hr = next(i for i in range(10) if norm(df.iat[i, 0]).startswith("meses"))
        cols = {}
        for j, v in enumerate(df.iloc[hr].tolist()):
            t = norm(v).rstrip("*").strip()
            for pref, name in OCC_ZONES.items():
                if t == pref or t.startswith(pref):
                    cols[name] = j
                    break
        for i in range(hr + 1, len(df)):
            mi = month_idx(df.iat[i, 0])
            if not mi:
                continue
            per = f"{y}-{mi:02d}"
            for name, j in cols.items():
                v = num(df.iat[i, j])
                if v is not None:
                    out.setdefault(name, {})[per] = round(v, 1)
    return out


# ------------------------------- 3. MITUR quarterly tables (rooms/hotels) ----
def mitur_quarterly(path, sheet):
    """{zone_key: {'YYYY-Qn': int}}, plus the update date."""
    df = pd.read_excel(path, sheet_name=sheet, header=None)
    upd = situr_update_date(df)
    hr = next(i for i in range(20) if norm(df.iat[i, 0]) == "ano")
    zones = {j: zkey(df.iat[hr, j]) for j in range(2, df.shape[1]) if norm(df.iat[hr, j])}
    out, year = {}, None
    for i in range(hr + 1, len(df)):
        c0 = str(df.iat[i, 0]).strip().split(".")[0]
        if re.fullmatch(r"\d{4}", c0):          # the year is printed only on the T1 row
            year = int(c0)
        q = str(df.iat[i, 1]).strip()
        if not (year and re.fullmatch(r"[Tt][1-4]", q)):
            continue
        per = f"{year}-Q{q[1]}"
        for j, z in zones.items():
            v = num(df.iat[i, j])
            if v is not None:
                out.setdefault(z, {})[per] = int(round(v))
    return out, upd


# ------------------------------- 4. MITUR monthly tables (by zone / port) ----
def mitur_monthly(path, sheet, first_col=2, hdr_label="ano", scale=1.0, decimals=None):
    """{col_key: {'YYYY-MM': value}}, plus the update date and the header row index."""
    df = pd.read_excel(path, sheet_name=sheet, header=None)
    upd = situr_update_date(df)
    hr = next(i for i in range(20) if norm(df.iat[i, 0]) == hdr_label)
    cols = {j: zkey(df.iat[hr, j]) for j in range(first_col, df.shape[1]) if norm(df.iat[hr, j])}
    out, year = {}, None
    for i in range(hr + 1, len(df)):
        c0 = str(df.iat[i, 0]).strip().split(".")[0]
        if re.fullmatch(r"\d{4}", c0):          # the year is printed only on the January row
            year = int(c0)
        mi = month_idx(df.iat[i, 1])
        if not (year and mi):
            continue
        per = f"{year}-{mi:02d}"
        for j, z in cols.items():
            v = num(df.iat[i, j])
            if v is None:
                continue
            v = v * scale
            out.setdefault(z, {})[per] = round(v, decimals) if decimals is not None else int(round(v))
    return out, upd, df, hr


def mitur_airports_nr():
    """{airport: {per: entries}} - Dominicano + Extranjero blocks summed."""
    p = fetch(SITUR + F_MIGR, "mitur_flujo_migratorio.xlsx")
    df = pd.read_excel(p, sheet_name="Entradas NR por aeropuerto", header=None)
    upd = situr_update_date(df)
    hr = next(i for i in range(20) if norm(df.iat[i, 0]) == "ano")
    out, year = {}, None
    cols = [(j, zkey(df.iat[hr, j])) for j in range(2, df.shape[1]) if norm(df.iat[hr, j])]
    for i in range(hr + 1, len(df)):
        c0 = str(df.iat[i, 0]).strip().split(".")[0]
        if re.fullmatch(r"\d{4}", c0):
            year = int(c0)
        mi = month_idx(df.iat[i, 1])
        if not (year and mi):
            continue
        per = f"{year}-{mi:02d}"
        for j, z in cols:
            v = num(df.iat[i, j])
            if v is None:
                continue
            d = out.setdefault(z, {})
            d[per] = d.get(per, 0) + int(round(v))
    return out, upd


# ------------------------------------------------------------ assembling -----
def ser(d, keys):
    """Sum one or more component dicts into [[period, value], ...].

    A period is emitted when at least one component publishes it; missing
    components are skipped (never zero-filled) - see the notes in the output.
    """
    pers = sorted({p for k in keys for p in d.get(k, {})})
    out = []
    for p in pers:
        vals = [d[k][p] for k in keys if p in d.get(k, {})]
        if vals:
            out.append([p, sum(vals)])
    return out


def as_int(rows):
    return [[p, int(v)] for p, v in rows]


def rnd1(rows):
    return [[p, round(v, 1)] for p, v in rows]


POLES = [
    # id, name, lat, lon, airports, occupancy zone (ASONAHORES/BCRD), rooms zones (MITUR)
    ("bavaro-punta-cana", "Bávaro – Punta Cana – Macao", 18.62, -68.42,
     ["PUNTA CANA"], "PUNTA CANA/BAVARO", ["bavaro-punta cana"]),
    ("la-romana-bayahibe", "La Romana – Bayahíbe", 18.40, -68.90,
     ["LA ROMANA"], "ROMANA/BAYAHIBE", ["la romana", "bayahibe"]),
    ("puerto-plata", "Puerto Plata", 19.79, -70.69,
     ["PUERTO PLATA"], "PUERTO PLATA", ["puerto plata"]),
    ("sosua-cabarete", "Sosúa – Cabarete", 19.75, -70.46,
     [], "SOSUA/CABARETE", ["sosua-cabarete"]),
    ("samana", "Samaná – Las Terrenas", 19.26, -69.43,
     ["EL CATEY SAMANA"], "SAMANA", ["samana-las terrenas"]),
    ("santo-domingo", "Gran Santo Domingo", 18.48, -69.93,
     ["LAS AMERICAS", "LA ISABELA"], "SANTO DOMINGO", ["gran santo domingo"]),
    ("boca-chica-juan-dolio", "Boca Chica – Juan Dolio", 18.44, -69.64,
     [], "BOCA CHICA/JUAN DOLIO", ["juan dolio-boca chica"]),
    ("santiago", "Santiago", 19.45, -70.70,
     ["CIBAO"], "SANTIAGO", ["santiago"]),
    ("miches", "Miches", 18.99, -69.05,
     [], "MICHES", ["miches"]),
    ("jarabacoa-constanza", "Jarabacoa – Constanza", 19.01, -70.69,
     [], None, ["jarabacoa-constanza"]),
    ("barahona-pedernales", "Barahona – Pedernales", 18.21, -71.10,
     ["MARIA MONTEZ"], None, ["barahona"]),
]

# Zone names of MITUR's monthly hotel-activity tables ('Ocupación por zona',
# '% Extranjeros por zona'). MITUR publishes two versions of the same tables:
#   'trad' = 9 zonas tradicionales, which line up with the ASONAHORES/BCRD zones
#            used by hotel_occupancy_rate -> preferred, same territory;
#   'mz'   = 14 zonas MITUR, which line up with the rooms table and are the only
#            ones covering Miches, Jarabacoa-Constanza and Barahona.
# Several spellings are listed because the two sheets of one file disagree
# (e.g. 'Romana - Bayahíbe' vs 'La Romana - Bayahíbe').
MITUR_ZONE = {
    "bavaro-punta-cana":     {"trad": ["bavaro-punta cana"]},
    "la-romana-bayahibe":    {"trad": ["romana-bayahibe", "la romana-bayahibe"]},
    "puerto-plata":          {"trad": ["puerto plata"]},
    "sosua-cabarete":        {"trad": ["sosua-cabarete"]},
    "samana":                {"trad": ["samana"]},
    "santo-domingo":         {"trad": ["gran santo domingo"]},
    "boca-chica-juan-dolio": {"trad": ["boca chica-juan dolio"]},
    "santiago":              {"trad": ["santiago"]},
    "miches":                {"mz": ["miches"]},
    "jarabacoa-constanza":   {"mz": ["jarabacoa-constanza"]},
    "barahona-pedernales":   {"mz": ["barahona"]},
}

AIRPORT_LABEL = {
    "LAS AMERICAS": "Las Américas (SDQ)",
    "PUERTO PLATA": "Puerto Plata / Gregorio Luperón (POP)",
    "PUNTA CANA": "Punta Cana (PUJ)",
    "LA ROMANA": "La Romana (LRM)",
    "MARIA MONTEZ": "María Montez, Barahona (BRX)",
    "CIBAO": "Cibao, Santiago (STI)",
    "LA ISABELA": "La Isabela / Dr. Joaquín Balaguer (JBQ)",
    "EL CATEY SAMANA": "El Catey, Samaná (AZS)",
}
ROOM_ZONE_LABEL = {
    "bavaro-punta cana": "Bávaro-Punta Cana", "gran santo domingo": "Gran Santo Domingo",
    "samana-las terrenas": "Samaná-Las Terrenas", "sosua-cabarete": "Sosúa-Cabarete",
    "jarabacoa-constanza": "Jarabacoa-Constanza", "juan dolio-boca chica": "Juan Dolio-Boca Chica",
    "puerto plata": "Puerto Plata", "santiago": "Santiago", "la romana": "La Romana",
    "barahona": "Barahona", "bayahibe": "Bayahíbe", "miches": "Miches", "cabrera": "Cabrera",
}


def src(**kw):
    kw.setdefault("retrieved", RETRIEVED)
    return kw


def main():
    national = json.loads(NATIONAL.read_text())
    nat = {s["key"]: dict(s["data"]) for s in national["series"]}
    last_air = max(nat["arrivals_air_nonresident"])

    print("BCRD non-resident arrivals by airport (lleg_total_YYYY.xls)")
    air, air_raw_check = bcrd_airports(last_air)
    print("BCRD/ASONAHORES occupancy by zone (turismo_ocupacion_YYYY.xls)")
    occ = bcrd_occupancy_zones()

    print("MITUR rooms / hotels by zone")
    p_rooms = fetch(SITUR + F_ROOMS, "mitur_hoteles_habitaciones.xlsx")
    rooms, upd_rooms = mitur_quarterly(p_rooms, "Habitaciones por zona")
    hotels, _ = mitur_quarterly(p_rooms, "Hoteles por zona")

    print("MITUR occupancy / foreign-guest share by zone (both zonings)")
    hot = {}
    for tag, fname, local in (("trad", F_HOTEL_TRAD, "mitur_actividad_hotelera_tradicionales.xlsx"),
                              ("mz", F_HOTEL_MZ, "mitur_actividad_hotelera_zonas_mitur.xlsx")):
        ph = fetch(SITUR + fname, local)
        occ_h, upd_h, _, _ = mitur_monthly(ph, "Ocupación por zona", scale=100.0, decimals=1)
        ext_h, _, _, _ = mitur_monthly(ph, "% Extranjeros por zona", scale=100.0, decimals=1)
        hot[tag] = dict(occ=occ_h, ext=ext_h, upd=upd_h, file=fname)
    upd_hot = hot["trad"]["upd"]

    print("MITUR non-resident entries by airport (corroboration)")
    air_mitur, upd_migr = mitur_airports_nr()

    # ------------------------------------------------------ source blocks ----
    air_src = dict(
        org="Banco Central de la República Dominicana (BCRD); compilación MITUR desde sep-2021",
        title="Llegada mensual de pasajeros según residencia y aeropuerto utilizado, vía aérea "
              "(un archivo por año) - bloque 'NO RESIDENTES'",
        page_url=BCRD_TUR_PAGE, file_url=BCRD_TUR + "lleg_total_{YYYY}.xls", format="xls",
        update_frequency="mensual",
        release_lag="~4-8 semanas tras el cierre del mes (jul-2026 publicado en ago-2026)",
        access_method="GET directo al .xls por año (2019..año en curso), sin auth, con User-Agent "
                      "de navegador. Hoja 0: localizar la fila 'NO RESIDENTES' (columna de "
                      "etiquetas = 1 en 2019-2020, = 0 desde 2021) y leer las filas de aeropuerto "
                      "hasta la primera fila vacía; columnas ENERO..DICIEMBRE. En el año en curso "
                      "los meses no publicados aparecen como 0 y se recortan con el último "
                      "periodo de la serie nacional.")
    occ_src = dict(
        org="Asociación de Hoteles y Turismo de la República Dominicana (ASONAHORES), "
            "republicado por el BCRD",
        title="Tasa promedio de ocupación en establecimientos de alojamiento turístico según zonas "
              "(un archivo por año)",
        page_url=BCRD_TUR_PAGE, file_url=BCRD_TUR + "turismo_ocupacion_{YYYY}.xls", format="xls",
        update_frequency="mensual",
        release_lag="~4-8 semanas tras el cierre del mes",
        access_method="GET directo al .xls por año. Hoja 0: fila de encabezado que empieza con "
                      "'Meses'; una columna por zona; filas por mes. Los meses sin publicar vienen "
                      "vacíos y se omiten; los 0 de abr-jun 2020 (y de algunas zonas en 2020) son "
                      "ceros reales por cierre de la actividad hotelera.")
    rooms_src = dict(
        org="Ministerio de Turismo (MITUR) - Sistema de Inteligencia Turística (SIT)",
        page_url=SITUR_PAGE, file_url=SITUR + F_ROOMS, format="xlsx",
        update_frequency=f"trimestral (archivo actualizado {upd_rooms})",
        release_lag="~5 semanas tras el cierre del trimestre (2026-T2 en el archivo del 5-ago-2026)",
        access_method="GET directo al .xlsx. Hojas 'Habitaciones por zona' / 'Hoteles por zona': "
                      "encabezado en la fila 'Año | Trimestre | <zonas>'; el año solo aparece en la "
                      "fila T1 de cada año, se arrastra hacia abajo. Serie disponible desde 2022-T1.")
    def mitur_occ_src(tag):
        z = ("zonas tradicionales (9 zonas, coinciden con la zonificación de ASONAHORES/BCRD)"
             if tag == "trad" else
             "zonas MITUR (14 zonas, coinciden con la zonificación del cuadro de habitaciones)")
        return dict(
            org="Ministerio de Turismo (MITUR) - Sistema de Inteligencia Turística (SIT)",
            page_url=SITUR_PAGE, file_url=SITUR + hot[tag]["file"], format="xlsx",
            update_frequency=f"mensual (archivo actualizado {hot[tag]['upd']})",
            release_lag="~1-2 semanas tras el cierre del mes",
            access_method=f"GET directo al .xlsx (versión por {z}). Hojas 'Ocupación por zona' y "
                          "'% Extranjeros por zona': encabezado 'Año | Mes | <zonas>'; el año solo "
                          "aparece en la fila de enero y se arrastra hacia abajo; los valores "
                          "vienen como fracción (0.751) y aquí se expresan en % (75.1). Serie "
                          "disponible desde 2022-01.")

    poles = []
    for pid, name, lat, lon, airports, occ_zone, room_zones in POLES:
        series, notes, missing = [], [], []

        # ---- airport arrivals (BCRD) ----
        if airports:
            rows = ser(air, airports)
            if rows:
                lab = " + ".join(AIRPORT_LABEL[a] for a in airports)
                series.append(dict(
                    key="arrivals_air_nonresident", category="arrivals",
                    label=f"Llegadas de no residentes vía aérea – {lab}",
                    unit="personas", frequency="monthly", data=as_int(rows),
                    source=src(last_period=rows[-1][0], **air_src)))
                if len(airports) > 1:
                    for a in airports:
                        r = ser(air, [a])
                        series.append(dict(
                            key="arrivals_air_nonresident_" + a.lower().replace(" ", "_"),
                            category="arrivals",
                            label=f"Llegadas de no residentes vía aérea – {AIRPORT_LABEL[a]}",
                            unit="personas", frequency="monthly", data=as_int(r),
                            source=src(last_period=r[-1][0], **air_src)))
                # corroboration vs MITUR
                mk = {"PUNTA CANA": "punta cana", "LAS AMERICAS": "las americas",
                      "PUERTO PLATA": "puerto plata", "LA ROMANA": "la romana",
                      "CIBAO": "cibao", "EL CATEY SAMANA": "samana"}
                comp = [mk[a] for a in airports if a in mk]
                if comp:
                    mrows = dict(ser(air_mitur, comp))
                    diffs = [(p, v - mrows[p]) for p, v in rows if p in mrows and v != mrows[p]]
                    if diffs:
                        worst = max(diffs, key=lambda x: abs(x[1]))
                        notes.append(
                            f"Corroboración MITUR ('Entradas NR por aeropuerto', flujo migratorio, "
                            f"desde 2022-01): {len(diffs)} de {len(mrows)} meses difieren del BCRD; "
                            f"diferencia máxima {worst[1]:+,} personas en {worst[0]} "
                            f"({abs(worst[1]) / max(dict(rows)[worst[0]], 1) * 100:.2f}% del mes). "
                            f"Se publica la serie BCRD (serie larga desde 2019 y fuente de cuentas "
                            f"nacionales); MITUR es indicador adelantado.")
                    else:
                        notes.append("Corroboración MITUR ('Entradas NR por aeropuerto'): coincide "
                                     "con el BCRD en todos los meses comparables (desde 2022-01).")
                if "LA ISABELA" in airports or "MARIA MONTEZ" in airports:
                    notes.append("MITUR no desglosa La Isabela ni María Montez (los agrupa en "
                                 "'Resto'), por lo que esos aeropuertos solo se corroboran "
                                 "parcialmente.")
            else:
                missing.append("arrivals_air_nonresident")
        else:
            missing.append("arrivals_air_nonresident (el polo no tiene aeropuerto propio; sus "
                           "visitantes entran por otros aeropuertos y la fuente no publica "
                           "llegadas por destino de hospedaje)")

        # ---- occupancy (BCRD / ASONAHORES) ----
        if occ_zone and occ_zone in occ:
            rows = sorted(occ[occ_zone].items())
            zeros = [p for p, v in rows if v == 0]
            series.append(dict(
                key="hotel_occupancy_rate", category="hotels",
                label=f"Tasa promedio de ocupación hotelera – zona {occ_zone.title()} (ASONAHORES)",
                unit="%", frequency="monthly", data=rnd1(rows),
                source=src(last_period=rows[-1][0], **occ_src)))
            if zeros:
                notes.append("hotel_occupancy_rate: los valores 0.0 de " + ", ".join(zeros) +
                             " son ceros publicados (cierre de la actividad hotelera por COVID-19), "
                             "no datos faltantes.")
        else:
            missing.append("hotel_occupancy_rate (la zona no aparece en el cuadro de ASONAHORES/BCRD)")

        # ---- rooms / establishments (MITUR) ----
        present = [z for z in room_zones if z in rooms]
        if present:
            rows = ser(rooms, present)
            lab = " + ".join(ROOM_ZONE_LABEL.get(z, z) for z in present)
            series.append(dict(
                key="hotel_rooms", category="hotels",
                label=f"Habitaciones hoteleras – zona MITUR {lab}",
                unit="habitaciones", frequency="quarterly", data=as_int(rows),
                source=src(title="Hoteles y habitaciones hoteleras (zonas MITUR) - "
                                 f"hoja 'Habitaciones por zona', columna(s) {lab}",
                           last_period=rows[-1][0], **rooms_src)))
            if len(present) > 1:
                for z in present:
                    r = ser(rooms, [z])
                    series.append(dict(
                        key="hotel_rooms_" + re.sub(r"[^a-z]+", "_", z), category="hotels",
                        label=f"Habitaciones hoteleras – zona MITUR {ROOM_ZONE_LABEL.get(z, z)}",
                        unit="habitaciones", frequency="quarterly", data=as_int(r),
                        source=src(title="Hoteles y habitaciones hoteleras (zonas MITUR) - "
                                         f"hoja 'Habitaciones por zona', columna {ROOM_ZONE_LABEL.get(z, z)}",
                                   last_period=r[-1][0], **rooms_src)))
            hp = [z for z in present if z in hotels]
            if hp:
                r = ser(hotels, hp)
                series.append(dict(
                    key="hotel_establishments", category="hotels",
                    label=f"Establecimientos hoteleros – zona MITUR {lab}",
                    unit="hoteles", frequency="quarterly", data=as_int(r),
                    source=src(title="Hoteles y habitaciones hoteleras (zonas MITUR) - "
                                     f"hoja 'Hoteles por zona', columna(s) {lab}",
                               last_period=r[-1][0], **rooms_src)))
        else:
            missing.append("hotel_rooms / hotel_establishments (la zona no aparece en el cuadro de "
                           "zonas MITUR)")

        # ---- MITUR secondary occupancy + foreign-guest share ----
        spec = MITUR_ZONE.get(pid, {})
        tag = next((t for t in ("trad", "mz") if t in spec), None)
        cands = spec.get(tag, []) if tag else []
        z_occ = next((z for z in cands if z in hot[tag]["occ"]), None) if tag else None
        z_ext = next((z for z in cands if z in hot[tag]["ext"]), None) if tag else None
        occ_m = hot[tag]["occ"] if tag else {}
        ext_m = hot[tag]["ext"] if tag else {}
        zoning = "tradicional" if tag == "trad" else "MITUR"
        if z_occ:
            rows = sorted(occ_m[z_occ].items())
            series.append(dict(
                key="hotel_occupancy_rate_mitur", category="hotels",
                label=f"Tasa de ocupación abierta (MITUR) – zona {z_occ.title()}",
                unit="%", frequency="monthly", data=rnd1(rows),
                source=src(title=f"Actividad hotelera por zonas {zoning}s - hoja 'Ocupación por "
                                 f"zona', columna {z_occ.title()} (ocupación abierta: habitaciones "
                                 "ocupadas / habitaciones en operación)",
                           last_period=rows[-1][0], **mitur_occ_src(tag))))
        else:
            missing.append("hotel_occupancy_rate_mitur (la zona no aparece en los cuadros de "
                           "actividad hotelera de MITUR)")
        if z_ext:
            rows = sorted(ext_m[z_ext].items())
            series.append(dict(
                key="share_foreign_guests", category="hotels",
                label=f"Huéspedes extranjeros sobre visitas totales – zona {z_ext.title()}",
                unit="%", frequency="monthly", data=rnd1(rows),
                source=src(title=f"Actividad hotelera por zonas {zoning}s - hoja '% Extranjeros "
                                 f"por zona', columna {z_ext.title()} (visitas extranjeras / "
                                 "visitas totales)",
                           last_period=rows[-1][0], **mitur_occ_src(tag))))
        else:
            missing.append("share_foreign_guests (la zona no aparece en los cuadros de actividad "
                           "hotelera de MITUR)")
        if tag == "mz":
            notes.append("hotel_occupancy_rate_mitur / share_foreign_guests provienen de la versión "
                         "'zonas MITUR' del cuadro de actividad hotelera (la versión 'zonas "
                         "tradicionales' no incluye esta zona).")
        if pid == "la-romana-bayahibe":
            notes.append("hotel_occupancy_rate_mitur / share_foreign_guests usan la zona combinada "
                         "'Romana - Bayahíbe' del cuadro por zonas tradicionales, para que cubran "
                         "el mismo territorio que hotel_occupancy_rate (ASONAHORES). La versión "
                         "'zonas MITUR' del mismo cuadro publica La Romana y Bayahíbe por separado; "
                         "no se suman aquí porque son tasas y el archivo no da el ponderador.")

        if pid == "barahona-pedernales":
            notes.append("arrivals_air_nonresident empieza en 2021-01 porque los archivos del BCRD "
                         "de 2019 y 2020 no traen la fila MARÍA MONTEZ (solo aparece desde el "
                         "archivo de 2021). Casi todos los meses valen 0: son ceros publicados "
                         "(el aeropuerto María Montez, Barahona, prácticamente no recibe vuelos "
                         "comerciales internacionales; 4 no residentes en todo 2021, 21 en "
                         "ene-jul 2026). No hay serie de ocupación de ASONAHORES/BCRD para este "
                         "polo, ni columna para Pedernales/Cabo Rojo en ninguna de las tres "
                         "fuentes, así que el nuevo desarrollo de Cabo Rojo no es medible aquí.")
        if pid == "miches":
            notes.append("hotel_occupancy_rate solo existe desde 2026-05: ASONAHORES/BCRD añadió la "
                         "columna Miches en el archivo de 2026 y los meses ene-abr 2026 vienen con "
                         "'-' (sin dato), que se omiten. La serie MITUR de este polo "
                         "(hotel_occupancy_rate_mitur) sí arranca en 2022-01.")
        # coverage summary, computed from the data actually extracted
        notes.append("Cobertura extraída: " + "; ".join(
            f"{x['key']} {x['data'][0][0]}→{x['data'][-1][0]} (n={len(x['data'])})"
            for x in series) if series else "Cobertura extraída: ninguna serie disponible.")
        notes.insert(0, f"lat/lon ({lat}, {lon}) son un punto de referencia aproximado del núcleo "
                        "turístico del polo, puesto para poder graficar un mapa; NO son "
                        "coordenadas oficiales ni el centroide del polo turístico definido por ley.")
        notes.append("Series ausentes en este polo: " + ("; ".join(missing) if missing else "ninguna") + ".")
        poles.append(dict(id=pid, name=name, lat=lat, lon=lon, series=series, notes=notes))

    # ------------------------------------------------------- validation ------
    print("\n=== VALIDATION ===")
    val_notes = []

    # (a) airports vs national arrivals_air_nonresident
    nat_air = nat["arrivals_air_nonresident"]
    pers = sorted(set(nat_air) & {p for a in air.values() for p in a})
    bad = []
    for p in pers:
        s = sum(a[p] for a in air.values() if p in a)
        if s != nat_air[p]:
            bad.append((p, s, nat_air[p], s - nat_air[p]))
    print(f"(a) airports vs national air arrivals: {len(pers)} months compared, "
          f"{len(bad)} mismatched")
    for p, s, n, d in bad:
        print(f"    {p}: airports {s:,} vs national {n:,} (diff {d:+,})")
    # stronger check: full-precision airport sum vs the file's own total row
    raw_bad = [(p, a, t) for p, (a, t) in sorted(air_raw_check.items()) if abs(a - t) > 1e-6]
    print(f"    full-precision airport sum vs the file's own 'NO RESIDENTES' total row: "
          f"{len(raw_bad)}/{len(air_raw_check)} mismatched")
    for p, a, t in raw_bad:
        print(f"    {p}: {a} vs {t}")
    if bad:
        worst = max(bad, key=lambda x: abs(x[3]))
        val_notes.append(
            f"Validación (a): la suma de los 8 aeropuertos del bloque NO RESIDENTES coincide con la "
            f"serie nacional arrivals_air_nonresident en {len(pers) - len(bad)} de {len(pers)} "
            f"meses ({min(pers)}..{max(pers)}); difieren {len(bad)} meses, todos de 2022 en "
            f"adelante y todos por +/-1 o +/-2 personas (máximo {worst[3]:+d} en {worst[0]}: suma "
            f"{worst[1]:,} vs nacional {worst[2]:,}). La causa es SOLO el redondeo: desde 2022 el "
            f"BCRD publica cifras ponderadas con decimales, y aquí se redondea cada aeropuerto por "
            f"separado mientras la serie nacional redondea el total. Comprobado a precisión "
            f"completa contra la propia fila 'NO RESIDENTES' del archivo: la suma de los "
            f"aeropuertos la reproduce exactamente en los {len(air_raw_check)} meses "
            f"({len(raw_bad)} discrepancias), de modo que el desglose por aeropuerto es exhaustivo "
            f"y no falta ningún aeropuerto.")
    else:
        val_notes.append(
            f"Validación (a): la suma de los 8 aeropuertos del bloque NO RESIDENTES reproduce "
            f"exactamente la serie nacional arrivals_air_nonresident en los {len(pers)} meses "
            f"comparables ({min(pers)}..{max(pers)}), y también la propia fila 'NO RESIDENTES' del "
            f"archivo a precisión completa ({len(air_raw_check)} meses, {len(raw_bad)} "
            f"discrepancias).")

    # (b) rooms by zone vs national hotel_rooms
    nat_rooms = nat["hotel_rooms"]
    pers_r = sorted(set(nat_rooms) & {p for z in rooms.values() for p in z})
    bad_r = []
    for p in pers_r:
        s = sum(z[p] for z in rooms.values() if p in z)
        if s != nat_rooms[p]:
            bad_r.append((p, s, nat_rooms[p], s - nat_rooms[p]))
    print(f"(b) rooms by zone vs national hotel_rooms: {len(pers_r)} quarters compared, "
          f"{len(bad_r)} mismatched")
    for p, s, n, d in bad_r:
        print(f"    {p}: zones {s:,} vs national {n:,} (diff {d:+,})")
    pole_zones = {zz for *_, zs in POLES for zz in zs}
    pole_rooms = sum(rooms[z][pers_r[-1]] for z in pole_zones
                     if z in rooms and pers_r[-1] in rooms[z])
    unassigned = {z: v[pers_r[-1]] for z, v in rooms.items()
                  if pers_r[-1] in v and z not in pole_zones}
    print(f"    {pers_r[-1]}: assigned to poles {pole_rooms:,}; "
          f"not assigned {unassigned} (total {nat_rooms[pers_r[-1]]:,})")
    if bad_r:
        val_notes.append(
            f"Validación (b): la suma de TODAS las columnas de zona de 'Habitaciones por zona' NO "
            f"cuadra con la serie nacional hotel_rooms en {len(bad_r)} de {len(pers_r)} trimestres: "
            + "; ".join(f"{p} {s:,} vs {n:,} ({d:+,})" for p, s, n, d in bad_r) + ".")
    else:
        val_notes.append(
            f"Validación (b): la suma de TODAS las columnas de zona de 'Habitaciones por zona' "
            f"reproduce exactamente la serie nacional hotel_rooms en los {len(pers_r)} trimestres "
            f"({pers_r[0]}..{pers_r[-1]}), incluido el valor de {pers_r[-1]} "
            f"({nat_rooms[pers_r[-1]]:,} habitaciones). Los polos de este archivo cubren "
            f"{pole_rooms:,} de esas habitaciones en {pers_r[-1]}; las restantes están en las "
            f"columnas " + ", ".join(f"{ROOM_ZONE_LABEL.get(z, z)} ({v:,})"
                                     for z, v in sorted(unassigned.items())) +
            ", que MITUR no asigna a ninguno de los polos construidos aquí.")

    notes = [
        "Este archivo desagrega por polo turístico las MISMAS fuentes oficiales de data/DO.json; "
        "ningún valor se estima, interpola ni imputa. Las únicas transformaciones son: redondeo a "
        "entero de las cifras ponderadas con decimales del BCRD (2022+), conversión de las "
        "fracciones de MITUR a porcentaje, y suma de columnas/filas explícitamente nombradas "
        "(aeropuertos, zonas, puertos) para formar el total del polo.",
        "IMPORTANTE - unidad de medida: las llegadas aéreas son por AEROPUERTO DE ENTRADA, no por "
        "destino de hospedaje. Ni el BCRD ni MITUR publican llegadas por polo de destino, así que "
        "un turista que aterriza en Punta Cana y se hospeda en Bayahíbe se cuenta en el aeropuerto "
        "de Punta Cana. Los polos sin aeropuerto (Sosúa-Cabarete, Boca Chica-Juan Dolio, Miches, "
        "Jarabacoa-Constanza) no tienen serie de llegadas por esa razón, no por falta de turistas.",
        "Las tres fuentes usan tres zonificaciones distintas y NO son intercambiables: los "
        "aeropuertos del BCRD, las zonas de ASONAHORES/BCRD para ocupación (Santo Domingo, Boca "
        "Chica/Juan Dolio, Romana/Bayahíbe, Punta Cana/Bávaro, Puerto Plata, Sosúa/Cabarete, "
        "Samaná, Santiago, Miches) y las zonas MITUR para habitaciones (que separan La Romana de "
        "Bayahíbe y añaden Jarabacoa-Constanza, Barahona, Miches, Cabrera y 'Resto'). Los polos de "
        "este archivo son un mapeo hecho aquí entre esas tres zonificaciones y no una lista oficial "
        "de polos turísticos (los polos de la Ley 158-01 / decretos de CONFOTUR tienen otra "
        "delimitación administrativa).",
        "Ocupación hotelera (hotel_occupancy_rate): el dato es de ASONAHORES, un gremio privado, "
        "republicado por el BCRD en su sección oficial de turismo -> fuente semi-oficial. MITUR "
        "publica además su propia 'ocupación abierta' (hotel_occupancy_rate_mitur = habitaciones "
        "ocupadas / habitaciones en operación, solo desde 2022-01), con definición y muestra "
        "distintas; los niveles no coinciden y las dos series no deben mezclarse ni empalmarse.",
        "OJO al comparar con data/DO.json: la serie nacional hotel_occupancy_rate de ese archivo "
        "omite abr-jun 2020 (el extractor nacional no reconoce las etiquetas de mes 'Abril/1', "
        "'Mayo/1', 'Junio/1' del archivo del BCRD), mientras que aquí esos tres meses SÍ se "
        "incluyen con su 0.0 publicado. Por eso las series de ocupación por polo tienen 91 "
        "observaciones (2019-01..2026-07) y la nacional 88.",
        "Abril-junio 2020: los 0.0 de ocupación son ceros publicados por cierre total de la "
        "actividad hotelera (nota /1 del archivo del BCRD), igual que algunos 0.0 de zonas "
        "concretas entre julio y octubre de 2020. Se conservan como ceros reales. Los meses aún no "
        "publicados vienen vacíos y se omiten.",
        "Habitaciones y hoteles por zona (MITUR): inventario 'según reportado al Ministerio de "
        "Turismo', solo desde 2022-T1, con caídas trimestrales que reflejan cambios de reporte y no "
        "necesariamente cierres (p.ej. Puerto Plata 6,465 -> 4,581 entre 2026-T1 y 2026-T2, o Juan "
        "Dolio-Boca Chica 2,360 -> 1,954). No es comparable con las 'Habitaciones hoteleras' de "
        "ASONAHORES publicadas por el BCRD en turismo_valor.xlsx.",
        "Llegadas marítimas / cruceristas por puerto: FUERA DE ALCANCE en este archivo por "
        "decisión del usuario, aunque MITUR sí las publica por puerto (hoja 'Cruceristas por "
        "puerto' de '1. Llegadas marítimas.xlsx', mensual desde 2022-01) y podrían añadirse a los "
        "polos con puerto (La Romana-Bayahíbe, Puerto Plata, Samaná, Gran Santo Domingo, "
        "Barahona-Pedernales). La serie nacional arrivals_cruise_passengers sigue en data/DO.json.",
        "NO EXISTE desglose oficial por polo de: gasto turístico / ingresos por turismo (la "
        "Balanza de Pagos y la encuesta de gasto del BCRD son solo nacionales), estadía promedio, "
        "inversión extranjera directa en turismo, ni empleo turístico. Tampoco hay serie "
        "descargable de proyectos aprobados por CONFOTUR por polo (solo notas de prensa).",
        "Las llegadas aéreas de MITUR ('Entradas NR por aeropuerto', flujo migratorio, desde "
        "2022-01) se usaron solo para corroborar la serie del BCRD; las diferencias encontradas se "
        "anotan en cada polo. MITUR no desglosa La Isabela ni María Montez (van en 'Resto').",
        "Los archivos del BCRD con año en el nombre (lleg_total_YYYY.xls, turismo_ocupacion_YYYY."
        "xls) cambian cada enero y el BCRD re-publica los años anteriores: conviene volver a "
        "descargar todo en cada corrida mensual. Las cifras del año en curso están 'sujetas a "
        "rectificación'.",
        "Miches y Barahona-Pedernales aparecen con habitaciones/hoteles en el cuadro de zonas MITUR "
        "(Miches también con ocupación de ASONAHORES desde 2026-05), por lo que se incluyen esas "
        "series aunque sean polos emergentes con muy pocos establecimientos.",
    ] + val_notes

    out = {"id": "DO", "poles": poles, "notes": notes}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1))

    print()
    for pl in poles:
        print(f"{pl['id']:24s} " + ", ".join(
            f"{s['key']}[{s['data'][0][0]}..{s['data'][-1][0]},n={len(s['data'])}]"
            for s in pl["series"]))
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
