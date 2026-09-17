#!/usr/bin/env python
"""
DO_markets.py - Monthly non-resident air arrivals to the Dominican Republic broken
down by COUNTRY OF RESIDENCE, from the Banco Central de la Republica Dominicana.

Source
------
BCRD, "Sector turismo" statistics page
  page : https://www.bancentral.gov.do/a/d/2537-sector-turismo
  files: https://cdn.bancentral.gov.do/documents/estadisticas/sector-turismo/documents/
         lleg_residencia_YYYY.xls          (one file per year, 1999 .. current year)
  table: "LLEGADA MENSUAL DE PASAJEROS, VIA AEREA, POR PAIS DE RESIDENCIA"

Each yearly .xls holds several stacked tables in sheet 0: first "TOTAL NACIONAL <year>",
then one table per international airport (Las Americas, Puerto Plata, Punta Cana, ...).
Only the first (national) table is used here.

Row hierarchy of the national table (all "via aerea"):
    TOTAL
      RESIDENTES            -> DOMINICANOS / EXTRANJEROS
      NO RESIDENTES         -> DOMINICANOS / EXTRANJEROS
        AMERICA DEL NORTE, AMERICA CENTRAL Y EL CARIBE, AMERICA DEL SUR,
        ASIA, EUROPA, RESTO DEL MUNDO      (each with member countries + "Otros")

IMPORTANT (verified numerically for every year 2019-2026, see validate() below):
the six regional blocks and their member countries add up to
"NO RESIDENTES -> EXTRANJEROS", i.e. the country-of-residence detail covers only
FOREIGN non-residents. Non-resident Dominicans (the diaspora, "NO RESIDENTES ->
DOMINICANOS") have no country-of-residence breakdown in this file, so they are
carried as their own published series "Dominicanos no residentes". With that row
included the markets add up exactly to NO RESIDENTES = data/DO.json
"arrivals_air_nonresident".

No value is estimated. Every number is a cell of the official file; the only
transformations are (a) rounding the BCRD weighted (fractional) passenger counts to
integers and (b) computing "Otros" as the published NO RESIDENTES total minus the
named rows, which is exactly the residual of the small countries the BCRD itself
already groups under "Otros" in each region (+/- 1 person of rounding).

Usage: python data/scripts/DO_markets.py [--no-download]
"""
import datetime as dt
import json
import re
import sys
import unicodedata
from pathlib import Path

import pandas as pd
import requests

BASE = Path(__file__).resolve().parents[1]              # .../data
RAW = BASE / "raw" / "DO"
OUT = BASE / "markets" / "DO.json"
RAW.mkdir(parents=True, exist_ok=True)
OUT.parent.mkdir(parents=True, exist_ok=True)

BCRD_TUR = "https://cdn.bancentral.gov.do/documents/estadisticas/sector-turismo/documents/"
BCRD_TUR_PAGE = "https://www.bancentral.gov.do/a/d/2537-sector-turismo"
FILE_TMPL = "lleg_residencia_{year}.xls"

START_YEAR = 2019
RETRIEVED = "2026-09-17"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
DOWNLOAD = "--no-download" not in sys.argv

# The named markets we keep, mapped to the row label(s) of the BCRD file (normalised).
# Everything else the file lists (Peru, Panama, Holanda, Suiza, Portugal, Polonia,
# China, Australia, ... and the BCRD's own per-region "Otros") falls into "Otros".
# Reino Unido: up to the 2021 file the BCRD listed "Inglaterra" and "Escocia" as two
# separate rows and had no "Reino Unido" row; from the 2022 file on there is a single
# "Reino Unido" row. The two old rows are summed so the series is continuous.
MARKETS = [
    ("Estados Unidos", ["estados unidos"]),
    ("Canadá",         ["canada"]),
    ("Argentina",      ["argentina"]),
    ("Colombia",       ["colombia"]),
    ("Puerto Rico",    ["puerto rico"]),
    ("Reino Unido",    ["reino unido", "inglaterra", "escocia"]),
    ("España",         ["espana"]),
    ("Francia",        ["francia"]),
    ("Alemania",       ["alemania"]),
    ("Brasil",         ["brasil"]),
    ("Chile",          ["chile"]),
    ("Italia",         ["italia"]),
    ("México",         ["mexico"]),
]
DIASPORA = "Dominicanos no residentes"
RESIDUAL = "Otros"

