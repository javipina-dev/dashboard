"""Cartagena de Indias (Colombia) – CO-CTG extraction.

Official sources only. Every run re-downloads from the publisher.

  1. Llegadas de extranjeros no residentes por ciudad de destino (Cartagena)
     MinCIT – Oficina de Estudios Economicos (OEE) with Migracion Colombia records,
     published as open data on datos.gov.co (Socrata dataset 7wm8-w5ad).
  2. Entradas de extranjeros por puesto de control migratorio (PCM Aeropuerto
     Rafael Nunez, Cartagena) – Migracion Colombia, Socrata dataset 96sh-4v8d.
  3. Pasajeros aereos por aeropuerto origen/destino – Aerocivil open data
     (Socrata dataset gb6w-ynu4). Only 2020+ is published there; www.aerocivil.gov.co
     itself returns HTTP 403 to every client we can use, so 2019 is not obtainable.
  4. Porcentaje de ocupacion / tarifas / huespedes no residentes – DANE,
     Encuesta Mensual de Alojamiento (EMA), anexos xlsx, dominio geografico "Cartagena".
  5. Ingresos de divisas por turismo (NACIONAL) – Banco de la Republica, balanza de
     pagos, "Exportaciones en la cuenta de viajes y transporte", as published by the
     MinCIT OEE monthly tourism report (Banrep's own statistics portal does NOT expose
     the "viajes" sub-account as a downloadable series; only "servicios" net).

Validation checks are printed at the end of every run (see main()).
"""
import subprocess
import json
import re
import sys
import urllib.parse
from pathlib import Path

import openpyxl
import pdfplumber
import requests

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / 'raw' / 'CO'
RETRIEVED = '2026-09-17'

# datos.gov.co blocks the plain curl UA on some paths; a browser-like UA works everywhere.
UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')
SOCRATA = 'https://www.datos.gov.co/resource/%s.json'
VIEWS = 'https://www.datos.gov.co/api/views/%s.json'

