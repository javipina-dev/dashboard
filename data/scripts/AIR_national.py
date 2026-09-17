#!/usr/bin/env python
"""
AIR_national.py - monthly flights / passengers per airport from the NATIONAL
civil aviation authorities, and assembly of data/air/<ID>.json.

Part (B) sources
----------------
MEXICO - AFAC (Agencia Federal de Aviacion Civil, SICT)
  "Estadistica Operacional de Aeropuertos" -> producto-aeropuerto-2006-*.xlsx
  page: https://www.gob.mx/afac/acciones-y-programas/estadisticas-280404
  The published workbook is a pivot table whose *cache* carries the whole
  database, so the monthly series are read straight out of
  xl/pivotCache/pivotCacheRecords1.xml (fields: OPCIONES {OPERACIONES,
  PASAJEROS, CARGA}, TIPO {NACIONAL, INTERNACIONAL}, ANO, GRUPO, AEROPUERTO,
  ENE..DIC, TOTAL).  AFAC publishes OPERACIONES (flights) and PASAJEROS - it
  does NOT publish seats.  Same workbook already used by MX_extract.py.

DOMINICAN REPUBLIC - JAC / IDAC: see notes in main(); resolved at runtime.

Part (A) (BTS T-100) is produced by AIR_bts.py and imported here.
"""
import glob
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
import zipfile

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)                 # .../data
RAW_MX = os.path.join(BASE, "raw", "MX")
RAW_DO = os.path.join(BASE, "raw", "DO")
OUT = os.path.join(BASE, "air")
TODAY = "2026-09-17"

UA = "curl/8.4.0"   # plain curl UA passes Akamai on gob.mx; browser UAs get a JS challenge
S = requests.Session()
S.headers["User-Agent"] = UA

AFAC_PAGE = "https://www.gob.mx/afac/acciones-y-programas/estadisticas-280404"
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


# ------------------------------------------------------------------ AFAC (Mexico)
def afac_workbook(force=False):
    """Return (local path, file_url) of the current AFAC airport workbook."""
    os.makedirs(RAW_MX, exist_ok=True)
    cached = sorted(glob.glob(os.path.join(RAW_MX, "producto-aeropuerto-2006-*.xlsx")))
    html = None
    try:
        html = S.get(AFAC_PAGE, timeout=120).text
    except Exception as e:                                       # noqa: BLE001
        print("  !! AFAC page unreachable (%s)" % e)
    url = None
    if html:
        m = re.search(r"/cms/uploads/attachment/file/\d+/"
                      r"producto-aeropuerto-2006-[^\"]+\.xlsx", html)
        if m:
            url = "https://www.gob.mx" + m.group(0)
    if url is None:
        if not cached:
            raise RuntimeError("AFAC workbook link not found and no local cache")
        return cached[-1], AFAC_PAGE
    path = os.path.join(RAW_MX, os.path.basename(url))
    if not os.path.exists(path) or force:
        r = S.get(url, timeout=300)
        r.raise_for_status()
        if r.content[:2] != b"PK":
            raise RuntimeError("AFAC: got HTML/challenge instead of xlsx")
        open(path, "wb").write(r.content)
        print("  downloaded", os.path.basename(path))
    return path, url


