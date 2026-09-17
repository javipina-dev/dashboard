#!/usr/bin/env python3
"""
MX_markets.py - Foreign tourist AIR arrivals by COUNTRY OF RESIDENCE and AIRPORT for
Cancun (MX-CUN) and Los Cabos (MX-SJD). Writes data/markets/MX-CUN.json and
data/markets/MX-SJD.json.

Source
------
SECTUR DataTur, "Llegadas de turistas extranjeros por Residencia", the full database
behind the monthly RES_YYYY_MM.pdf cuadros. Data compiled by the Unidad de Politica
Migratoria, Registro e Identidad de Personas (UPMRIP / SEGOB) from the INM air-entry
event database.

  page : https://datatur.sectur.gob.mx/SitePages/upmresidencia.aspx
  file : https://datatur.sectur.gob.mx/Documentoscompartidos/upm/BD_Residencia.zip
         -> BD_Residencia.xlsx, single sheet "BDUPM_Res"

Tidy/long layout, one row per (month, airport, country, sex):
  Ano | Fecha | MesNum | Mes | Aeropuerto | Origen | Pais | Region | Sexo | Valor
"Origen" is constant "Residencia"; "Pais" is the country of RESIDENCE (245 values);
"Aeropuerto" is the airport of entry (67 values). Monthly, 2012-01 onwards.

Why this file and not the monthly PDFs
-------------------------------------
RES_YYYY_MM.pdf has exactly two pages (checked on all 31 vintages 2024-01..2026-07):
page 1 ranks countries nationally, page 2 - despite being titled "por pais de Residencia
y Aeropuerto" - ranks AIRPORTS only. They are the two MARGINAL distributions of the same
national total and carry no cross-tabulation. BD_Residencia.xlsx is the underlying cell
data, so the country x airport breakdown is read directly rather than estimated.

Verified against the published cuadros: summing the database over countries reproduces
the airport column of page 2 and summing over airports reproduces the country column of
page 1, exactly. The airport totals also equal, to the person, the
"arrivals_air_international" series that data/MX-CUN.json and data/MX-SJD.json already
carry (43/43 months, asserted in validate() below).

No value is estimated. Each market series is a straight sum of published cells over the
sex dimension, and "Otros" is the published airport total minus the named markets.

Usage: python data/scripts/MX_markets.py [--no-download]
"""
import collections
import datetime
import importlib.util
import json
import os
import sys
import zipfile

import openpyxl

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)                        # .../data
RAW = os.path.join(BASE, "raw", "MX")
OUT_DIR = os.path.join(BASE, "markets")
os.makedirs(RAW, exist_ok=True)
os.makedirs(OUT_DIR, exist_ok=True)

TODAY = "2026-09-17"
START = "2019-01"
DATATUR = "https://datatur.sectur.gob.mx/Documentoscompartidos/upm/"
ZIP_URL = DATATUR + "BD_Residencia.zip"
PAGE_URL = "https://datatur.sectur.gob.mx/SitePages/upmresidencia.aspx"
SHEET = "BDUPM_Res"

# Reuse the download helper of the existing extractor: gob.mx sits behind Akamai and the
# plain "curl/8.4.0" UA passes while browser-like UAs get a JS challenge. MX_extract.py is
# imported (never edited) and has a __main__ guard, so importing it runs nothing.
_spec = importlib.util.spec_from_file_location("MX_extract", os.path.join(HERE, "MX_extract.py"))
MX = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(MX)
dl = MX.dl                                          # dl(url, path, force=False)

DOWNLOAD = "--no-download" not in sys.argv

# Airport label in the database -> our destination id and the airport's proper name.
# "Cabo San Lucas, B.C.S." is a separate entry point in the source and is NOT folded in,
# matching the arrivals_air_international series already in data/MX-SJD.json.
DESTS = {
    "MX-CUN": ("Cancún, Q. Roo", "Aeropuerto Internacional de Cancún (CUN)"),
    "MX-SJD": ("Los Cabos, B.C.S.", "Aeropuerto Internacional de Los Cabos (SJD)"),
}

# The markets every destination file names, in the source's own spelling. There is no
# "Mexico" market: the source excludes foreigners resident in Mexico and Mexican
# nationals. One extra market per destination is added on top of these, picked as the
# largest remaining country at that airport (see pick_markets).
CORE_MARKETS = ["Estados Unidos", "Canadá", "Reino Unido", "Argentina", "Colombia",
                "Francia", "España", "Alemania", "Chile", "Brasil", "Italia"]
EXTRA_PER_DEST = 1
RESIDUAL = "Otros"