MES_ABR = {'Ene': 1, 'Feb': 2, 'Mar': 3, 'Abr': 4, 'May': 5, 'Jun': 6,
           'Jul': 7, 'Ago': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dic': 12}
MES_FULL = {'Enero': 1, 'Febrero': 2, 'Marzo': 3, 'Abril': 4, 'Mayo': 5, 'Junio': 6,
            'Julio': 7, 'Agosto': 8, 'Septiembre': 9, 'Octubre': 10,
            'Noviembre': 11, 'Diciembre': 12}

# MinCIT monthly tourism report (OEE). Latest issue at time of writing: junio 2026.
MINCIT_PAGE = ('https://www.mincit.gov.co/estudios-economicos/estadisticas-e-informes/'
               'informes-de-turismo')
MINCIT_PDF = ('https://www.mincit.gov.co/getattachment/estudios-economicos/'
              'estadisticas-e-informes/informes-de-turismo/2026/junio/'
              'oee-ec-turismo-junio-2026.pdf.aspx')
# DANE EMA (anexos). Latest issue at time of writing: julio 2026.
EMA_PAGE = ('https://www.dane.gov.co/index.php/estadisticas-por-tema/servicios/'
            'encuesta-mensual-de-alojamiento-ema')
EMA_XLSX = 'https://www.dane.gov.co/files/operaciones/EMA/anex-EMA-jul2026.xlsx'
EMA_BOL = 'https://www.dane.gov.co/files/operaciones/EMA/bol-EMA-jul2026.pdf'

# Puesto de control migratorio of the Cartagena airport, as it appears in the
# "Ubicacion PCM" column (lat,lon of the post). Matched by bounding box so that a
# small coordinate revision by the publisher does not silently drop the series.
CTG_AIRPORT_BOX = (10.42, 10.47, -75.53, -75.50)   # lat_min, lat_max, lon_min, lon_max

_session = requests.Session()
_session.headers['User-Agent'] = UA


def download(url, name):
    """Re-download url into RAW/name on every run and return the local path.

    Uses curl rather than requests: www.dane.gov.co negotiates a TLS version that
    the bundled Python/OpenSSL rejects ("tlsv1 alert protocol version").
    """
    RAW.mkdir(parents=True, exist_ok=True)
    dest = RAW / name
    subprocess.run(['curl', '-sSL', '-m', '300', '-A', UA,
                    '-H', 'Accept-Language: es-CO,es;q=0.9', '-o', str(dest), url],
                   check=True)
    assert dest.stat().st_size > 10000, 'suspiciously small download: %s' % name
    print('  downloaded %-28s %8.1f kB' % (name, dest.stat().st_size / 1024))
    return dest


def soql(dataset, **params):
    """Run a SoQL query against a datos.gov.co (Socrata) dataset."""
    q = {('$' + k if not k.startswith('$') else k): v for k, v in params.items()}
    url = SOCRATA % dataset + '?' + urllib.parse.urlencode(q)
    r = _session.get(url, timeout=180)
    r.raise_for_status()
    return r.json()


def dataset_updated(dataset):
    r = _session.get(VIEWS % dataset, timeout=120)
    r.raise_for_status()
    import datetime
    ts = r.json().get('rowsUpdatedAt')
    return datetime.datetime.utcfromtimestamp(ts).strftime('%Y-%m-%d') if ts else '?'


def to_series(pairs, start='2019-01'):
    """{(year, month): value} -> [["YYYY-MM", value], ...] sorted, from `start`."""
    out = []
    for (y, m), v in sorted(pairs.items()):
        p = '%04d-%02d' % (y, m)
        if p >= start:
            out.append([p, v])
    return out


# --------------------------------------------------------------------------- #
# 1. MinCIT / Migracion Colombia – extranjeros no residentes por ciudad destino
# --------------------------------------------------------------------------- #
DS_NORES = '7wm8-w5ad'


def nonresident_by_city(city='Cartagena', dept='Bolívar'):
    rows = soql(DS_NORES,
                select='a_o,mes,sum(cant_extranjeros_no_residentes) as t',
                group='a_o,mes',
                where="ciudad='%s' AND departamento='%s'" % (city, dept),
                limit=2000)
    assert rows, 'no rows for %s/%s in %s' % (city, dept, DS_NORES)
    return {(int(r['a_o']), MES_ABR[r['mes']]): int(r['t']) for r in rows}


def nonresident_national_annual():
    rows = soql(DS_NORES, select='a_o,sum(cant_extranjeros_no_residentes) as t',
                group='a_o', limit=100)
    return {int(r['a_o']): int(r['t']) for r in rows}


# --------------------------------------------------------------------------- #
# 2. Migracion Colombia – entradas de extranjeros por PCM
# --------------------------------------------------------------------------- #
DS_PCM = '96sh-4v8d'


def _resolve_pcm(box):
    """Find the ubicacion_pcm string(s) inside a lat/lon bounding box."""
    rows = soql(DS_PCM, select='ubicacion_pcm', group='ubicacion_pcm', limit=1000)
    lat0, lat1, lon0, lon1 = box
    hit = []
    for r in rows:
        m = re.fullmatch(r'\(\s*(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)\s*\)',
                         r['ubicacion_pcm'] or '')
        if not m:
            continue
        lat, lon = float(m.group(1)), float(m.group(2))
        if lat0 <= lat <= lat1 and lon0 <= lon <= lon1:
            hit.append(r['ubicacion_pcm'])
    assert len(hit) == 1, 'expected exactly 1 PCM in %s, got %r' % (box, hit)
    return hit[0]


def foreign_entries_pcm(box):
    pcm = _resolve_pcm(box)
    rows = soql(DS_PCM, select='a_o,mes,sum(total) as t', group='a_o,mes',
                where="ubicacion_pcm='%s'" % pcm, limit=2000)
    assert rows, 'no rows for PCM %s' % pcm
    return pcm, {(int(r['a_o']), MES_FULL[r['mes']]): int(r['t']) for r in rows}


# --------------------------------------------------------------------------- #
# 3. Aerocivil – pasajeros origen/destino
# --------------------------------------------------------------------------- #
DS_AERO = 'gb6w-ynu4'
# tipo_vuelo R = servicio regular (what MinCIT/Aerocivil headline as "vuelos regulares");
# C charter, A adicional, T taxi aereo. trafico N = nacional, I = internacional.


def aero_passengers(iata='CTG', direction='destino', trafico='I', tipo_vuelo='R'):
    rows = soql(DS_AERO, select='a_o,n_mero_de_mes,sum(pasajeros) as p',
                group='a_o,n_mero_de_mes',
                where="%s='%s' AND tipo_vuelo='%s' AND tr_fico_n_i='%s'"
                      % (direction, iata, tipo_vuelo, trafico),
                limit=2000)
    assert rows, 'no Aerocivil rows for %s %s %s' % (iata, direction, trafico)
    return {(int(r['a_o']), int(r['n_mero_de_mes'])): int(r['p']) for r in rows}


def _add(a, b):
    out = dict(a)
    for k, v in b.items():
        out[k] = out.get(k, 0) + v
    # only keep periods present in both directions, otherwise the "total" is lopsided
    return {k: v for k, v in out.items() if k in a and k in b}


# --------------------------------------------------------------------------- #
# 4. DANE EMA
# --------------------------------------------------------------------------- #
def _ema_rows(path, sheet):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[sheet]
    return [list(r) for r in ws.iter_rows(values_only=True)]


def _ema_header_row(rows, label, max_scan=20):
    for i, r in enumerate(rows[:max_scan]):
        if r and r[0] == label:
            return i
    raise AssertionError('EMA header row %r not found' % label)


def ema_monthly(path, sheet, col_label, sub_offset=0):
    """Read a 'Ano | Meses | <domains...>' EMA sheet for one geographic domain.

    sub_offset shifts right from the domain's first column (sheets 5.5 and 6.1 have
    two/six sub-columns per domain).
    """
    rows = _ema_rows(path, sheet)
    hi = _ema_header_row(rows, 'Año')
    hdr = rows[hi]
    assert col_label in hdr, '%s not in %s header' % (col_label, sheet)
    ci = hdr.index(col_label) + sub_offset
    year = None
    out = {}
    for r in rows[hi + 1:]:
        if r[0] not in (None, ''):
            m = re.match(r'(\d{4})', str(r[0]))
            if m:
                year = int(m.group(1))
        mes = str(r[1]).strip() if r[1] else ''
        if year is None or mes not in MES_FULL:
            continue
        v = r[ci]
        if v is None or str(v).strip() in ('', '-', 'nd'):
            continue          # month not published (e.g. suppressed sample)
        out[(year, MES_FULL[mes])] = round(float(v), 2)
    assert out, 'no EMA values read from %s / %s' % (sheet, col_label)
    return out


def ema_last_period_from_bulletin(path):
    """Read the reference month printed on the EMA bulletin cover, e.g. 'Julio de 2026'."""
    with pdfplumber.open(path) as pdf:
        t = pdf.pages[0].extract_text() or ''
    m = re.search(r'Encuesta Mensual de Alojamiento \(EMA\)\s*\n\s*(\w+) de (\d{4})', t)
    assert m, 'EMA reference month not found on bulletin cover'
    return '%04d-%02d' % (int(m.group(2)), MES_FULL[m.group(1).capitalize()])


# --------------------------------------------------------------------------- #
# 5. Banrep travel+transport credits, via the MinCIT OEE report chart
# --------------------------------------------------------------------------- #
def mincit_travel_exports(path):
    """Parse the 'Exportaciones en la cuenta de viajes y transporte' chart.

    The bar data labels are rotated: each label is a column of single characters at a
    fixed x, reading bottom-to-top. Group by x, sort by y descending, join.
    Returns ({year: usd_millions}, {'2026-I': usd_millions, ...}).
    """
    with pdfplumber.open(path) as pdf:
        page = None
        for pg in pdf.pages:
            t = pg.extract_text() or ''
            # the summary page repeats the title and the headline sentence, so also
            # require the chart's own axis: many plain years plus a quarter label.
            if ('cuenta de viajes y transporte' in t
                    and len(re.findall(r'\b20\d\d\b', t)) >= 9
                    and re.search(r'\b20\d\d-I\b', t)):
                page = pg
                break
        assert page is not None, 'divisas/viajes chart page not found'
        words = page.extract_words(x_tolerance=1.2, y_tolerance=1.2)
        text = page.extract_text() or ''

    # x position of each category label on the axis -> the bar it belongs to.
    # Year-like words also occur in the paragraph under the chart, so keep only the
    # single horizontal row that holds the most of them: that row is the axis.
    by_row = {}
    for w in words:
        if re.fullmatch(r'20\d\d(-I{1,3}V?)?', w['text']):
            by_row.setdefault(round(w['top']), []).append(w)
    assert by_row, 'no year/quarter axis labels found'
    axis_row = max(by_row.values(), key=len)
    assert len(axis_row) >= 9, 'axis row has only %d labels' % len(axis_row)
    cats = sorted((round(w['x0'], 1), w['text']) for w in axis_row)
    axis_top = min(w['top'] for w in axis_row)

    cols = {}
    for w in words:
        if w['top'] >= axis_top - 5:
            continue
        if not re.fullmatch(r'[\d.,]', w['text']):
            continue
        cols.setdefault(round(w['x0'], 1), []).append((w['top'], w['text']))

    out = {}
    for x, label in cats:
        best = min(cols, key=lambda c: abs(c - x)) if cols else None
        assert best is not None and abs(best - x) < 12, 'no label column near %s' % label
        chars = [c for _, c in sorted(cols[best], reverse=True)]     # bottom -> top
        s = ''.join(chars)
        assert re.fullmatch(r'\d{1,2}\.\d{3}', s), 'unparsable label %r for %s' % (s, label)
        out[label] = float(s.replace('.', ''))

    annual = {int(k): v for k, v in out.items() if re.fullmatch(r'20\d\d', k)}
    quarters = {k: v for k, v in out.items() if not re.fullmatch(r'20\d\d', k)}

    # cross-check the headline sentence, e.g. "En 2025 ingresaron USD $11,418 millones"
    m = re.search(r'En (\d{4}) ingresaron USD \$([\d.,]+) millones', text)
    assert m, 'headline divisas sentence not found'
    y, v = int(m.group(1)), float(m.group(2).replace('.', '').replace(',', ''))
    assert annual.get(y) == v, 'chart %s=%s != text %s' % (y, annual.get(y), v)
    return annual, quarters, y, v


# --------------------------------------------------------------------------- #
def main():
    print('== downloading sources ==')
    pdf_mincit = download(MINCIT_PDF, 'mincit_turismo_jun2026.pdf')
    xlsx_ema = download(EMA_XLSX, 'anex-EMA-jul2026.xlsx')
    pdf_ema = download(EMA_BOL, 'bol-EMA-jul2026.pdf')

    checks = []
    series = []

    # ---------------- arrivals: extranjeros no residentes, destino Cartagena -------
    print('== Socrata: extranjeros no residentes por ciudad de destino ==')
    ctg = nonresident_by_city()
    nores_upd = dataset_updated(DS_NORES)
    data = to_series(ctg)
    nores_last = data[-1][0]
    series.append({
        'key': 'arrivals_stopover', 'category': 'arrivals',
        'label': 'Llegadas de extranjeros no residentes con ciudad de destino Cartagena '
                 '(todas las vías de ingreso al país)',
        'unit': 'personas', 'frequency': 'monthly', 'data': data,
        'source': {
            'org': 'Ministerio de Comercio, Industria y Turismo (MinCIT) – Oficina de '
                   'Estudios Económicos (OEE), con registros de Migración Colombia',
            'title': 'Extranjeros No Residentes (llegada de extranjeros no residentes por '
                     'departamento y ciudad de destino y país de residencia)',
            'page_url': 'https://www.datos.gov.co/Comercio-Industria-y-Turismo/'
                        'Extranjeros-No-Residentes/7wm8-w5ad/about_data',
            'file_url': 'https://www.datos.gov.co/resource/7wm8-w5ad.json',
            'format': 'api', 'update_frequency': 'mensual',
            'release_lag': '~7-8 semanas (jun-2026 disponible el %s)' % nores_upd,
            'last_period': nores_last, 'retrieved': RETRIEVED,
            'access_method': "SoQL sobre datos.gov.co (Socrata) 7wm8-w5ad: "
                             "$select=a_o,mes,sum(cant_extranjeros_no_residentes) "
                             "$group=a_o,mes $where=ciudad='Cartagena' AND "
                             "departamento='Bolívar'. Sin token; UA de navegador. "
                             "La misma cifra agregada se publica en el informe mensual "
                             "de turismo de la OEE (PDF).",
        }})

    # ---------------- arrivals: entradas de extranjeros, PCM aeropuerto CTG --------
    print('== Socrata: entradas de extranjeros por PCM (aeropuerto Rafael Núñez) ==')
    pcm, ent = foreign_entries_pcm(CTG_AIRPORT_BOX)
    pcm_upd = dataset_updated(DS_PCM)
    data = to_series(ent)
    series.append({
        'key': 'arrivals_air_international', 'category': 'arrivals',
        'label': 'Entradas de extranjeros registradas por Migración Colombia en el puesto '
                 'de control migratorio del Aeropuerto Internacional Rafael Núñez '
                 '(Cartagena) – solo entradas, todas las condiciones migratorias',
        'unit': 'personas', 'frequency': 'monthly', 'data': data,
        'source': {
            'org': 'Unidad Administrativa Especial Migración Colombia',
            'title': 'Entradas de extranjeros a Colombia (por nacionalidad, género y '
                     'ubicación del puesto de control migratorio – PCM)',
            'page_url': 'https://www.datos.gov.co/Estad-sticas-Nacionales/'
                        'Entradas-de-extranjeros-a-Colombia/96sh-4v8d',
            'file_url': 'https://www.datos.gov.co/resource/96sh-4v8d.json',
            'format': 'api', 'update_frequency': 'mensual',
            'release_lag': '~7-8 semanas (jun-2026 disponible el %s)' % pcm_upd,
            'last_period': data[-1][0], 'retrieved': RETRIEVED,
            'access_method': "SoQL sobre datos.gov.co (Socrata) 96sh-4v8d. La columna "
                             "ubicacion_pcm trae las coordenadas del puesto, no su nombre: "
                             "el script resuelve el PCM por caja geográfica "
                             "lat 10.42-10.47 / lon -75.53..-75.50 y exige una única "
                             "coincidencia (actualmente '%s'; el PCM marítimo de Cartagena "
                             "está en (10.408582,-75.538003))." % pcm,
        }})

    # ---------------- air: Aerocivil ----------------------------------------------
    print('== Socrata: Aerocivil origen-destino (CTG) ==')
    aero_upd = dataset_updated(DS_AERO)
    arr_i = aero_passengers(direction='destino', trafico='I')
    arr_n = aero_passengers(direction='destino', trafico='N')
    dep_i = aero_passengers(direction='origen', trafico='I')
    dep_n = aero_passengers(direction='origen', trafico='N')
    aero_src = {
        'org': 'Unidad Administrativa Especial de Aeronáutica Civil (Aerocivil)',
        'title': 'Transporte Aéreo Comercial – Tráfico Origen-Destino (Colombia), '
                 'servicio regular, aeropuerto CTG – Rafael Núñez',
        'page_url': 'https://www.datos.gov.co/Transporte/'
                    'Transporte-A-reo-Comercial-Tr-fico-Origen-Destino-C/gb6w-ynu4',
        'file_url': 'https://www.datos.gov.co/resource/gb6w-ynu4.json',
        'format': 'api', 'update_frequency': 'mensual',
        'release_lag': '~5-6 semanas (jun-2026 disponible el %s)' % aero_upd,
        'last_period': None, 'retrieved': RETRIEVED,
        'access_method': "SoQL sobre datos.gov.co (Socrata) gb6w-ynu4 filtrando "
                         "destino='CTG' (llegadas) u origen='CTG' (salidas), "
                         "tipo_vuelo='R' (servicio regular) y tr_fico_n_i='I'/'N'. "
                         "El sitio www.aerocivil.gov.co responde 403 a todo cliente "
                         "(incluido navegador), por eso se usa el portal de datos "
                         "abiertos, que sólo publica desde 2020.",
    }
    for key, label, pairs in [
        ('passengers_air_international_arrivals',
         'Pasajeros aéreos internacionales LLEGADOS (solo llegadas, vuelos regulares) – '
         'Aeropuerto Internacional Rafael Núñez, Cartagena', arr_i),
        ('passengers_air_domestic_arrivals',
         'Pasajeros aéreos nacionales LLEGADOS (solo llegadas, vuelos regulares) – '
         'Aeropuerto Internacional Rafael Núñez, Cartagena', arr_n),
        ('passengers_air_international_total',
         'Pasajeros aéreos internacionales (llegadas + salidas, vuelos regulares) – '
         'Aeropuerto Internacional Rafael Núñez, Cartagena', _add(arr_i, dep_i)),
        ('passengers_air_domestic_total',
         'Pasajeros aéreos nacionales (llegadas + salidas, vuelos regulares) – '
         'Aeropuerto Internacional Rafael Núñez, Cartagena', _add(arr_n, dep_n)),
    ]:
        data = to_series(pairs)
        src = dict(aero_src, last_period=data[-1][0])
        series.append({'key': key, 'category': 'arrivals', 'label': label,
                       'unit': 'pasajeros', 'frequency': 'monthly', 'data': data,
                       'source': src})

    # ---------------- hotels: DANE EMA --------------------------------------------
    print('== DANE EMA (dominio Cartagena) ==')
    ema_last = ema_last_period_from_bulletin(pdf_ema)
    occ = ema_monthly(xlsx_ema, '4.2 Porc Mens Ocupación.reg', 'Cartagena')
    nores_share = ema_monthly(xlsx_ema, '5.5 Porc Huéspedes.EMA', 'Cartagena',
                              sub_offset=1)
    tarifa_s = ema_monthly(xlsx_ema, '6.1 Ind.Var Tarifas.acomoda', 'Cartagena')
    ema_src = {
        'org': 'Departamento Administrativo Nacional de Estadística (DANE)',
        'title': 'Encuesta Mensual de Alojamiento (EMA) – anexos, dominio geográfico '
                 '"Cartagena"',
        'page_url': EMA_PAGE, 'file_url': EMA_XLSX, 'format': 'xlsx',
        'update_frequency': 'mensual',
        'release_lag': '~6-7 semanas (jul-2026 publicado el 15-sep-2026)',
        'last_period': None, 'retrieved': RETRIEVED,
        'access_method': "Descargar anex-EMA-<mes><aaaa>.xlsx de la página de la EMA "
                         "(UA de navegador); hoja '4.2 Porc Mens Ocupación.reg' "
                         "(ocupación), '5.5 Porc Huéspedes.EMA' (residentes / no "
                         "residentes, dos subcolumnas por dominio) y "
                         "'6.1 Ind.Var Tarifas.acomoda' (índice de tarifa, seis "
                         "subcolumnas por dominio: Sencilla/Doble x índice, var. "
                         "mensual, var. anual). Columna 'Cartagena'.",
    }
    for key, label, unit, pairs in [
        ('hotel_occupancy_rate',
         'Porcentaje de ocupación de los alojamientos (habitaciones ocupadas / '
         'habitaciones disponibles) – Cartagena', '%', occ),
        ('hotel_guests_nonresident_share',
         'Participación de huéspedes NO residentes en el total de huéspedes – Cartagena',
         '%', nores_share),
        ('hotel_rate_index_single',
         'Índice de tarifa de acomodación sencilla (no se publica la tarifa en pesos) – '
         'Cartagena', 'índice (base 2019=100)', tarifa_s),
    ]:
        data = to_series(pairs)
        assert data[-1][0] == ema_last, 'EMA %s ends %s, bulletin says %s' % (
            key, data[-1][0], ema_last)
        series.append({'key': key, 'category': 'hotels', 'label': label, 'unit': unit,
                       'frequency': 'monthly', 'data': data,
                       'source': dict(ema_src, last_period=data[-1][0])})

    # ---------------- spending: national travel+transport credits -----------------
    print('== MinCIT/Banrep: exportaciones de viajes y transporte (NACIONAL) ==')
    annual, quarters, hl_year, hl_val = mincit_travel_exports(pdf_mincit)
    data = [[str(y), v] for y, v in sorted(annual.items()) if y >= 2019]
    series.append({
        'key': 'spending_tourism_receipts_national', 'category': 'spending',
        'label': 'NACIONAL (Colombia) – Ingresos de divisas por turismo: exportaciones '
                 'en la cuenta de viajes y transporte de pasajeros (balanza de pagos)',
        'unit': 'US$ millones', 'frequency': 'annual', 'data': data,
        'source': {
            'org': 'Banco de la República – Balanza de Pagos; publicado por MinCIT – '
                   'Oficina de Estudios Económicos (OEE)',
            'title': 'Informe mensual de turismo, "Exportaciones en la cuenta de viajes y '
                     'transporte" (cifras macroeconómicas del sector turismo)',
            'page_url': MINCIT_PAGE, 'file_url': MINCIT_PDF, 'format': 'pdf',
            'update_frequency': 'trimestral (el informe mensual de la OEE lo reproduce '
                                'cada mes)',
            'release_lag': '~9 semanas tras el cierre del trimestre (el informe de '
                           'jun-2026 cita la publicación Banrep del 02-jun-2026)',
            'last_period': data[-1][0], 'retrieved': RETRIEVED,
            'access_method': "PDF del informe mensual de turismo de la OEE; la página "
                             "'Exportaciones en la cuenta de viajes y transporte' trae "
                             "las etiquetas de barra rotadas (un carácter por fila, se "
                             "leen de abajo hacia arriba): se agrupan las palabras por "
                             "x y se ordenan por y descendente. El valor del último año "
                             "se contrasta con la frase 'En AAAA ingresaron USD $X "
                             "millones'. El portal de estadísticas del Banco de la "
                             "República (suameca.banrep.gov.co) NO expone la subcuenta "
                             "'viajes' como serie descargable, sólo 'servicios' neto, y "
                             "www.banrep.gov.co está detrás de Radware Bot Manager.",
        }})

    # ------------------------------ validations -----------------------------------
    print('\n== VALIDATION ==')

    # (a) national annual totals of the non-resident dataset vs MinCIT report Tabla 1
    with pdfplumber.open(pdf_mincit) as pdf:
        txt = '\n'.join((p.extract_text() or '') for p in pdf.pages)
    m = re.search(r'Extranjeros no residentes\*?\s+((?:[\d.]+\s+){3}[\d.]+)', txt)
    assert m, 'Tabla 1 (extranjeros no residentes por año) not found in MinCIT PDF'
    pub = [int(x.replace('.', '')) for x in m.group(1).split()]
    yrs = sorted(re.search(r'Tabla 1\. Visitantes no residentes\s*\n\s*((?:20\d\d\s+){3}20\d\d)',
                           txt).group(1).split())
    api = nonresident_national_annual()
    for y, v in zip(yrs, pub):
        got = api.get(int(y))
        ok = got == v
        checks.append(ok)
        print('  [%s] extranjeros no residentes NACIONAL %s: API %s vs informe MinCIT %s'
              % ('OK' if ok else 'FAIL', y, got, v))

    # (b) Cartagena monthly sums add up to the annual aggregate queried separately
    ann = soql(DS_NORES, select='a_o,sum(cant_extranjeros_no_residentes) as t',
               group='a_o', where="ciudad='Cartagena' AND departamento='Bolívar'",
               limit=100)
    ann = {int(r['a_o']): int(r['t']) for r in ann}
    for y in sorted(ann):
        if y < 2019:
            continue
        s = sum(v for (yy, _), v in ctg.items() if yy == y)
        ok = s == ann[y]
        checks.append(ok)
        print('  [%s] Cartagena %s: suma mensual %s vs total anual publicado %s'
              % ('OK' if ok else 'FAIL', y, s, ann[y]))

    # (c) Aerocivil arrivals vs the airport table printed in the MinCIT report
    m = re.search(r'Cartagena - Rafael Núñez\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)', txt)
    mi = re.search(r'julio (\d{4}) julio', txt)  # month of the "mensual" section
    rep_month = 6  # the junio-2026 report's monthly airport tables refer to June
    for label, pat, pairs in [
        ('internacional', r'Llegadas mensuales de pasajeros en vuelos\s*\n?internacionales '
                          r'regulares[\s\S]{0,400}?Cartagena - Rafael Núñez\s+([\d.]+)\s+'
                          r'([\d.]+)\s+([\d.]+)', arr_i),
        ('nacional', r'Llegadas de pasajeros en vuelos nacionales\s*\n?regulares por '
                     r'principales aeropuertos[\s\S]{0,400}?Cartagena - Rafael Núñez\s+'
                     r'([\d.]+)\s+([\d.]+)\s+([\d.]+)', arr_n),
    ]:
        mm = re.search(pat, txt)
        assert mm, 'airport table (%s) not found in MinCIT PDF' % label
        pubv = [int(x.replace('.', '')) for x in mm.groups()]
        for off, v in zip((2024, 2025, 2026), pubv):
            got = pairs.get((off, rep_month))
            ok = got == v
            checks.append(ok)
            print('  [%s] Aerocivil llegadas %s CTG %04d-%02d: API %s vs informe MinCIT %s'
                  % ('OK' if ok else 'FAIL', label, off, rep_month, got, v))

    # (d) DANE EMA: last month + year-ago month vs the EMA bulletin text
    with pdfplumber.open(pdf_ema) as pdf:
        bol = '\n'.join((p.extract_text() or '') for p in pdf.pages)
    mb = re.search(r'En Cartagena,\s+para\s+\w+\s+de\s+\d{4}\s+el porcentaje de ocupación'
                   r'\s+fue de\s+([\d,]+)%,\s+en\s+\w+\s+de\s+\d{4}\s+este\s+porcentaje'
                   r'\s+fue de\s+([\d,]+)%', bol, flags=re.S)
    assert mb, 'Cartagena occupancy sentence not found in EMA bulletin'
    y, mo = int(ema_last[:4]), int(ema_last[5:])
    for (yy, mm_), want in [((y, mo), mb.group(1)), ((y - 1, mo), mb.group(2))]:
        got = occ[(yy, mm_)]
        ok = abs(got - float(want.replace(',', '.'))) < 0.05
        checks.append(ok)
        print('  [%s] EMA ocupación Cartagena %04d-%02d: anexo %.2f%% vs boletín %s%%'
              % ('OK' if ok else 'FAIL', yy, mm_, got, want))

    # (e) the MinCIT report prints Cartagena's occupancy twice: the reference month and
    #     the year-to-date average. Both must be reproducible from the EMA anexo.
    mr = re.search(r'Ocupación alojamiento a (\w+) (\d{4})', txt)
    assert mr, 'MinCIT occupancy reference month not found'
    ry, rm = int(mr.group(2)), MES_FULL[mr.group(1).capitalize()]
    pubs = [float(v.replace(',', '.'))
            for v in re.findall(r'Cartagena\s+(\d{2},\d)%', txt)]
    assert len(pubs) >= 2, 'expected 2 Cartagena occupancy figures, got %r' % pubs
    ytd = [occ[(ry, k)] for k in range(1, rm + 1) if (ry, k) in occ]
    for name, got in [('mes %04d-%02d' % (ry, rm), occ[(ry, rm)]),
                      ('promedio ene-%02d %d' % (rm, ry), sum(ytd) / len(ytd))]:
        ok = any(abs(got - p) < 0.06 for p in pubs)
        checks.append(ok)
        print('  [%s] ocupación Cartagena %s: EMA %.2f%% vs informe MinCIT %s'
              % ('OK' if ok else 'FAIL', name, got, pubs))

    # (f) divisas: chart label vs headline sentence (already asserted) + YoY
    print('  [OK] divisas viajes+transporte %d: gráfico %.0f == texto %.0f (USD millones)'
          % (hl_year, annual[hl_year], hl_val))
    checks.append(True)
    prev = annual.get(hl_year - 1)
    mg = re.search(r'aumento de ([\d,]+)' + re.escape('%') + r' respecto al año '
                   + str(hl_year - 1), txt)
    if prev and mg:
        yoy = (hl_val / prev - 1) * 100
        want = float(mg.group(1).replace(',', '.'))
        ok = abs(yoy - want) < 0.15
        checks.append(ok)
        print('  [%s] divisas %d/%d: variación calculada %.1f%% vs texto %s%%'
              % ('OK' if ok else 'FAIL', hl_year, hl_year - 1, yoy, want))
    print('  Q1 sueltos publicados en el mismo gráfico (no se cargan, serie anual): %s'
          % quarters)

    out = {
        'id': 'CO-CTG', 'name': 'Cartagena (Colombia)', 'type': 'destination',
        'lat': 10.42, 'lon': -75.55, 'series': series,
        'notes': [
            "arrivals_stopover (MinCIT OEE / Migración Colombia): extranjeros NO "
            "residentes clasificados por la CIUDAD DE DESTINO declarada, no por el puesto "
            "de entrada; incluye a quienes entran al país por Bogotá u otro puesto y "
            "declaran Cartagena como destino, y por tanto NO es una serie aérea. Excluye "
            "colombianos residentes en el exterior y excluye pasajeros de cruceros (el "
            "MinCIT los cuenta aparte). Desde ene-2020 incluye residentes en Venezuela "
            "que declararon motivo 'descanso y esparcimiento'. Cifras preliminares para "
            "el año en curso.",
            "NO es comparable con el arrivals_air_international de Cancún (que es "
            "'entradas aéreas de turistas extranjeros' en un aeropuerto). El equivalente "
            "más cercano para Cartagena es arrivals_air_international de este archivo "
            "(PCM del aeropuerto Rafael Núñez), pero ése cuenta TODAS las entradas de "
            "extranjeros registradas en ese puesto, sin filtrar por condición migratoria "
            "de turista ni por residencia en el exterior, así que es un concepto más "
            "amplio que el de Cancún. No existe publicación oficial de 'turistas "
            "extranjeros no residentes por vía aérea' desagregada por aeropuerto en "
            "Colombia.",
            "passengers_air_*: pasajeros de vuelos de SERVICIO REGULAR (tipo_vuelo='R') "
            "en el aeropuerto Rafael Núñez; excluye charter/adicional/taxi aéreo (~1% del "
            "tráfico). Las series *_arrivals son sólo llegadas y son las que reproduce el "
            "informe mensual del MinCIT; las *_total suman llegadas + salidas y son las "
            "comparables con passengers_air_*_total de Cancún y Los Cabos. Cuentan "
            "pasajeros (colombianos y extranjeros), no turistas.",
            "Sin datos aéreos de 2019: el portal de datos abiertos de Aerocivil sólo "
            "publica origen-destino desde 2020, y www.aerocivil.gov.co devuelve HTTP 403 "
            "a todo cliente disponible (incluido un navegador real), por lo que no se "
            "pudo recuperar 2019. Entre abr-2020 y ago-2020 no hay registros (cierre del "
            "espacio aéreo): esos meses se omiten en lugar de imputar ceros.",
            "hotel_occupancy_rate: la EMA del DANE trata 'Cartagena' como dominio "
            "geográfico propio (el resto de Bolívar va en la región 'Golfo de Morrosquillo "
            "y Sabana'). Es ocupación de HABITACIONES (ocupadas/disponibles) en "
            "establecimientos formales con NIT y ≥10 ocupados o ingresos ≥$400 millones "
            "de 2017 (muestra nacional de 1.244 establecimientos: hoteles, apartahoteles, "
            "centros vacacionales, alojamiento rural, hostales y camping). Cifras "
            "provisionales para los meses recientes.",
            "hotel_rate_index_single: el DANE sólo publica ÍNDICES de tarifa (base "
            "2019=100) por tipo de acomodación, no la tarifa media en pesos ni en dólares; "
            "no hay ADR oficial para Cartagena. Tampoco se publica inventario de "
            "habitaciones para Cartagena (la EMA publica sólo índices de oferta y demanda; "
            "el Registro Nacional de Turismo lista establecimientos, no un inventario "
            "estadístico de habitaciones).",
            "NO EXISTE gasto turístico oficial a nivel de ciudad para Cartagena. Ni el "
            "MinCIT, ni el DANE, ni Corpoturismo / la Corporación Turismo Cartagena de "
            "Indias publican gasto, derrama o gasto medio por turista para el destino. "
            "La Encuesta de Gasto Interno en Turismo (EGIT, DANE) mide turismo INTERNO de "
            "las 24 ciudades principales y está inactiva desde 2024. Por eso sólo se "
            "incluye la cifra NACIONAL.",
            "spending_tourism_receipts_national: es 'exportaciones en la cuenta de viajes "
            "y TRANSPORTE de pasajeros' de la balanza de pagos (definición que usa el "
            "MinCIT), no sólo 'viajes'; incluye cruceristas y transporte aéreo de "
            "pasajeros. Es ANUAL porque el Banco de la República no expone la subcuenta "
            "de viajes como serie trimestral descargable en su portal de estadísticas "
            "(sólo 'servicios' neto) y www.banrep.gov.co está protegido por Radware Bot "
            "Manager. El informe del MinCIT publica además el primer trimestre de cada "
            "año de forma aislada (2023-I a 2026-I), que no forma serie trimestral "
            "continua y por eso no se carga.",
            "spending_avg_stay: no disponible. Ni la EMA del DANE ni el MinCIT publican "
            "estadía promedio para Cartagena ni para Colombia.",
            "Cruceros (no solicitados): el MinCIT publica pasajeros de cruceros "
            "internacionales sólo a nivel nacional en el informe mensual; el dato por "
            "puerto de Cartagena lo difunde la Sociedad Portuaria Regional de Cartagena a "
            "través de los boletines de Corpoturismo (acumulado de temporada, no serie "
            "mensual). No se incluye.",
            "Los boletines 'Indicadores de Turismo' de Corpoturismo (corpoturismo.com) son "
            "infografías con acumulados año-corrido y su ocupación proviene de COTELCO y "
            "ASOTELCA (gremios privados, muestra de afiliados), no de una operación "
            "estadística oficial; por eso se usa la EMA del DANE.",
        ]}
    path = ROOT / 'CO-CTG.json'
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    print('\n== SERIES WRITTEN to %s ==' % path)
    for s in series:
        print('  %-42s %-9s %3d  %s..%s [%s]' % (
            s['key'], s['frequency'], len(s['data']), s['data'][0][0], s['data'][-1][0],
            s['unit']))
    bad = checks.count(False)
    print('\n== %d/%d checks passed ==' % (len(checks) - bad, len(checks)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