def afac(force=False):
    """{(AIRPORT, option, kind): {'YYYY-MM': value}} for CUN and SJD."""
    path, url = afac_workbook(force=force)
    z = zipfile.ZipFile(path)
    d = ET.fromstring(z.read("xl/pivotCache/pivotCacheDefinition1.xml"))
    fields = []
    for cf in d.find("m:cacheFields", NS):
        si = cf.find("m:sharedItems", NS)
        fields.append([e.get("v") for e in si] if si is not None else [])
    names = [cf.get("name") for cf in d.find("m:cacheFields", NS)]
    assert names[0].startswith("OPCIONES") and names[4].startswith("AEROPUERTO"), names

    out = {}
    for rec in ET.fromstring(z.read("xl/pivotCache/pivotCacheRecords1.xml")):
        vals = []
        for i, e in enumerate(rec):
            tag = e.tag.split("}")[1]
            vals.append(fields[i][int(e.get("v"))] if tag == "x"
                        else (None if tag == "m" else e.get("v")))
        opt, tipo, year, _grp, apt = vals[:5]
        if apt not in ("CANCUN", "SAN JOSE DEL CABO") or int(year) < 2019:
            continue
        option = ("operations" if opt.startswith("OPERACIONES")
                  else "passengers" if opt.startswith("PASAJEROS") else None)
        if option is None:
            continue
        kind = "intl" if tipo.startswith("INTERNACIONAL") else "dom"
        for i in range(12):
            v = vals[5 + i]
            if v is None:
                continue
            out.setdefault((apt, option, kind), {})[
                "%s-%02d" % (year, i + 1)] = int(float(v))

    # unpublished months of the current year are stored as 0 -> trim the tail
    last_year = max(int(k[:4]) for s in out.values() for k in s)
    for s in out.values():
        for k in sorted(s, reverse=True):
            if int(k[:4]) == last_year and s[k] == 0:
                del s[k]
            else:
                break
    # combine intl + dom into a total
    for (apt, option, _k) in list(out):
        tot = {}
        for kind in ("intl", "dom"):
            for p, v in out.get((apt, option, kind), {}).items():
                tot[p] = tot.get(p, 0) + v
        # only months present in BOTH breakdowns
        both = set(out.get((apt, option, "intl"), {})) & set(out.get((apt, option, "dom"), {}))
        out[(apt, option, "total")] = {p: v for p, v in tot.items() if p in both}
    return out, url, AFAC_PAGE


# ------------------------------------------------------------------ JAC (Dominican Republic)
JAC_PAGE = "https://jac.gob.do/transparencia/estadisticas-institucionales/"
JAC_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/120 Safari/537.36")
# JAC airport label (as printed in the workbook) -> (IATA, pretty name)
JAC_AIRPORTS = {
    "Punta Cana": ("PUJ", "Punta Cana (PUJ)"),
    "Santo Domingo - Las Américas": ("SDQ", "Santo Domingo Las Américas (SDQ)"),
    "Puerto Plata": ("POP", "Puerto Plata (POP)"),
    "Santiago": ("STI", "Santiago (STI)"),
    "La Romana": ("LRM", "La Romana (LRM)"),
}
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def jac_workbook(force=False):
    """
    Locate + download the JAC 'Reporte Historico 2005-<mes> <ano>.xlsx'.

    It is linked from the year sub-pages of
    /transparencia/estadisticas-institucionales/ (the current year's page, e.g.
    .../estadisticas-institucionales/2026/).  Those pages also carry the
    monthly 'Reporte-Estadisico-<Mes>-<ano>.pdf' bulletins, but the historical
    workbook supersedes them: same figures, machine-readable, full back series.
    """
    import datetime
    os.makedirs(RAW_DO, exist_ok=True)
    s = requests.Session()
    s.headers["User-Agent"] = JAC_UA
    year = datetime.date.fromisoformat(TODAY).year
    cands = []
    pages = [JAC_PAGE] + ["%s%d/" % (JAC_PAGE, y) for y in (year, year - 1)] \
                       + ["%s%d-2/" % (JAC_PAGE, y) for y in (year, year - 1)]
    for pg in pages:
        try:
            html = s.get(pg, timeout=120).text
        except Exception:                                    # noqa: BLE001
            continue
        for u in re.findall(
                r"https://jac\.gob\.do/wp-content/uploads/[^\"' ]*"
                r"Reporte[-_ ]?Historico[^\"' ]*\.xlsx", html, re.I):
            cands.append((pg, u))
    if not cands:
        cached = sorted(glob.glob(os.path.join(RAW_DO, "JAC-Reporte-Historico*.xlsx")))
        if cached:
            print("  !! JAC link not found, using cache", os.path.basename(cached[-1]))
            return cached[-1], None, JAC_PAGE
        raise RuntimeError("JAC historical workbook not found on " + JAC_PAGE)
    # newest upload path (…/uploads/YYYY/MM/…) wins
    page, url = sorted(cands, key=lambda t: t[1])[-1]
    path = os.path.join(RAW_DO, "JAC-" + os.path.basename(url))
    if not os.path.exists(path) or force:
        r = s.get(url, timeout=600)
        r.raise_for_status()
        if r.content[:2] != b"PK":
            raise RuntimeError("JAC: expected xlsx, got %s" %
                               r.headers.get("Content-Type"))
        open(path, "wb").write(r.content)
        print("  downloaded", os.path.basename(path))
    return path, url, page