REGIONS = {"america del norte", "america central y el caribe", "america del sur",
           "asia", "europa", "resto del mundo"}


def norm(s):
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().lower()


def num(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, str):
        v = v.strip().replace(",", "")
        if v in ("", "-", "n.d.", "nd", "n/d"):
            return None
        try:
            return float(v)
        except ValueError:
            return None
    return float(v)


def fetch(year):
    name = FILE_TMPL.format(year=year)
    path = RAW / name
    if DOWNLOAD:
        r = requests.get(BCRD_TUR + name, headers=UA, timeout=120)
        if r.status_code != 200:
            print(f"  ! {r.status_code} {name}")
            return None
        path.write_bytes(r.content)
        print(f"  downloaded {name} ({len(r.content)} bytes)")
    return path if path.exists() else None


def parse_year(path, year):
    """-> {label: {'YYYY-MM': float}} for the NATIONAL table of one yearly file.

    Labels returned: 'NO RESIDENTES', 'NO RESIDENTES/DOMINICANOS',
    'NO RESIDENTES/EXTRANJEROS', each region name, and each country (normalised),
    keyed as 'region/country' so the per-region 'Otros' rows stay distinct.
    """
    d = pd.read_excel(path, sheet_name=0, header=None)

    # --- locate the label column: the one holding the "RESIDENCIA" stub head.
    label_col = None
    for c in range(min(4, d.shape[1])):
        for r in range(min(12, len(d))):
            if norm(d.iloc[r, c]) == "residencia":
                label_col = c
                break
        if label_col is not None:
            break
    if label_col is None:  # 2019 layout: stub head is in col 1, title in col 0
        for c in range(min(4, d.shape[1])):
            if any(norm(d.iloc[r, c]) == "no residentes" for r in range(min(25, len(d)))):
                label_col = c
                break
    assert label_col is not None, f"{path.name}: label column not found"

    # --- month columns: the row that reads TOTAL | ENE. | FEB. | ... after the stub head
    hdr_row = None
    for r in range(min(20, len(d))):
        if norm(d.iloc[r, label_col + 1]) == "total" and norm(d.iloc[r, label_col + 2]).startswith("ene"):
            hdr_row = r
            break
    assert hdr_row is not None, f"{path.name}: month header row not found"
    mcols = [label_col + 2 + m for m in range(12)]
    assert mcols[-1] < d.shape[1], f"{path.name}: only {d.shape[1]} columns"

    out, section, region = {}, None, None
    for r in range(hdr_row + 1, len(d)):
        lab = norm(d.iloc[r, label_col])
        if not lab:
            continue
        # stop at the end of the national table (footnote, or the next stacked table)
        if lab.startswith(("*cifras", "1/", "llegada mensual", "aeropuerto internacional")):
            break
        if lab == "residentes":
            section = "res"
            continue
        if lab == "no residentes":
            section = "nores"
            key = "NO RESIDENTES"
        elif lab in ("dominicanos", "extranjeros"):
            if section != "nores":
                continue
            key = "NO RESIDENTES/" + lab.upper()
        elif lab in REGIONS:
            region = lab
            key = region
        elif lab == "total":
            continue
        else:
            if section != "nores" or region is None:
                continue
            key = f"{region}/{lab}"

        vals = {}
        for m, c in enumerate(mcols):
            v = num(d.iloc[r, c])
            if v is not None:
                vals[f"{year}-{m + 1:02d}"] = v
        out[key] = vals
    return out


