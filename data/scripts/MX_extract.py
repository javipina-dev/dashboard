#!/usr/bin/env python3
"""
MX_extract.py - Official data for Cancún (MX-CUN) and Los Cabos (MX-SJD).

Sources (all official):
  1. AFAC (SICT) "Estadística Operacional de Aeropuertos" xlsx (pivot cache holds the full DB)
     -> passengers (arrivals+departures) international / domestic, CUN and SJD, monthly.
  2. SECTUR DataTur (data from SEGOB-UPM / INM) monthly PDFs "Entradas aéreas de turistas
     extranjeros por país de residencia y aeropuerto" (RES_YYYY_MM.pdf)
     -> foreign tourist AIR ARRIVALS by airport (arrivals only), monthly from 2023-01.
  3. Banco de México SIE, cuadro CE36 "Cuenta de viajeros internacionales" (INEGI EVI data)
     -> NATIONAL receipts / number / average spend of non-border tourists by air.
  4. SEDETUR Quintana Roo - SITURQ "Indicadores turísticos" API -> Derrama económica Cancún.
  5. DataTur hotel monitoring, datos.gob.mx datastore (Base70centros) -> hotel tourist arrivals and
     average stay (turistas noche / llegadas) Cancún & Los Cabos.
  6. Secretaría de Economía, IED por entidad federativa y actividad económica (cifras actualizadas)
     -> quarterly FDI flows, state total and SCIAN 721.

Notes on access:
  * gob.mx and repodatos.atdt.gob.mx sit behind Akamai bot protection. A browser-like UA gets a JS
    challenge; the plain "curl/x.y" UA currently passes on gob.mx. repodatos.atdt.gob.mx returns 403
    to everything, so datos.gob.mx CKAN `datastore_search` API is used instead.
  * Banxico SIE export: POST the cuadro form with formatoCSV.x/y (no token needed).
  * SITURQ API needs the public bearer token embedded in the indicadores page + Origin header.
"""
import csv, io, json, os, re, subprocess, sys, zipfile, datetime
import xml.etree.ElementTree as ET
import requests
import pdfplumber
import openpyxl

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../data
RAW = os.path.join(BASE, "raw", "MX")
os.makedirs(os.path.join(RAW, "datatur"), exist_ok=True)
TODAY = "2026-09-17"
UA = "curl/8.4.0"  # passes Akamai on gob.mx / datatur; browser UAs get a JS challenge
S = requests.Session()
S.headers["User-Agent"] = UA
MES = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8,
       "sep": 9, "oct": 10, "nov": 11, "dic": 12}


def dl(url, path, force=False):
    if os.path.exists(path) and os.path.getsize(path) > 2000 and not force:
        return path
    r = S.get(url, timeout=180)
    r.raise_for_status()
    if r.content[:15].lower().startswith((b"<!doctype", b"<html")):
        raise RuntimeError(f"HTML/challenge instead of file: {url}")
    open(path, "wb").write(r.content)
    return path


def find_link(page, pattern):
    html = S.get(page, timeout=60).text
    m = re.search(pattern, html)
    if not m:
        raise RuntimeError(f"link {pattern} not found on {page}")
    u = m.group(0)
    return u if u.startswith("http") else "https://www.gob.mx" + u