def _jac_sheet(wb, sheet):
    """
    Parse one 'por Aeropuertos' sheet.

    Layout (identical in both sheets): a row whose col B is a bare year opens a
    block; inside it each airport takes three rows - col B carries the airport
    name on the first of them, col C is Entrada / Salida / Total, cols D..O are
    Enero..Diciembre and col P the yearly total.
    Returns {(airport_label, 'entrada'|'salida'|'total'): {'YYYY-MM': value}}.
    """
    ws = wb[sheet]
    out, year, apt = {}, None, None
    for row in ws.iter_rows(min_row=1, max_col=16, values_only=True):
        b, c = row[1], row[2]
        bs = str(b).strip() if b is not None else ""
        if re.fullmatch(r"(19|20)\d{2}", bs):
            year, apt = int(bs), None
            continue
        if bs.lower().startswith(("aeropuerto", "total")):
            continue
        if bs:
            apt = bs
        cs = str(c).strip().lower() if c is not None else ""
        if not (year and apt and cs in ("entrada", "salida", "total")):
            continue
        for i in range(12):
            v = row[3 + i]
            if isinstance(v, (int, float)):
                out.setdefault((apt, cs), {})["%d-%02d" % (year, i + 1)] = int(round(v))
    return out


def jac(force=False):
    """
    ({(IATA, 'passengers'|'operations', dir): {'YYYY-MM': v}}, file_url, page)

    Trailing months of the current year are present in the grid but zero until
    published, so they are trimmed.
    """
    import openpyxl
    path, url, page = jac_workbook(force=force)
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    raw = {"passengers": _jac_sheet(wb, "5. Pasajeros por Aeropuertos"),
           "operations": _jac_sheet(wb, "17. Operaciones por Aeropuertos")}
    out = {}
    for option, tab in raw.items():
        for (label, direction), d in tab.items():
            if label not in JAC_AIRPORTS:
                continue
            iata = JAC_AIRPORTS[label][0]
            d = {p: v for p, v in d.items() if p >= "2019-01"}
            for p in sorted(d, reverse=True):       # trim unpublished tail
                if d[p] == 0:
                    del d[p]
                else:
                    break
            # JAC prints a BLANK cell for a zero month (its own 'Total' row for
            # that airport still shows 0 - e.g. Puerto Plata, Entradas,
            # 2020-06).  Close such holes inside the published span with 0.
            if d:
                lo, hi = min(d), max(d)
                for y in range(int(lo[:4]), int(hi[:4]) + 1):
                    for mo in range(1, 13):
                        p = "%d-%02d" % (y, mo)
                        if lo <= p <= hi:
                            d.setdefault(p, 0)
            out[(iata, option, direction)] = d
    return out, url, page