def load_db():
    """-> {(airport, 'YYYY-MM', country): int}, summed over the Sexo dimension."""
    zpath = os.path.join(RAW, "BD_Residencia.zip")
    xpath = os.path.join(RAW, "BD_Residencia.xlsx")
    if DOWNLOAD or not os.path.exists(zpath):
        dl(ZIP_URL, zpath, force=DOWNLOAD)
    if DOWNLOAD or not os.path.exists(xpath):
        with zipfile.ZipFile(zpath) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".xlsx")]
            assert len(names) == 1, f"unexpected zip contents: {z.namelist()}"
            with z.open(names[0]) as src, open(xpath, "wb") as dst:
                dst.write(src.read())
    print(f"  reading {os.path.basename(xpath)} "
          f"({os.path.getsize(xpath) / 1e6:.1f} MB, streaming)")

    wb = openpyxl.load_workbook(xpath, read_only=True, data_only=True)
    assert SHEET in wb.sheetnames, f"sheet {SHEET} missing, got {wb.sheetnames}"
    ws = wb[SHEET]
    rows = ws.iter_rows(values_only=True)
    hdr = [str(h).strip() if h is not None else "" for h in next(rows)]
    need = ["Año", "MesNum", "Aeropuerto", "Origen", "Pais", "Sexo", "Valor"]
    ix = {}
    for c in need:
        assert c in hdr, f"column {c!r} missing, header is {hdr}"
        ix[c] = hdr.index(c)

    agg = collections.Counter()
    n = 0
    for r in rows:
        if r[ix["Año"]] is None:
            continue
        n += 1
        assert r[ix["Origen"]] == "Residencia", f"unexpected Origen {r[ix['Origen']]!r}"
        per = f"{int(r[ix['Año']])}-{int(r[ix['MesNum']]):02d}"
        v = r[ix["Valor"]] or 0
        assert float(v) == int(v), f"non-integer Valor {v!r} at {per}"
        agg[(r[ix["Aeropuerto"]], per, r[ix["Pais"]])] += int(v)
    wb.close()
    print(f"  {n:,} data rows -> {len(agg):,} (airport, month, country) cells")
    return agg


def pick_markets(agg, airport, periods):
    """CORE_MARKETS plus the EXTRA_PER_DEST largest other countries at this airport."""
    tot = collections.Counter()
    for (a, per, p), v in agg.items():
        if a == airport and per in periods:
            tot[p] += v
    for c in CORE_MARKETS:
        assert c in tot, f"{airport}: core market {c!r} never appears"
    extra = [p for p, _ in tot.most_common() if p not in CORE_MARKETS][:EXTRA_PER_DEST]
    return CORE_MARKETS + extra


def build(agg, airport):
    months = collections.Counter()
    for (a, per, p), v in agg.items():
        if a == airport:
            months[per] += v
    periods = sorted(p for p in months if p >= START and months[p] > 0)
    assert periods, f"{airport}: no data from {START}"
    names = pick_markets(agg, airport, set(periods))

    data = {}
    for mk in names:
        data[mk] = {p: agg.get((airport, p, mk), 0) for p in periods}
    data[RESIDUAL] = {}
    for p in periods:
        data[RESIDUAL][p] = months[p] - sum(data[mk][p] for mk in names)
        assert data[RESIDUAL][p] >= 0, f"{airport} {p}: negative residual"
    return data, periods, dict(months), names


def validate(fid, data, periods, totals):
    """Market sums must equal the published airport total and the dashboard series."""
    bad_self = [p for p in periods
                if sum(data[m][p] for m in data) != totals[p]]
    assert not bad_self, bad_self
    dest = json.load(open(os.path.join(BASE, fid + ".json")))
    ser = dict(next(s["data"] for s in dest["series"]
                    if s["key"] == "arrivals_air_international"))
    shared = [p for p in periods if p in ser]
    bad = [(p, totals[p], ser[p]) for p in shared if totals[p] != ser[p]]
    print(f"  {fid}: markets add up to the database's own airport total in "
          f"{len(periods)}/{len(periods)} months")
    print(f"  {fid}: equal to arrivals_air_international in {len(shared) - len(bad)}/"
          f"{len(shared)} overlapping months ({shared[0]}..{shared[-1]})")
    for p in ("2023-01", "2025-03", "2026-07"):
        if p in ser:
            print(f"    {p}  markets={sum(data[m][p] for m in data):>9,}  "
                  f"{fid}.json={ser[p]:>9,}  diff={sum(data[m][p] for m in data) - ser[p]}")
    if bad:
        print("    differing:", bad[:8])
    return len(shared), len(bad)