# ---------------------------------------------------------------- 1. AFAC airports
def afac():
    page = "https://www.gob.mx/afac/acciones-y-programas/estadisticas-280404"
    url = find_link(page, r"/cms/uploads/attachment/file/\d+/producto-aeropuerto-2006-[^\"]+\.xlsx")
    path = dl(url, os.path.join(RAW, os.path.basename(url)))
    z = zipfile.ZipFile(path)
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    d = ET.fromstring(z.read("xl/pivotCache/pivotCacheDefinition1.xml"))
    fields = []
    for cf in d.find("m:cacheFields", ns):
        si = cf.find("m:sharedItems", ns)
        fields.append((cf.get("name"), [e.get("v") for e in si] if si is not None else []))
    names = [f[0] for f in fields]
    assert names[0].startswith("OPCIONES") and names[4].startswith("AEROPUERTO") and names[5].startswith("ENE"), names
    out = {}
    for r in ET.fromstring(z.read("xl/pivotCache/pivotCacheRecords1.xml")):
        vals = []
        for i, e in enumerate(r):
            tag = e.tag.split("}")[1]
            vals.append(fields[i][1][int(e.get("v"))] if tag == "x" else (None if tag == "m" else e.get("v")))
        opt, tipo, year, grp, apt = vals[:5]
        if not opt.startswith("PASAJEROS") or apt not in ("CANCUN", "SAN JOSE DEL CABO") or int(year) < 2019:
            continue
        kind = "intl" if tipo.startswith("INTERNACIONAL") else "dom"
        for m in range(12):
            v = vals[5 + m]
            out.setdefault((apt, kind), {})[f"{year}-{m + 1:02d}"] = int(float(v))
    # months not yet published are stored as 0 in the current year -> drop trailing zeros of last year
    last_year = max(int(k[:4]) for s in out.values() for k in s)
    for s in out.values():
        for k in sorted(s):
            if int(k[:4]) == last_year and s[k] == 0 and all(s[j] == 0 for j in s if j >= k and j[:4] == k[:4]):
                del s[k]
    return out, url, page


# ---------------------------------------------------------------- 2. DataTur / UPM foreign tourist air arrivals
BLOCK = re.compile(r"(\d{1,2})([A-ZÁÉÍÓÚÑ][^\d]*?)\s([\d,]+)\s([\d,]+)\s(-?[\d.]+%|n\.?[a-z]\.?)\s(-?[\d.]+%|n\.?[a-z]\.?|-)")


def upm_airport():
    now = datetime.date.fromisoformat(TODAY)
    vals = {}   # (airport, 'YYYY-MM') -> (vintage, value); later vintage (revised prior-year figure) wins
    files = []
    for y in range(2024, now.year + 1):
        for m in range(1, 13):
            if (y, m) >= (now.year, now.month):
                break
            fn = f"RES_{y}_{m:02d}.pdf"
            url = "https://datatur.sectur.gob.mx/Documentoscompartidos/upm/" + fn
            try:
                p = dl(url, os.path.join(RAW, "datatur", fn))
            except Exception:
                continue
            files.append((y, m, url))
            with pdfplumber.open(p) as pdf:
                txt = "\n".join(pg.extract_text() or "" for pg in pdf.pages)
            sec = txt[txt.find("por país de Residencia y Aeropuerto"):]
            for line in sec.splitlines():
                blocks = BLOCK.findall(line)
                if len(blocks) != 3:
                    continue
                name = blocks[2][1]
                key = "CUN" if name.startswith("Cancún") else "SJD" if name.startswith("Los Cabos") else None
                if not key:
                    continue
                prev, cur = int(blocks[2][2].replace(",", "")), int(blocks[2][3].replace(",", ""))
                vint = y * 100 + m
                for per, v in ((f"{y - 1}-{m:02d}", prev), (f"{y}-{m:02d}", cur)):
                    if (key, per) not in vals or vals[(key, per)][0] < vint:
                        vals[(key, per)] = (vint, v)
    out = {}
    for (k, per), (_, v) in vals.items():
        out.setdefault(k, {})[per] = v
    return out, files


# ---------------------------------------------------------------- 3. Banxico CE36 (national)
def banxico():
    series = ["SE28552", "SE28570", "SE28582"]
    body = ("idCuadro=CE36&sector=1&version=3&locale=es&anoInicial=2019&anoFinal=2026&tipoInformacion=4%2C1"
            "&formatoHorizontal=false&metadatosWeb=true&" + "&".join("series=" + s for s in series) +
            "&formatoCSV.x=5&formatoCSV.y=5")
    url = "https://www.banxico.org.mx/SieInternet/consultarDirectorioInternetAction.do?accion=consultarSeries"
    r = S.post(url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=120)
    r.raise_for_status()
    txt = r.content.decode("latin1")
    open(os.path.join(RAW, "banxico_CE36_SE28552_SE28570_SE28582.csv"), "w", encoding="utf-8").write(txt)
    rows = list(csv.reader(io.StringIO(txt)))
    hdr = next(r for r in rows if r and r[0] == "Fecha")
    idx = {s: hdr.index(s) for s in series}
    out = {s: {} for s in series}
    for r in rows:
        if r and re.match(r"\d{2}/\d{2}/\d{4}", r[0]):
            per = f"{r[0][6:10]}-{r[0][3:5]}"
            for s in series:
                v = r[idx[s]].strip()
                if v and v != "N/E":
                    out[s][per] = float(v)
    return out