JAC_ACCESS = (
    "Descargar el 'Reporte Histórico 2005-<mes> <año>.xlsx' enlazado en "
    "jac.gob.do/transparencia/estadisticas-institucionales/<año>/ (buscar por regex "
    "'Reporte[-_ ]?Historico.*\\.xlsx'; requiere UA de navegador). Hoja "
    "'5. Pasajeros por Aeropuertos' y '17. Operaciones por Aeropuertos': bloques por "
    "año (col B = año), tres filas por aeropuerto (col B = nombre, col C = "
    "Entrada/Salida/Total) y columnas D..O = Enero..Diciembre. Recortar la cola de meses "
    "aún no publicados (vienen como 0). Ver data/scripts/AIR_national.py."
)


def jac_series(force=False):
    """JAC series for data/air/DO.json."""
    try:
        j, url, page = jac(force=force)
    except Exception as e:                                   # noqa: BLE001
        print("  !! JAC unavailable: %s" % e)
        return []
    specs = [
        ("air_operations", "operations", "total",
         "Operaciones aéreas comerciales (entradas + salidas)",
         "operaciones (entradas + salidas)"),
        ("air_passengers_total", "passengers", "total",
         "Pasajeros aéreos totales (entradas + salidas, todos los orígenes)",
         "pasajeros"),
        ("air_passengers_arrivals", "passengers", "entrada",
         "Pasajeros aéreos llegados (entradas, todos los orígenes)",
         "pasajeros"),
    ]
    def src(lp):
        return {
            "org": ("Junta de Aviación Civil (JAC) – República Dominicana, "
                    "Departamento de Economía, Sección Estadística"),
            "title": ("Informe de Pasajeros y Operaciones – Reporte Histórico "
                      "2005-<mes> <año> (hojas '5. Pasajeros por Aeropuertos' "
                      "y '17. Operaciones por Aeropuertos')"),
            # the workbook is linked from the year sub-pages of this landing
            # page
            "page_url": JAC_PAGE,
            "file_url": url or "",
            "format": "xlsx",
            "update_frequency": "mensual",
            "release_lag": ("~2–3 semanas (a 2026-09-17 el último mes "
                            "publicado es 2026-08)"),
            "last_period": lp, "retrieved": TODAY,
            "access_method": JAC_ACCESS,
        }

    out = []
    order = ["PUJ", "SDQ", "POP", "STI", "LRM"]
    for key, option, direction, lab, unit in specs:
        # aggregate PUJ + SDQ first (same pair the air_us_* series cover)
        pair = [(i, option, direction) for i in ("PUJ", "SDQ")]
        if all(j.get(k) for k in pair):
            common = sorted(set(j[pair[0]]) & set(j[pair[1]]))
            out.append({
                "key": key, "category": "air",
                "label": "%s – PUJ + SDQ" % lab,
                "unit": unit, "frequency": "monthly",
                "data": [[m, sum(j[k][m] for k in pair)] for m in common],
                "source": src(common[-1]),
            })
        # then per-airport detail
        for iata in order:
            d = j.get((iata, option, direction), {})
            if not d:
                continue
            pretty = [v[1] for v in JAC_AIRPORTS.values() if v[0] == iata][0]
            out.append({
                "key": "%s_%s" % (key, iata.lower()),
                "category": "air",
                "label": "%s – %s" % (lab, pretty),
                "unit": unit, "frequency": "monthly",
                "data": ser(d),
                "source": src(last(d)),
            })
    return out