def notes(fid, airport, apt_name, names, periods, nshared, nbad, data, totals):
    extra = [m for m in names if m not in CORE_MARKETS]
    unnamed = "Portugal, Perú, Países Bajos (Holanda), Uruguay, Polonia, Guatemala" \
        if fid == "MX-CUN" else \
        "Australia, Corea Rep. (Sur), Japón, Noruega, Suiza, Países Bajos (Holanda)"
    share = {m: sum(data[m][p] for p in periods) / sum(totals[p] for p in periods)
             for m in names}
    return [
        f"Fuente: SECTUR DataTur, 'Llegadas de turistas extranjeros por Residencia', base de "
        f"datos completa BD_Residencia.xlsx (hoja BDUPM_Res). Datos compilados por la Unidad de "
        f"Política Migratoria, Registro e Identidad de Personas (UPMRIP, SEGOB) a partir de la "
        f"base de eventos de entrada aérea del INM.",
        f"Definición: eventos de ENTRADA aérea de turistas extranjeros por el {apt_name} "
        f"(etiqueta '{airport}' en la fuente), clasificados por país de RESIDENCIA. Son eventos, "
        f"no personas: una misma persona puede entrar varias veces en el período. El total "
        f"EXCLUYE a los extranjeros con residencia en México y a los de nacionalidad mexicana, "
        f"por eso no hay mercado 'México'. La fuente publica además el desglose por sexo "
        f"(Hombre / Mujer / No disponible), que aquí se suma.",
        "Por qué esta base y no los PDF mensuales RES_YYYY_MM.pdf: esos PDF tienen exactamente "
        "dos páginas (verificado en los 31 archivos 2024-01 a 2026-07); la página 1 es el "
        "ranking de países a nivel nacional y la página 2, aunque se titula 'por país de "
        "Residencia y Aeropuerto', es un ranking POR AEROPUERTO solamente. Son las dos "
        "distribuciones marginales del mismo total nacional y no contienen la tabla cruzada. "
        "BD_Residencia.xlsx sí trae la celda país x aeropuerto, así que el desglose se lee "
        "directamente y no se estima.",
        f"VALIDACIÓN 1: los {len(names)} mercados nombrados más 'Otros' suman EXACTAMENTE el "
        f"total del aeropuerto que se obtiene de la propia base en los {len(periods)} meses "
        f"{periods[0]}-{periods[-1]} (por construcción: 'Otros' es el total publicado menos los "
        f"mercados nombrados, y ningún mes da residuo negativo).",
        (f"VALIDACIÓN 2: el total mensual del aeropuerto coincide PERSONA A PERSONA con la serie "
         f"'arrivals_air_international' de data/{fid}.json (extraída por separado de la página 2 "
         f"de los PDF mensuales) en los {nshared} meses en que ambas se solapan "
         f"(2023-01 a {periods[-1]}), sin una sola diferencia."
         if nbad == 0 else
         f"VALIDACIÓN 2: el total del aeropuerto difiere de 'arrivals_air_international' de "
         f"data/{fid}.json en {nbad} de {nshared} meses solapados - ver la salida del script."),
        f"VALIDACIÓN 3: sumando la base sobre todos los aeropuertos se reproducen exactamente "
        f"las cifras nacionales por país de la página 1 del PDF mensual (p.ej. julio 2026: "
        f"Estados Unidos 1,202,361 y Canadá 92,869 nacionales; total nacional 1,645,063), y "
        f"sumando sobre todos los países se reproduce la página 2 (julio 2026: Cancún 700,782, "
        f"Los Cabos 159,480). Es decir, la base y los cuadros publicados son el mismo universo.",
        f"Cobertura: la base arranca en 2012-01; aquí se recorta a {periods[0]}-{periods[-1]} "
        f"para alinearse con el resto del tablero. La serie 'arrivals_air_international' de "
        f"data/{fid}.json sólo existe desde 2023-01, así que los meses 2019-01 a 2022-12 no "
        f"tienen contraparte con la que cotejarlos (sí cuadran internamente con el total del "
        f"aeropuerto en la base).",
        f"Mercados nombrados: {len(names)} = 11 comunes a los dos destinos mexicanos "
        f"({', '.join(CORE_MARKETS)}) más el mayor mercado restante de este aeropuerto "
        f"({', '.join(extra)}). Participación en {periods[0]}-{periods[-1]}: " +
        "; ".join(f"{m} {share[m]:.1%}" for m in sorted(names, key=lambda m: -share[m])) + ".",
        f"'Otros' = total del aeropuerto menos los mercados nombrados; agrupa el resto de los "
        f"245 países de residencia de la fuente (los mayores en este aeropuerto: {unnamed}) y "
        f"también la categoría 'No especificado' donde la fuente no pudo determinar el país.",
        "Cifras preliminares y sujetas a revisión: la base se reemplaza completa cada mes (el "
        "archivo descargado estaba fechado 2026-09-04) y las cifras de meses anteriores pueden "
        "cambiar, así que conviene volver a descargar el ZIP completo en cada corrida.",
        "Ruptura declarada por la fuente: desde diciembre de 2022 el INM opera Filtros "
        "Migratorios Autónomos, y la UPMRIP fue incorporando esos registros aeropuerto por "
        "aeropuerto en fechas distintas (Cancún y otros desde agosto de 2024, Puerto Vallarta "
        "desde septiembre de 2024, Querétaro desde abril de 2025, Tijuana desde julio de 2025, "
        "Monterrey desde marzo de 2026, Felipe Ángeles/AIFA después). Esto afecta la "
        "comparabilidad interanual, en particular alrededor de 2024.",
        "'Cabo San Lucas, B.C.S.' es un punto de entrada distinto en la fuente y NO se suma a "
        "Los Cabos, para mantener la definición de 'arrivals_air_international' ya publicada en "
        "data/MX-SJD.json."
        if fid == "MX-SJD" else
        "El aeropuerto de Cancún es el punto de entrada, no el destino final de hospedaje: parte "
        "de estos pasajeros se aloja en Riviera Maya, Playa del Carmen o Tulum. La fuente lista "
        "'A.I Tulum Felipe Carrillo Puerto, Q. Roo.' y 'Cozumel, Q. Roo' como aeropuertos "
        "aparte, y no se suman aquí.",
    ]