# ---------------------------------------------------------------- 4. SITURQ (SEDETUR Q. Roo) derrama Cancún
def situr_cancun():
    page = "https://siturq.gob.mx/indicadores-turisticos"
    html = S.get(page, timeout=60).text
    token = re.search(r'token\s*=\s*"([^"]+)"', html).group(1)
    api = "https://returq.siturq.gob.mx/api/charts/getCharData"
    H = {"Authorization": "Bearer " + token, "Origin": "https://siturq.gob.mx", "Referer": "https://siturq.gob.mx/"}
    cun = "17766274-96f2-38c4-b164-ec2065452c31"
    # current indicator "Derrama Economica" (2023->) and discontinued "Derrama económica por Turistas" (2022-2024Q1)
    inds = [("5942ad3c-ad95-45e7-8f34-bb074510d7cd", "Derrama Subtotal"), ("a002e94b-1db2-4e37-b09a-0191018c2556", "Total")]
    out, raw = {}, {}
    for ind, fld in inds:  # later indicator overwrites (identical values where they overlap: verified for 2023)
        for y in range(2019, int(TODAY[:4]) + 1):
            cond = f'[[["month",">=",1],["year","=",{y}],["month","<=",12],["year","=",{y}]]]'
            r = S.post(api, headers=H, files={"indicators": (None, f'["{ind}"]'), "destinations": (None, f'["{cun}"]'),
                                              "conditions": (None, cond), "isOnline": (None, "false")}, timeout=60)
            r.raise_for_status()
            for x in r.json().get("data", []):
                if fld in x and x[fld] not in (None, ""):
                    per = f"{int(x['Año'])}-{int(x['Mes']):02d}"
                    v = float(x[fld])
                    raw.setdefault(ind, {})[per] = v
                    if per in out and abs(out[per] - v) > 0.011:
                        print(f"WARN situr overlap differs {per}: {out[per]} vs {v}", file=sys.stderr)
                    out[per] = v
    json.dump(raw, open(os.path.join(RAW, "siturq_derrama_cancun.json"), "w"), ensure_ascii=False, indent=1)
    return {k: v for k, v in sorted(out.items()) if v > 0}


# ---------------------------------------------------------------- 5. DataTur hotel (datos.gob.mx datastore)
def datatur_hotel():
    rid = "53fd6153-84c7-4485-ae32-62112251c1a4"
    B = "https://www.datos.gob.mx/api/3/action/datastore_search"
    out = {}
    for centro, key in (("Cancún", "CUN"), ("Los Cabos", "SJD")):
        recs, off = [], 0
        while True:
            d = S.get(B, params={"resource_id": rid, "filters": json.dumps({"centro": centro}), "limit": 1000, "offset": off}, headers={"User-Agent": "Mozilla/5.0"},
                      timeout=120).json()["result"]
            recs += d["records"]
            off += 1000
            if off >= d["total"]:
                break
        json.dump(recs, open(os.path.join(RAW, f"datatur_base70centros_{key}.json"), "w"), ensure_ascii=False)
        agg = {}
        for r in recs:
            if int(r["anio"]) < 2019:
                continue
            per = f"{r['anio']}-{int(r['mes']):02d}"
            a = agg.setdefault(per, [0, 0, 0, 0])
            a[0] += r["llegada_turistas_no_residentes"] or 0
            a[1] += r["llegada_turistas_residentes"] or 0
            a[2] += r["turistas_noche_no_residentes"] or 0
            a[3] += r["turistas_noche_residentes"] or 0
        out[key] = agg
    return out