JAC_NOTES = [
    "JAC (República Dominicana) publica pasajeros y OPERACIONES por aeropuerto, no "
    "asientos. 'air_operations_*' cuenta entradas + salidas de aeronave, de modo que un "
    "vuelo redondo cuenta 2.",
    "Las cifras de JAC cubren transporte aéreo comercial regular y no regular "
    "(charter) en ambos sentidos y para cualquier origen/destino, no sólo EE.UU.; no "
    "son comparables directamente con las series air_us_* de BTS.",
    "Detalle por aeropuerto de JAC: PUJ, SDQ, POP, STI y LRM (el reporte también "
    "trae Samaná-El Catey y Santo Domingo-El Higüero, omitidos por volumen "
    "marginal).",
    "Validación cruzada oficial–oficial: las 'Entradas' de pasajeros de JAC por "
    "PUJ coinciden con las llegadas aéreas de no residentes del Banco Central "
    "(data/DO.json) dentro de ±3 % en todos los meses y años 2019–2026 "
    "(razones mensuales 0,978–1,029), lo que confirma la lectura de ambas hojas.",
    "El mismo reporte se publica también como PDF mensual "
    "('Reporte-Estadisico-<Mes>-<año>.pdf') y como informe anual "
    "('Informe-Estadistico-del-Transporte-Aero_<año>.pdf'); el xlsx histórico se "
    "prefiere porque es legible por máquina y trae toda la serie desde 2005. JAC "
    "mantiene además un 'Panel Estadístico' en Power BI, no automatizable de forma "
    "estable.",
    "Fuentes oficiales alternativas verificadas para lo mismo (no usadas, pero válidas "
    "como respaldo si JAC cambia de formato): (a) IDAC, boletín mensual acumulado "
    "'Volumen de Operaciones y Pasajeros Internacionales' en xlsx, incluida una hoja "
    "'DATA CRUDA' "
    "(idac.gob.do/transparencia/estadisticas-institucionales/volumen-operaciones-"
    "pasajeros-internacionales/; los enlaces son identificadores opacos "
    "/transparencia/descarga/<id>/, p. ej. 25080 = enero–agosto 2026), cubre sólo "
    "tráfico internacional y llega también hasta 2026-08; (b) datos.gob.do (CKAN): "
    "IDAC operaciones por aeropuerto y pasajeros por aeropuerto en csv mensual desde "
    "2016, y JAC movimiento DIARIO de pasajeros por aeropuerto 2018–2026 — ambos con "
    "un rezago mayor (~2,5 meses, último dato 2026-06).",
    "Ni JAC ni IDAC publican ASIENTOS: se revisaron las 25 hojas del reporte "
    "histórico de JAC y las columnas de los archivos de IDAC y no existe ninguna "
    "métrica de capacidad/asientos. Para República Dominicana los asientos sólo "
    "están disponibles en la serie air_us_seats de BTS (limitada a tramos con "
    "EE.UU.). No se usó ningún agregador privado.",
]


# ------------------------------------------------------------------ helpers
def ser(d):
    return [[p, d[p]] for p in sorted(d)]


def last(d):
    return sorted(d)[-1]



# ------------------------------------------------------------------ assembly
BTS_SRC_BASE = {
    "org": "US DOT \u2013 Bureau of Transportation Statistics",
    "title": "T-100 International Segment (All Carriers)",
    "page_url": "https://www.transtats.bts.gov/DL_SelectFields.aspx?gnoyr_VQ=FJE",
    "file_url": ("https://www.transtats.bts.gov/DL_SelectFields.aspx?gnoyr_VQ=FJE"
                 " (POST con btnDownload=Download \u2192 ZIP/CSV por a\u00f1o)"),
    "format": "csv",
    "update_frequency": "mensual",
    "release_lag": "~3 meses (a 2026-09-17 el \u00faltimo mes completo es 2026-06)",
    "retrieved": TODAY,
    "access_method": (
        "TranStats no expone T-100 ni en /PREZIP (s\u00f3lo exports de usuario de 2015) "
        "ni en data.bts.gov. Ruta automatizable = postback ASP.NET: (1) GET "
        "DL_SelectFields.aspx?gnoyr_VQ=FJE guardando cookies y extrayendo __VIEWSTATE, "
        "__VIEWSTATEGENERATOR y __EVENTVALIDATION; (2) POST a la misma URL con esos 3 "
        "tokens + cboGeography=All, cboYear=<AAAA>, cboPeriod=All, chkDownloadZip=on, "
        "btnDownload=Download y un par '<CAMPO>=on' por columna (PASSENGERS, SEATS, "
        "DEPARTURES_PERFORMED, ORIGIN, ORIGIN_COUNTRY, DEST, DEST_COUNTRY, YEAR, MONTH, "
        "CLASS); (3) la respuesta es un ZIP con T_T100I_SEGMENT_ALL_CARRIER.csv. "
        "Una petici\u00f3n por a\u00f1o. Filtrar ORIGIN_COUNTRY='US' y DEST=<IATA> para "
        "llegadas. Ver data/scripts/AIR_bts.py.")
}