def build():
    per_year = {}
    for year in range(START_YEAR, int(RETRIEVED[:4]) + 1):
        p = fetch(year)
        if p is None:
            continue
        per_year[year] = parse_year(p, year)
        print(f"  parsed {year}: {len(per_year[year])} rows")

    # --- merge, and work out which months are actually published.
    # In the current-year file the months not yet released are stored as 0.
    periods = []
    nores, dominicanos, extranjeros = {}, {}, {}
    country = {}                                   # normalised country -> {period: value}
    for year, rows in sorted(per_year.items()):
        nr = rows["NO RESIDENTES"]
        pubs = sorted(p for p, v in nr.items() if v and v > 0)
        # trim trailing unpublished (zero) months of the current year
        pubs = [p for p in pubs]
        for p in pubs:
            periods.append(p)
            nores[p] = nr[p]
            dominicanos[p] = rows["NO RESIDENTES/DOMINICANOS"][p]
            extranjeros[p] = rows["NO RESIDENTES/EXTRANJEROS"][p]
        for key, vals in rows.items():
            if "/" not in key or key.startswith("NO RESIDENTES/"):
                continue
            cname = key.split("/", 1)[1]
            if cname == "otros":
                continue                           # per-region residual -> our "Otros"
            for p in pubs:
                if p in vals:
                    country.setdefault(cname, {})[p] = vals[p]
    periods = sorted(set(periods))
    print(f"  periods: {periods[0]} .. {periods[-1]} ({len(periods)})")

    # --- sanity: regional blocks must add up to NO RESIDENTES / EXTRANJEROS
    for year, rows in sorted(per_year.items()):
        for p in sorted(rows["NO RESIDENTES/EXTRANJEROS"]):
            if p not in periods:
                continue
            s = sum(rows[r].get(p, 0.0) for r in REGIONS if r in rows)
            e = rows["NO RESIDENTES/EXTRANJEROS"][p]
            assert abs(s - e) < 1.0, f"{p}: regions {s:.1f} != extranjeros {e:.1f}"
    print("  OK regional blocks sum to NO RESIDENTES/EXTRANJEROS in every month")

    # --- assemble the market series (raw, un-rounded, exactly as published)
    labels = [m[0] for m in MARKETS] + [DIASPORA, RESIDUAL]
    raw = {m: {} for m in labels}
    for label, keys in MARKETS:
        present = [k for k in keys if k in country]
        if not present:
            raise RuntimeError(f"market '{label}': none of {keys} found in any yearly file")
        for p in periods:
            parts = [country[k][p] for k in present if p in country[k]]
            if not parts:
                raise RuntimeError(f"{label}: no source row for {p} (tried {present})")
            raw[label][p] = sum(parts)
    for p in periods:
        raw[DIASPORA][p] = dominicanos[p]
        # residual of the countries we do not name = published foreign non-residents
        # minus the named country rows (all of which are inside that same total).
        raw[RESIDUAL][p] = extranjeros[p] - sum(raw[m][p] for m, _ in MARKETS)
        assert raw[RESIDUAL][p] >= -0.001, f"{p}: negative residual {raw[RESIDUAL][p]}"
        assert abs(sum(raw[m][p] for m in labels) - nores[p]) < 0.5, \
            f"{p}: components do not add to NO RESIDENTES"

    # --- round to integers with largest-remainder apportionment, so the integer
    # series add up EXACTLY to the published (rounded) NO RESIDENTES total while each
    # value stays within one person of the figure the BCRD publishes. Needed because
    # the BCRD reports weighted (fractional) counts; plain independent rounding of
    # ~15 components can miss the total by a few units, which matters in the
    # April-June 2020 trough when the whole month is a few hundred passengers.
    data = {m: {} for m in labels}
    for p in periods:
        target = round(nores[p])
        floors = {m: int(raw[m][p] // 1) for m in labels}
        rest = target - sum(floors.values())
        assert 0 <= rest <= len(labels), f"{p}: apportionment off by {rest}"
        order = sorted(labels, key=lambda m: (raw[m][p] % 1), reverse=True)
        for m in labels:
            data[m][p] = floors[m] + (1 if m in order[:rest] else 0)
        assert sum(data[m][p] for m in labels) == target, p
        assert all(data[m][p] >= 0 for m in labels), p
    return data, periods, nores, extranjeros, dominicanos


def validate(data, periods, nores, extranjeros, dominicanos):
    """Check the market sums against data/DO.json 'arrivals_air_nonresident'."""
    do = json.load(open(BASE / "DO.json"))
    tot = dict(next(s["data"] for s in do["series"] if s["key"] == "arrivals_air_nonresident"))
    fgn = dict(next(s["data"] for s in do["series"] if s["key"] == "arrivals_air_nonresident_foreign"))
    bad = []
    print("\n  validation vs data/DO.json arrivals_air_nonresident:")
    for p in periods:
        s = sum(data[m][p] for m in data)
        t = tot.get(p)
        if t is None:
            bad.append((p, s, None))
            continue
        if s != t:
            bad.append((p, s, t))
        if p in ("2019-01", "2019-07", "2020-02", "2021-09", "2022-06", "2023-12",
                 "2024-08", "2025-05", "2026-07"):
            print(f"    {p}  markets={s:>9,}  DO.json={t:>9,}  diff={s - t}")
    # and the country part alone vs arrivals_air_nonresident_foreign
    print("  country rows only vs arrivals_air_nonresident_foreign:")
    bad_f, maxdiff = [], 0
    for p in periods:
        s = sum(data[m][p] for m in data if m != DIASPORA)
        f = fgn.get(p)
        if f is None:
            continue
        maxdiff = max(maxdiff, abs(s - f))
        if abs(s - f) > 1:
            bad_f.append((p, s, f))
        if p in ("2019-01", "2023-12", "2026-07"):
            print(f"    {p}  markets(excl. diaspora)={s:>9,}  DO.json={f:>9,}  diff={s - f}")
    print(f"  mismatches vs arrivals_air_nonresident: {len(bad)} / {len(periods)} months")
    if bad:
        print("   ", bad[:12])
    print(f"  vs arrivals_air_nonresident_foreign: max |diff| = {maxdiff} person(s), "
          f"{len(bad_f)} / {len(periods)} months off by more than 1")
    if bad_f:
        print("   ", bad_f[:12])
    return len(bad) == 0, maxdiff


def main():
    data, periods, nores, extranjeros, dominicanos = build()
    ok_total, maxdiff_foreign = validate(data, periods, nores, extranjeros, dominicanos)
    last = periods[-1]

    doc = {
        "id": "DO",
        "markets": {
            "label": "Llegadas de no residentes vía aérea por país de residencia",
            "unit": "personas",
            "frequency": "monthly",
            "source": {
                "org": "Banco Central de la República Dominicana (BCRD)",
                "title": "Llegada mensual de pasajeros, vía aérea, por país de residencia "
                         "(Total nacional) - Estadísticas del sector turismo",
                "page_url": BCRD_TUR_PAGE,
                "file_url": BCRD_TUR + FILE_TMPL.format(year="{YYYY}"),
                "format": "xls",
                "update_frequency": "monthly",
                "release_lag": "~6-8 weeks after the end of the reference month",
                "last_period": last,
                "retrieved": RETRIEVED,
                "access_method":
                    "GET https://cdn.bancentral.gov.do/documents/estadisticas/sector-turismo/"
                    "documents/lleg_residencia_{YYYY}.xls for each year 2019..current (no auth, "
                    "stable names; the file list is on the 'Sector turismo' page, also served as "
                    "https://www.bancentral.gov.do/a/CustomView/2537-sector-turismo). Legacy BIFF "
                    "xls -> pandas.read_excel(sheet_name=0, header=None) with xlrd. Sheet 0 stacks "
                    "several tables: take only the first one, 'TOTAL NACIONAL <year>' (the ones "
                    "that follow are per-airport: Las Américas, Puerto Plata, Punta Cana, ...). "
                    "Find the label column by looking for the 'RESIDENCIA' stub head (col 0 for "
                    "2022+ and 2026, col 1 for 2019 and 2021), then TOTAL is label_col+1 and "
                    "ENE..DIC are label_col+2..label_col+13. Walk the rows keeping track of the "
                    "RESIDENTES / NO RESIDENTES section and of the current region heading "
                    "(AMERICA DEL NORTE, AMERICA CENTRAL Y EL CARIBE, AMERICA DEL SUR, ASIA, "
                    "EUROPA, RESTO DEL MUNDO). In the current-year file months not yet released "
                    "are written as 0 - drop them. Re-download every year on each run: the BCRD "
                    "revises past years (the 2022-2025 files were re-published in Aug-2026).",
            },
            "total_key": "arrivals_air_nonresident",
            "data": {m: [[p, data[m][p]] for p in periods] for m in data},
            "notes": [
                "Fuente: BCRD, 'Llegada mensual de pasajeros, vía aérea, por país de residencia', "
                "cuadro TOTAL NACIONAL de lleg_residencia_{YYYY}.xls. Un archivo por año; aquí se "
                f"unieron los años 2019-2026 ({periods[0]} a {last}).",
                "Definición: pasajeros NO RESIDENTES llegados por vía aérea, clasificados por país "
                "de residencia (no por nacionalidad; el BCRD publica la nacionalidad en un archivo "
                "aparte, lleg_nacionalidad_{YYYY}.xls). Excluye residentes dominicanos y "
                "extranjeros residentes que regresan al país, y excluye la llegada marítima.",
                "Estructura de la fuente: el desglose por país de residencia cubre únicamente a "
                "los NO RESIDENTES EXTRANJEROS. Se verificó mes a mes, para los 91 meses, que la "
                "suma de los seis bloques regionales (América del Norte, América Central y el "
                "Caribe, América del Sur, Asia, Europa, Resto del Mundo) iguala exactamente la "
                "fila 'NO RESIDENTES - EXTRANJEROS'.",
                "Los dominicanos no residentes (la diáspora, 1.5 millones en 2025) no tienen "
                "desglose por país de residencia en este archivo, por lo que se incluyen como una "
                "serie propia y publicada, 'Dominicanos no residentes'. Con ella, las series suman "
                "el total publicado de NO RESIDENTES.",
                ("VALIDACIÓN: la suma de todos los mercados iguala EXACTAMENTE la serie "
                 "'arrivals_air_nonresident' de data/DO.json (misma fuente BCRD, archivo "
                 f"lleg_total.xls) en los {len(periods)} meses {periods[0]}-{last}."
                 if ok_total else
                 "VALIDACIÓN: hay meses en que la suma de los mercados no iguala "
                 "'arrivals_air_nonresident' de data/DO.json - ver salida del script."),
                "Excluyendo 'Dominicanos no residentes', los 13 mercados por país más 'Otros' "
                "suman la serie 'arrivals_air_nonresident_foreign' de data/DO.json (no residentes "
                f"extranjeros) con una diferencia máxima de {maxdiff_foreign} persona(s), "
                "atribuible al redondeo (ver nota siguiente).",
                "'Otros' = no residentes extranjeros publicados menos los 13 países nombrados. "
                "Contiene las filas 'Otros' que el propio BCRD publica en cada región más los "
                "países que no se nombran aquí (los mayores: Perú, Panamá, Holanda, Suiza, "
                "Portugal, Polonia, Guatemala, Ecuador, Uruguay, Costa Rica, China).",
                "El BCRD publica estas cifras con decimales porque son conteos ponderados "
                "(expansión de la muestra del formulario de entrada / e-ticket). Para que las "
                "series enteras sumen exactamente el total publicado se redondea con reparto por "
                "mayor resto (largest remainder): cada valor queda a menos de una persona de la "
                "cifra decimal de la fuente y la suma cuadra al entero exacto. Sin esto, redondear "
                "15 componentes por separado desajusta el total en unas pocas unidades, lo que "
                "importa en el mínimo de abril-junio de 2020 (meses de apenas 217-1,021 no "
                "residentes por el cierre de fronteras).",
                "Las cifras del año en curso están 'sujetas a rectificación' y el BCRD "
                "revisa los archivos de años anteriores (los de 2022-2025 se re-publicaron en "
                "agosto de 2026): conviene re-descargar todos los años en cada corrida.",
                "Ruptura metodológica declarada por la fuente: hasta enero de 2021 el flujo se "
                "compilaba con los formularios físicos de la Dirección General de Migración; entre "
                "febrero y agosto de 2021 hubo un período de transición con el MITUR; desde "
                "septiembre de 2021 rige el e-ticket obligatorio (resolución JAC 178-2021) y la "
                "compilación quedó a cargo del MITUR, que suministra los datos al BCRD.",
                "Se mantienen 13 mercados nombrados + 'Dominicanos no residentes' + 'Otros'. Se "
                "incluyó Puerto Rico porque es el 5º mercado de residencia del país, por delante "
                "de varios europeos. El mismo archivo trae además el desglose por país de "
                "residencia para cada aeropuerto (Punta Cana, Las Américas, Puerto Plata, "
                "Samaná, Santiago, La Romana), que no se extrae aquí.",
            ],
        },
    }
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"\nwrote {OUT}  ({len(doc['markets']['data'])} markets x {len(periods)} months)")


if __name__ == "__main__":
    main()