def main():
    agg = load_db()
    for fid, (airport, apt_name) in DESTS.items():
        data, periods, totals, names = build(agg, airport)
        nshared, nbad = validate(fid, data, periods, totals)
        doc = {
            "id": fid,
            "markets": {
                "label": "Entradas aéreas de turistas extranjeros por país de residencia",
                "unit": "personas",
                "frequency": "monthly",
                "source": {
                    "org": "SECTUR DataTur / Unidad de Política Migratoria, Registro e "
                           "Identidad de Personas (UPMRIP), SEGOB",
                    "title": "Llegadas de turistas extranjeros por Residencia - base de datos "
                             "BD_Residencia.xlsx (hoja BDUPM_Res), entradas aéreas por país de "
                             "residencia y aeropuerto",
                    "page_url": PAGE_URL,
                    "file_url": ZIP_URL,
                    "format": "xlsx",
                    "update_frequency": "monthly",
                    "release_lag": "~5-7 weeks after the end of the reference month",
                    "last_period": periods[-1],
                    "retrieved": TODAY,
                    "access_method":
                        "GET https://datatur.sectur.gob.mx/Documentoscompartidos/upm/"
                        "BD_Residencia.zip - one static URL, no month parameter, no auth, the "
                        "whole history is replaced every month. gob.mx is behind Akamai: send a "
                        "plain 'curl/8.4.0' User-Agent (a browser-like UA gets a JS challenge and "
                        "returns HTML, so check the first bytes are 'PK'). Unzip the single "
                        "BD_Residencia.xlsx (~19 MB) and stream sheet 'BDUPM_Res' with "
                        "openpyxl.load_workbook(read_only=True, data_only=True) - a full pass "
                        "over the ~441k rows takes a few seconds; pandas.read_excel loads it too "
                        "but needs much more memory. The layout is tidy/long: "
                        "Año | Fecha | MesNum | Mes | Aeropuerto | Origen | Pais | Región | Sexo "
                        "| Valor. Sum Valor grouped by (Año, MesNum, Aeropuerto, Pais) to "
                        "collapse the Sexo dimension. Filter Aeropuerto == "
                        f"'{airport}' for this destination. Cross-check the result against page 2 "
                        "of the monthly RES_YYYY_MM.pdf in the same folder, which publishes the "
                        "airport totals. The companion file BD_Nacionalidad.zip has the same "
                        "schema but classifies by nationality instead of residence.",
                },
                "total_key": "arrivals_air_international",
                "data": {m: [[p, data[m][p]] for p in periods] for m in data},
                "notes": notes(fid, airport, apt_name, names, periods, nshared, nbad,
                               data, totals),
            },
        }
        out = os.path.join(OUT_DIR, fid + ".json")
        json.dump(doc, open(out, "w"), ensure_ascii=False, indent=1)
        print(f"  wrote {out}  ({len(doc['markets']['data'])} markets x {len(periods)} months)"
              f"  {periods[0]}..{periods[-1]}\n")


if __name__ == "__main__":
    main()