def bts_source(last_period):
    d = dict(BTS_SRC_BASE)
    d["last_period"] = last_period
    return d


def bts_series(agg, months, airports, key_suffix, label_airports):
    """Three series (pax / seats / flights) US -> airports, summed."""
    import AIR_bts
    specs = [
        ("air_us_passengers", "PASSENGERS", "pasajeros",
         "Pasajeros a\u00e9reos llegados desde Estados Unidos"),
        ("air_us_seats", "SEATS", "asientos",
         "Asientos ofrecidos en vuelos desde Estados Unidos"),
        ("air_us_flights", "DEPARTURES_PERFORMED", "vuelos",
         "Vuelos operados desde Estados Unidos"),
    ]
    out = []
    for key, metric, unit, label in specs:
        data = []
        for m in months:
            data.append([m, sum(agg[(ap, "in", metric)].get(m, 0) for ap in airports)])
        out.append({
            "key": key + key_suffix,
            "category": "air",
            "label": "%s \u2013 %s" % (label, label_airports),
            "unit": unit,
            "frequency": "monthly",
            "data": data,
            "source": bts_source(months[-1]),
        })
    return out


def write(dest_id, series, notes):
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, dest_id + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"id": dest_id, "series": series, "notes": notes},
                  f, ensure_ascii=False, indent=1)
    print("wrote %-14s %d series  (%s .. %s)" %
          (os.path.basename(path), len(series),
           series[0]["data"][0][0], series[0]["data"][-1][0]))


AFAC_ACCESS = (
    "Scrapear la página AFAC 'estadisticas-280404' buscando el enlace "
    "'producto-aeropuerto-2006-*.xlsx' (UA 'curl/8.4.0'; los UA de navegador reciben "
    "un reto JS de Akamai). El xlsx es una tabla dinámica: leer "
    "xl/pivotCache/pivotCacheRecords1.xml (campos OPCIONES {OPERACIONES, PASAJEROS, "
    "CARGA}, TIPO {NACIONAL, INTERNACIONAL}, AÑO, GRUPO, AEROPUERTO, ENE..DIC). "
    "Los meses aún no publicados del año corriente vienen como 0: recortar la cola. "
    "Ver data/scripts/AIR_national.py."
)


def afac_series(a, apt, url, page, label_apt):
    out = []
    for kind, key, lab in (
            ("total", "air_operations",
             "Operaciones aéreas totales (nacionales + internacionales)"),
            ("intl", "air_operations_intl",
             "Operaciones aéreas internacionales")):
        d = a.get((apt, "operations", kind), {})
        if not d:
            continue
        out.append({
            "key": key, "category": "air",
            "label": "%s – %s" % (lab, label_apt),
            "unit": "operaciones (despegues + aterrizajes)",
            "frequency": "monthly",
            "data": ser(d),
            "source": {
                "org": "Agencia Federal de Aviación Civil (AFAC), SICT – México",
                "title": ("Estadística Operacional de Aeropuertos – "
                          "producto-aeropuerto (operaciones por aeropuerto)"),
                "page_url": page, "file_url": url, "format": "xlsx",
                "update_frequency": "mensual",
                "release_lag": "~1–2 meses (a 2026-09-17 el último mes es 2026-07)",
                "last_period": last(d), "retrieved": TODAY,
                "access_method": AFAC_ACCESS,
            }})
    return out