# ---------------------------------------------------------------- 6. SE IED by state x activity
STATES = ["Aguascalientes", "Baja California", "Baja California Sur", "Campeche", "Chiapas", "Chihuahua",
          "Ciudad de México", "Coahuila de Zaragoza", "Colima", "Durango", "Guanajuato", "Guerrero", "Hidalgo",
          "Jalisco", "México", "Michoacán de Ocampo", "Morelos", "Nayarit", "Nuevo León", "Oaxaca", "Puebla",
          "Querétaro", "Quintana Roo", "San Luis Potosí", "Sinaloa", "Sonora", "Tabasco", "Tamaulipas", "Tlaxcala",
          "Veracruz de Ignacio de la Llave", "Yucatán", "Zacatecas", "No distribuido"]


def parse_ef(path):
    """YTD cumulative values {(state, 'total'|'721'): {'YYYY-n': value}} from an SE EF workbook."""
    wb = openpyxl.load_workbook(path, read_only=True)
    ws = wb["EF por Actividad Económica"]
    rows = ws.iter_rows(values_only=True)
    years = quarters = None
    cur_state = None
    ytd = {}
    for row in rows:
        row = list(row)
        first = row[0] if row else None
        if isinstance(first, str) and first.startswith("Entidad Federativa"):
            years = row
            quarters = list(next(rows))
            yr = None
            periods = []
            for i in range(len(years)):
                if isinstance(years[i], (int, float)):
                    yr = int(years[i])
                periods.append(f"{yr}-{int(quarters[i])}" if (i > 0 and yr and isinstance(quarters[i], (int, float))) else None)
            continue
        if years is None or not isinstance(first, str):
            continue
        name = first.strip()
        if name in STATES:
            cur_state = name
            target = "total"
        elif name.startswith("721 "):
            target = "721"
        else:
            continue
        if cur_state not in ("Quintana Roo", "Baja California Sur"):
            continue
        for i, p in enumerate(periods):
            if p:
                ytd.setdefault((cur_state, target), {})[p] = row[i]
    return ytd


def se_fdi():
    page = "https://www.gob.mx/se/acciones-y-programas/competitividad-y-normatividad-inversion-extranjera-directa"
    url = find_link(page, r"/cms/uploads/attachment/file/\d+/\d{4}_\dT_Flujos_EF_AC[^\"]*\.xlsx")
    ytd = parse_ef(dl(url, os.path.join(RAW, os.path.basename(url))))
    # 'cifras actualizadas' lag one quarter; the newest quarter only exists in 'originalmente publicadas' (OR)
    url_or = find_link(page, r"/cms/uploads/attachment/file/\d+/\d{4}_\dT_Flujos_EF_OR[^\"]*\.xlsx")
    ytd_or = parse_ef(dl(url_or, os.path.join(RAW, os.path.basename(url_or))))
    added = []
    for k, s in ytd_or.items():
        for p, v in s.items():
            if p not in ytd.get(k, {}):
                ytd.setdefault(k, {})[p] = v
                added.append(p)
    # de-cumulate YTD -> quarterly flows
    out = {}
    for k, s in ytd.items():
        q = {}
        for p, v in s.items():
            y, n = map(int, p.split("-"))
            if y < 2019 or not isinstance(v, (int, float)):
                continue
            if n == 1:
                q[f"{y}-Q1"] = round(v, 2)
            else:
                pv = s.get(f"{y}-{n - 1}")
                if isinstance(pv, (int, float)):
                    q[f"{y}-Q{n}"] = round(v - pv, 2)
        out[k] = dict(sorted(q.items()))
    return out, url, page, sorted(set(added)), url_or


# ---------------------------------------------------------------- build
def ser(d):
    return [[k, d[k]] for k in sorted(d)]