def main():
    sys.path.insert(0, HERE)
    import AIR_bts

    force = "--force" in sys.argv
    print("== BTS T-100 ==")
    agg = AIR_bts.aggregate(AIR_bts.load(force=force))
    months = AIR_bts.complete_months(agg)
    print("  complete months %s .. %s" % (months[0], months[-1]))

    print("== AFAC (México) ==")
    a, afac_url, afac_page = afac(force=force)

    BTS_NOTE_DIR = ("Las cifras de BTS son de UNA sola dirección: tramos sin escala "
                    "con ORIGIN_COUNTRY='US' y DEST=<aeropuerto>, es decir llegadas "
                    "al destino desde Estados Unidos. El flujo inverso "
                    "(destino→EE.UU.) es 1–2 % mayor o menor según el mes.")
    BTS_NOTE_COV = ("T-100 International cubre transportistas de EE.UU. (Form 41 T-100) y "
                    "transportistas extranjeros que reportan al DOT (T-100(f)); incluye "
                    "servicio regular y no regular/charter (CLASS F y L). No incluye "
                    "aviación general ni privada.")
    BTS_NOTE_PR = ("'Estados Unidos' incluye Puerto Rico y las Islas Vírgenes "
                   "estadounidenses (ORIGIN_COUNTRY='US'); San Juan (SJU) es un origen "
                   "relevante hacia PUJ/SDQ.")
    BTS_NOTE_ZERO = ("Los meses con 0 corresponden al cierre de fronteras/aeropuertos de "
                     "2020 (abr–jun): no hay tramos reportados porque no se operó, "
                     "no es un hueco de la fuente.")
    BTS_NOTE_REV = ("BTS revisa meses anteriores cuando los transportistas corrigen sus "
                    "reportes; reejecutar el extractor reescribe toda la serie.")
    BTS_NOTE_SEAT = ("'Asientos' = SEATS de T-100, asientos disponibles en los vuelos "
                     "efectivamente operados (no es capacidad programada).")

    # ---------------- JM (Jamaica) --------------------------------------
    s = bts_series(agg, months, ["MBJ", "KIN"], "", "MBJ + KIN")
    s += bts_series(agg, months, ["MBJ"], "_mbj", "Montego Bay (MBJ)")[:1]
    s += bts_series(agg, months, ["KIN"], "_kin", "Kingston (KIN)")[:1]
    write("JM", s, [
        "Serie sustituta: las estadísticas nacionales de turismo de Jamaica (JTB) no "
        "están disponibles en formato reutilizable, por lo que el tráfico aéreo "
        "de origen estadounidense es la señal de referencia.",
        "Incluye los dos aeropuertos internacionales con servicio a EE.UU.: Sangster "
        "(MBJ, turismo) y Norman Manley (KIN, Kingston). No incluye Ian Fleming (OCJ).",
        BTS_NOTE_DIR, BTS_NOTE_COV, BTS_NOTE_PR, BTS_NOTE_SEAT, BTS_NOTE_ZERO,
        BTS_NOTE_REV,
        "No se encontró fuente oficial jamaiquina (JCAA) con operaciones o asientos "
        "mensuales por aeropuerto en formato descargable; no se usó ningún agregador "
        "privado.",
        "Validación de consistencia: el flujo EE.UU.→MBJ y MBJ→EE.UU. de 2024 "
        "difiere 1,9 % (1.826.014 vs 1.861.449 pasajeros) y el factor de ocupación "
        "implicado es 82,7 %, coherente con operación comercial real.",
    ])

    # ---------------- BS (Bahamas) --------------------------------------
    s = bts_series(agg, months, ["NAS"], "", "Nassau (NAS)")
    write("BS", s, [
        "Sólo Nassau/Lynden Pindling (NAS). No incluye Freeport (FPO), Exuma (GGT) "
        "ni los demás aeropuertos de las Family Islands con servicio a EE.UU.",
        BTS_NOTE_DIR, BTS_NOTE_COV, BTS_NOTE_PR, BTS_NOTE_SEAT, BTS_NOTE_ZERO,
        BTS_NOTE_REV,
        "No se localizó un boletín mensual descargable de la Bahamas Civil Aviation "
        "Authority con operaciones o asientos por aeropuerto; no se usó ningún "
        "agregador privado.",
    ])

    # ---------------- MX-CUN / MX-SJD -----------------------------------
    for dest, apt, iata, lab in (("MX-CUN", "CANCUN", "CUN", "Cancún (CUN)"),
                                 ("MX-SJD", "SAN JOSE DEL CABO", "SJD",
                                  "Los Cabos (SJD)")):
        s = bts_series(agg, months, [iata], "", lab)
        s += afac_series(a, apt, afac_url, afac_page, lab)
        write(dest, s, [
            BTS_NOTE_DIR, BTS_NOTE_COV, BTS_NOTE_PR, BTS_NOTE_SEAT, BTS_NOTE_ZERO,
            BTS_NOTE_REV,
            "AFAC no publica asientos: la métrica oficial de capacidad disponible es "
            "OPERACIONES (movimientos de aeronave). Una 'operación' es un despegue O "
            "un aterrizaje, de modo que un vuelo redondo cuenta 2; incluye servicio "
            "regular, fletamento (charter), aviación general y taxi aéreo.",
            "'air_operations' = nacionales + internacionales; 'air_operations_intl' = "
            "sólo vuelos internacionales (cualquier país, no sólo EE.UU.).",
            "Los pasajeros de AFAC por aeropuerto ya están en data/%s.json "
            "(passengers_air_*); aquí sólo se añaden las operaciones." % dest,
            "Validación cruzada BTS↔AFAC en %s: los vuelos T-100 en ambos sentidos "
            "%s↔EE.UU. representan entre 53 %% y 68 %% de las operaciones "
            "internacionales que publica AFAC para ese aeropuerto (máximo en "
            "temporada baja, cuando cae el tráfico de Canadá y Europa), "
            "siempre por debajo del total: coherente." % (iata, iata),
        ])

    # ---------------- DO (Dominican Republic) ---------------------------
    s = bts_series(agg, months, ["PUJ", "SDQ"], "", "PUJ + SDQ")
    s += bts_series(agg, months, ["PUJ"], "_puj", "Punta Cana (PUJ)")[:1]
    s += bts_series(agg, months, ["SDQ"], "_sdq",
                    "Santo Domingo Las Am\u00e9ricas (SDQ)")[:1]
    s += jac_series()
    write("DO", s, [
        "Incluye los dos aeropuertos con m\u00e1s tr\u00e1fico estadounidense: Punta Cana "
        "(PUJ) y Las Am\u00e9ricas / Santo Domingo (SDQ). No incluye Puerto Plata (POP), "
        "Santiago (STI), La Romana (LRM), Saman\u00e1 (AZS) ni La Isabela (JBQ), que "
        "tambi\u00e9n tienen servicio a EE.UU.",
        BTS_NOTE_DIR, BTS_NOTE_COV, BTS_NOTE_PR, BTS_NOTE_SEAT, BTS_NOTE_ZERO,
        BTS_NOTE_REV,
        "Validaci\u00f3n cruzada: los pasajeros T-100 EE.UU.\u2192PUJ equivalen al "
        "43\u201348 % de las llegadas a\u00e9reas de no residentes por PUJ que publica el "
        "Banco Central (serie arrivals_air_nonresident_puj en data/DO.json). No son "
        "universos id\u00e9nticos \u2014 T-100 cuenta pasajeros transportados (incluye "
        "residentes dominicanos y conexiones v\u00eda EE.UU.), el BCRD cuenta llegadas de "
        "no residentes desde cualquier origen \u2014 pero la proporci\u00f3n es estable y "
        "consistente con que EE.UU. sea el mayor mercado.",
    ] + JAC_NOTES)

    return agg, months, a, afac_url, afac_page


if __name__ == "__main__":
    main()