def main():
    afac_d, afac_url, afac_page = afac()
    upm_d, upm_files = upm_airport()
    bx = banxico()
    derr = situr_cancun()
    hot = datatur_hotel()
    fdi, fdi_url, fdi_page, fdi_added, fdi_url_or = se_fdi()

    last = lambda d: max(d) if d else None
    upm_last = max(f"{y}-{m:02d}" for y, m, _ in upm_files)

    def afac_series(apt, kind, label_apt):
        d = afac_d[(apt, kind)]
        k = "international" if kind == "intl" else "domestic"
        lab = "internacionales" if kind == "intl" else "nacionales"
        return {"key": f"passengers_air_{k}_total", "category": "arrivals",
                "label": f"Pasajeros aéreos {lab} (llegadas + salidas) – {label_apt}", "unit": "pasajeros",
                "frequency": "monthly", "data": ser(d),
                "source": {"org": "Agencia Federal de Aviación Civil (AFAC), SICT",
                           "title": "Estadística Operacional de Aeropuertos / Statistics by Airport 2006-2026 (pasajeros, servicio regular y fletamento)",
                           "page_url": afac_page, "file_url": afac_url, "format": "xlsx",
                           "update_frequency": "mensual", "release_lag": "~4 semanas (jul-2026 publicado 27-ago-2026)",
                           "last_period": last(d), "retrieved": TODAY,
                           "access_method": "Scrape gob.mx AFAC 'estadisticas-280404' page for link 'producto-aeropuerto-2006-*.xlsx'; download with UA 'curl/x' (browser UA gets Akamai JS challenge); parse xl/pivotCache/pivotCacheRecords1.xml (fields OPCIONES=PASAJEROS, TIPO, AÑO, GRUPO, AEROPUERTO, ENE..DIC)."}}

    def upm_series(key, label_apt):
        d = upm_d.get(key, {})
        return {"key": "arrivals_air_international", "category": "arrivals",
                "label": f"Llegadas aéreas de turistas extranjeros (solo entradas) – Aeropuerto de {label_apt}",
                "unit": "personas", "frequency": "monthly", "data": ser(d),
                "source": {"org": "SECTUR – DataTur, con datos de la Unidad de Política Migratoria, Registro e Identidad de Personas (SEGOB) / INM",
                           "title": "Entradas aéreas de turistas extranjeros por país de residencia y aeropuerto (reporte mensual RES_AAAA_MM)",
                           "page_url": "https://datatur.sectur.gob.mx/SitePages/upmresidencia.aspx",
                           "file_url": f"https://datatur.sectur.gob.mx/Documentoscompartidos/upm/RES_{upm_last.replace('-', '_')}.pdf",
                           "format": "pdf", "update_frequency": "mensual",
                           "release_lag": "~5-7 semanas", "last_period": last(d), "retrieved": TODAY,
                           "access_method": "Download https://datatur.sectur.gob.mx/Documentoscompartidos/upm/RES_YYYY_MM.pdf (UA curl) for each month since 2024-01; page 2 table 'por país de Residencia y Aeropuerto', third block (mes) gives prior-year and current month; prior-year (revised) value preferred over the preliminary one."}}

    bx_src = lambda title, sid, last_p: {
        "org": "Banco de México (SIE) con datos de INEGI – Encuesta de Viajeros Internacionales (EVI)",
        "title": title + f" (serie {sid}, cuadro CE36 'Cuenta de viajeros internacionales')",
        "page_url": "https://www.banxico.org.mx/SieInternet/consultarDirectorioInternetAction.do?accion=consultarCuadro&idCuadro=CE36&locale=es",
        "file_url": "https://www.banxico.org.mx/SieInternet/consultarDirectorioInternetAction.do?accion=consultarSeries (POST, formatoCSV)",
        "format": "csv", "update_frequency": "mensual",
        "release_lag": "~6 semanas (jul-2026 publicado 14-sep-2026 en boletín INEGI EVI)",
        "last_period": last_p, "retrieved": TODAY,
        "access_method": "POST form to consultarSeries with idCuadro=CE36, series=SE28552&series=SE28570&series=SE28582, anoInicial/anoFinal, tipoInformacion=4,1, formatoCSV.x=5&formatoCSV.y=5 (no token). Alternative: Banxico SIE REST API (requires free token) or INEGI BIE API (token)."}
    receipts = {k: round(v / 1000, 2) for k, v in bx["SE28552"].items()}
    avg = {k: round(v, 2) for k, v in bx["SE28582"].items()}
    ntour = {k: int(round(v * 1000)) for k, v in bx["SE28570"].items()}
    nat_series = [
        {"key": "spending_tourism_receipts_national", "category": "spending",
         "label": "NACIONAL (México) – Gasto total de turistas internacionales no fronterizos vía aérea (ingresos)",
         "unit": "US$ millones", "frequency": "monthly", "data": ser(receipts),
         "source": bx_src("Ingresos, Turistas no fronterizos (antes 'de internación'), Vía aérea", "SE28552", last(receipts))},
        {"key": "spending_avg_per_tourist_national", "category": "spending",
         "label": "NACIONAL (México) – Gasto medio por turista internacional no fronterizo vía aérea",
         "unit": "US$", "frequency": "monthly", "data": ser(avg),
         "source": bx_src("Gasto medio, Ingresos, Turistas no fronterizos, Vía aérea", "SE28582", last(avg))},
        {"key": "arrivals_tourists_air_national", "category": "arrivals",
         "label": "NACIONAL (México) – Turistas internacionales no fronterizos que ingresaron vía aérea",
         "unit": "personas", "frequency": "monthly", "data": ser(ntour),
         "source": bx_src("Número de viajeros, Ingresos, Turistas no fronterizos, Vía aérea (miles; convertido a personas x1000)", "SE28570", last(ntour))},
    ]

    def hotel_series(key, centro):
        agg = hot[key]
        stay = {p: round((a[2] + a[3]) / (a[0] + a[1]), 2) for p, a in agg.items() if (a[0] + a[1]) > 0}
        nores = {p: int(a[0]) for p, a in agg.items()}
        src = lambda t: {"org": "SECTUR – DataTur (Programa de Monitoreo de la Ocupación en Servicios Turísticos de Hospedaje)",
                         "title": t, "page_url": "https://www.datos.gob.mx/dataset/ocupacion_hotelera_70_destinos_principales_monitoreados_datatur",
                         "file_url": "https://www.datos.gob.mx/api/3/action/datastore_search?resource_id=53fd6153-84c7-4485-ae32-62112251c1a4",
                         "format": "api", "update_frequency": "mensual en DataTur; el conjunto en datos.gob.mx se actualiza de forma irregular (última: mar-2025)",
                         "release_lag": "datos.gob.mx: hasta dic-2024 (≈15+ meses de rezago); DataTur publica ocupación mensual con ~6 semanas pero sin turistas-noche",
                         "last_period": last(agg), "retrieved": TODAY,
                         "access_method": "CKAN datastore_search with filters {\"centro\": \"%s\"}, paginate limit=1000 (UA \"Mozilla/5.0\"; curl UA is blocked there); sum categories 1-5 estrellas by anio/mes (repodatos.atdt.gob.mx CSV returns 403)." % centro}
        return [
            {"key": "arrivals_hotel_tourists_nonresident", "category": "arrivals",
             "label": f"Llegadas de turistas no residentes a hoteles monitoreados – {centro}", "unit": "personas",
             "frequency": "monthly", "data": ser(nores),
             "source": src("Ocupación hotelera en los 70 destinos principales monitoreados en DataTur (Base70centros) – llegada_turistas_no_residentes")},
            {"key": "spending_avg_stay", "category": "spending",
             "label": f"Estadía promedio en hoteles monitoreados (residentes + no residentes) – {centro}", "unit": "noches",
             "frequency": "monthly", "data": ser(stay),
             "source": src("Base70centros – estadía = (turistas_noche_residentes + no_residentes) / (llegada_turistas_residentes + no_residentes)")},
        ]

    def fdi_series(state, short):
        base = lambda t: {"org": "Secretaría de Economía – Dirección General de Inversión Extranjera / CNIE",
                          "title": "Información estadística de flujos de IED hacia México por entidad federativa y actividad económica (cifras actualizadas, SCIAN 2023; último trimestre %s de cifras originalmente publicadas: %s)" % (",".join(fdi_added), fdi_url_or),
                          "page_url": fdi_page, "file_url": fdi_url, "format": "xlsx", "update_frequency": "trimestral",
                          "release_lag": "~7 semanas (2T-2026 publicado ago-2026)", "last_period": t, "retrieved": TODAY,
                          "access_method": "Scrape SE IED page for '*_Flujos_EF_AC*.xlsx' (UA curl); sheet 'EF por Actividad Económica'; state row = total, row '721 Servicios de alojamiento temporal' below it; values are YTD cumulative -> quarterly flow = YTD(Qn) - YTD(Qn-1). Also on datos.gob.mx (datastore resource ed8f888c-7d1a-4063-b18d-934bbe644025), which lags one quarter."}
        tot, lod = fdi[(state, "total")], fdi[(state, "721")]
        return [
            {"key": "investment_fdi_state", "category": "investment", "label": f"IED total – {short}", "unit": "US$ millones",
             "frequency": "quarterly", "data": ser(tot), "source": base(last(tot))},
            {"key": "investment_fdi_lodging", "category": "investment", "label": f"IED alojamiento temporal (SCIAN 721) – {short}",
             "unit": "US$ millones", "frequency": "quarterly", "data": ser(lod), "source": base(last(lod))},
        ]

    derr_series = {"key": "spending_tourism_receipts", "category": "spending",
                   "label": "Derrama económica de turistas – Cancún (estimación SEDETUR)", "unit": "US$ millones",
                   "frequency": "monthly", "data": ser(derr),
                   "source": {"org": "Secretaría de Turismo del Estado de Quintana Roo (SEDETUR) – SITURQ",
                              "title": "Indicadores Turísticos: 'Derrama Economica' (destino Cancún); 2022–2024-03 del indicador descontinuado 'Derrama económica por Turistas' (valores idénticos en el traslape)",
                              "page_url": "https://siturq.gob.mx/indicadores-turisticos",
                              "file_url": "https://returq.siturq.gob.mx/api/charts/getCharData", "format": "api",
                              "update_frequency": "mensual", "release_lag": "~8 semanas (jun-2026 actualizado 27-ago-2026)",
                              "last_period": last(derr), "retrieved": TODAY,
                              "access_method": "GET siturq.gob.mx/indicadores-turisticos, regex public bearer token; POST multipart to getCharData with indicators=[\"a002e94b-1db2-4e37-b09a-0191018c2556\"], destinations=[Cancún id 17766274-96f2-38c4-b164-ec2065452c31], conditions per year, isOnline=false; headers Authorization + Origin https://siturq.gob.mx. Page also offers 'Descargar Excel'."}}

    # sanity checks
    for key in ("CUN", "SJD"):
        yr = {}
        for p, v in upm_d.get(key, {}).items():
            yr.setdefault(p[:4], []).append(v)
        print(key, "UPM months per year", {y: len(v) for y, v in sorted(yr.items())},
              "sum", {y: sum(v) for y, v in sorted(yr.items()) if len(v) == 12})

    notes_common = [
        "passengers_air_*_total (AFAC): pasajeros de servicio regular + fletamento (charter) que llegan Y salen del aeropuerto (llegadas + salidas); 'internacionales' = vuelos internacionales, incluye mexicanos y extranjeros. No es comparable con llegadas solamente.",
        "arrivals_air_international (DataTur/UPM): SOLO entradas por vía aérea de turistas extranjeros (residentes en el extranjero, condición de estancia turista) registradas por migración en ese aeropuerto; excluye mexicanos residentes en el extranjero y no turistas. El mes más reciente es preliminar (p); se usa la cifra revisada del reporte del año siguiente cuando existe. Serie disponible en PDF desde 2024-01 (incluye comparativo 2023), por eso inicia 2023-01; 2019-2022 no se localizó en formato mensual por aeropuerto en DataTur (el Boletín Mensual de Estadísticas Migratorias de UPM podría cubrirlo – no extraído).",
        "Series *_national (Banxico/INEGI EVI): contexto NACIONAL de México, no específico del destino. 'Turistas no fronterizos' es la nueva nomenclatura INEGI 2026 de 'turistas de internación'. Cifras preliminares desde 2025.",
        "investment_fdi_* (SE): flujos trimestrales derivados restando acumulados anuales publicados (YTD Qn − YTD Qn−1); cifras actualizadas (se revisan cada trimestre, especialmente las recientes). El trimestre más reciente (no incluido aún en cifras actualizadas) se toma del archivo de cifras originalmente publicadas: flujo = YTD original del último trimestre − YTD actualizado del trimestre previo (ambos de la misma publicación). Trimestres con dato confidencial 'C' se omiten. IED por entidad se asigna al domicilio de la empresa receptora, no necesariamente a la ubicación del hotel. IED no es gasto turístico (categoría secundaria).",
        "spending_avg_stay: derivado (no publicado como tal en el conjunto abierto) = turistas-noche / llegadas de turistas en hoteles monitoreados por DataTur (1-5 estrellas), definición de estadía promedio de DataTur. El conjunto abierto termina en 2024-12; DataTur publica ocupación mensual 2025-2026 en xlsx (2026-MES_MM_Publico.zip) pero sin turistas-noche/llegadas.",
    ]

    cun = {"id": "MX-CUN", "name": "Cancún (Quintana Roo)", "type": "destination", "lat": 21.16, "lon": -86.85,
           "series": [upm_series("CUN", "Cancún"), afac_series("CANCUN", "intl", "Aeropuerto Internacional de Cancún"),
                      afac_series("CANCUN", "dom", "Aeropuerto Internacional de Cancún"), derr_series]
                     + hotel_series("CUN", "Cancún") + nat_series + fdi_series("Quintana Roo", "Quintana Roo"),
           "notes": notes_common + [
               "spending_tourism_receipts (Cancún): estimación de SEDETUR Quintana Roo ('Estimaciones de la Secretaría de Turismo del Estado con datos de diversas fuentes'), US$ millones, turistas hospedados en Cancún; no incluye cruceristas. No es una medición directa por encuesta. Sin datos 2019-2021 en SITURQ.",
               "IED Quintana Roo incluye toda la entidad (Riviera Maya, Tulum, etc.), no solo Cancún.",
           ]}
    sjd = {"id": "MX-SJD", "name": "Los Cabos (Baja California Sur)", "type": "destination", "lat": 22.89, "lon": -109.91,
           "series": [upm_series("SJD", "Los Cabos (San José del Cabo)"),
                      afac_series("SAN JOSE DEL CABO", "intl", "Aeropuerto Internacional de Los Cabos (SJD)"),
                      afac_series("SAN JOSE DEL CABO", "dom", "Aeropuerto Internacional de Los Cabos (SJD)")]
                     + hotel_series("SJD", "Los Cabos") + nat_series + fdi_series("Baja California Sur", "Baja California Sur"),
           "notes": notes_common + [
               "No se encontró serie oficial mensual de gasto/derrama turística a nivel destino para Los Cabos: SETUE BCS solo publica estimaciones puntuales por evento/temporada (p.ej. Semana Santa, Spring Break) y documentos anuales 'Información Estratégica'. INEGI EVI no publica gasto por aeropuerto de entrada. Se incluye solo contexto NACIONAL.",
               "AFAC 'SAN JOSE DEL CABO' = Aeropuerto Internacional de Los Cabos (GAP). No incluye el aeródromo de Cabo San Lucas.",
               "IED Baja California Sur incluye toda la entidad (La Paz, Loreto, etc.), no solo Los Cabos.",
           ]}
    for obj in (cun, sjd):
        path = os.path.join(BASE, obj["id"] + ".json")
        json.dump(obj, open(path, "w"), ensure_ascii=False, indent=1)
        print("wrote", path, [(s["key"], s["data"][0][0] if s["data"] else None, s["data"][-1][0] if s["data"] else None, len(s["data"])) for s in obj["series"]])


if __name__ == "__main__":
    main()
