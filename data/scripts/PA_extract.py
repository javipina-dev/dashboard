"""Panamá (INEC) extraction.

Usage:  python data/scripts/PA_extract.py [--no-download]
  --no-download  re-parse the files already present in data/raw/PA/ (fails if any is missing)

Inputs (WD/raw/PA): ep_<YYYY>.xls = INEC "Principales Indicadores Económicos Mensuales <YYYY>" cuadro
"Entrada de viajeros y sus gastos" (Sección de Balanza de Pagos, INEC).
Discovery (every run):
  1. https://www.inec.gob.pa/avance/Default.aspx?ID_CATEGORIA=1&ID_IDIOMA=1 (Avance en cifras >
     Indicadores de coyuntura) lists "Principales Indicadores Económicos Mensuales - <YYYY>" with its
     ID_CIFRAS (2019=40, 2020=43, 2021=45, 2022=47, 2023=48, 2024=50, 2025=51, 2026=52); a new year
     (2027...) is picked up from this listing, no ID is hard-coded.
  2. https://www.inec.gob.pa/avance/Default2.aspx?ID_CATEGORIA=1&ID_CIFRAS=<id>&ID_IDIOMA=1 links
     ../archivos/<timestamp>_entrada_pasajeros.xls (name changes with every update).
Download policy: files of the current and previous year are downloaded on every run; older (closed)
years are re-used from data/raw/PA/ only if the file name linked by INEC is unchanged (recorded in
data/raw/PA/sources.json), otherwise downloaded again. Any failure -> exit 1, PA.json untouched.
Each file has annual rows for 4 previous years, then monthly rows for previous year and current year (P).
For each year we use the most recent file that contains all published months (year Y+1 file if it has 12 months, else year Y file).
"""
import json, os, re, sys
from datetime import date
from urllib.parse import urljoin
import pandas as pd
import requests
from bs4 import BeautifulSoup

WD = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(WD, 'raw', 'PA')
OUT = os.path.join(WD, 'PA.json')
MESES = ['Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio', 'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre']
COLS = {1: 'total_viajeros', 2: 'visitantes', 3: 'turistas', 4: 'excursionistas', 5: 'cruceros', 6: 'transito_total', 7: 'transito_tocumen', 8: 'transito_cruceros', 9: 'gasto'}
RETRIEVED = date.today().isoformat()
DOWNLOAD = '--no-download' not in sys.argv
FIRST_YEAR = 2019
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'
LISTING = 'https://www.inec.gob.pa/avance/Default.aspx?ID_CATEGORIA=1&ID_IDIOMA=1'
PAGE = 'https://www.inec.gob.pa/avance/Default2.aspx?ID_CATEGORIA=1&ID_CIFRAS={id}&ID_IDIOMA=1'
KNOWN_IDS = {2019: 40, 2020: 43, 2021: 45, 2022: 47, 2023: 48, 2024: 50, 2025: 51, 2026: 52}  # sanity check only
META = os.path.join(RAW, 'sources.json')
FILE_URLS = {}   # year -> xls URL
PAGE_URLS = {}   # year -> INEC page URL


def _get(sess, url):
    r = sess.get(url, timeout=120)
    r.raise_for_status()
    return r


def discover_year_ids(sess):
    """year -> ID_CIFRAS from the official INEC listing of 'Principales Indicadores Económicos Mensuales - <YYYY>'."""
    soup = BeautifulSoup(_get(sess, LISTING).text, 'lxml')
    ids = {}
    for a in soup.find_all('a', href=True):
        t = a.get_text(' ', strip=True)
        m = re.match(r'Principales Indicadores Econ[oó]micos Mensuales\s*-\s*(20\d\d)\b', t)
        mi = re.search(r'ID_CIFRAS=(\d+)', a['href'])
        if m and mi and int(m.group(1)) >= FIRST_YEAR:
            y = int(m.group(1))
            if y in ids and ids[y] != int(mi.group(1)):
                raise RuntimeError(f'INEC: dos ID_CIFRAS para {y}: {ids[y]} y {mi.group(1)}')
            ids[y] = int(mi.group(1))
    if not ids:
        raise RuntimeError(f'INEC: no se encontró ningún "Principales Indicadores Económicos Mensuales - <año>" en {LISTING}')
    years = list(range(FIRST_YEAR, max(ids) + 1))
    missing = [y for y in years if y not in ids]
    if missing:
        raise RuntimeError(f'INEC: faltan años en el listado {LISTING}: {missing}')
    for y, i in KNOWN_IDS.items():
        if ids.get(y) != i:
            raise RuntimeError(f'INEC: ID_CIFRAS de {y} cambió ({i} -> {ids.get(y)}); revisar el listado')
    if max(ids) < date.today().year - 1:
        raise RuntimeError(f'INEC: el año más reciente del listado es {max(ids)}; se esperaba al menos {date.today().year - 1}')
    return {y: ids[y] for y in years}


def find_xls(sess, page_url):
    soup = BeautifulSoup(_get(sess, page_url).text, 'lxml')
    links = sorted({urljoin(page_url, a['href']) for a in soup.find_all('a', href=True)
                    if re.search(r'entrada_pasajeros\.xls$', a['href'], flags=re.I)})
    if len(links) != 1:
        raise RuntimeError(f'INEC: se esperaba 1 enlace *entrada_pasajeros.xls en {page_url}, hay {len(links)}: {links}')
    return links[0]


def fetch_all():
    """Locate and download ep_<YYYY>.xls; return the list of years."""
    os.makedirs(RAW, exist_ok=True)
    meta = json.load(open(META, encoding='utf-8')) if os.path.exists(META) else {}
    if not DOWNLOAD:
        if not meta:
            raise RuntimeError(f'--no-download: falta {META}; corre sin --no-download primero')
        years = sorted(int(y) for y in meta)
        for y in years:
            if not os.path.exists(os.path.join(RAW, f'ep_{y}.xls')):
                raise RuntimeError(f'--no-download: falta {os.path.join(RAW, f"ep_{y}.xls")}')
            FILE_URLS[y], PAGE_URLS[y] = meta[str(y)]['file_url'], meta[str(y)]['page_url']
        print('usando archivos locales (--no-download)')
        return years
    sess = requests.Session()
    sess.headers['User-Agent'] = UA
    ids = discover_year_ids(sess)
    print('INEC ID_CIFRAS por año:', ids)
    cur = date.today().year
    new_meta = {}
    for y, i in ids.items():
        page_url = PAGE.format(id=i)
        url = find_xls(sess, page_url)
        dest = os.path.join(RAW, f'ep_{y}.xls')
        prev = meta.get(str(y), {})
        if y < cur - 1 and prev.get('file_url') == url and os.path.exists(dest):
            print(f'ep_{y}.xls: año cerrado, archivo INEC sin cambios, se reutiliza ({url})')
        else:
            r = _get(sess, url)
            if not r.content.startswith(bytes.fromhex('D0CF11E0A1B11AE1')):
                raise RuntimeError(f'la descarga de {url} no es un .xls (content-type {r.headers.get("content-type")})')
            tmp = dest + '.part'
            with open(tmp, 'wb') as f:
                f.write(r.content)
            os.replace(tmp, dest)
            print(f'descargado ep_{y}.xls <- {url} ({len(r.content)} bytes)')
        FILE_URLS[y], PAGE_URLS[y] = url, page_url
        new_meta[str(y)] = {'id_cifras': i, 'page_url': page_url, 'file_url': url}
    with open(META, 'w', encoding='utf-8') as f:
        json.dump(new_meta, f, indent=1)
    return sorted(ids)


def guard_against_shrink(path, series):
    """Abort if any series already in the current JSON would come out missing, empty, shorter, ending earlier,
    or lose any period it currently has (even if other periods are added)."""
    if not os.path.exists(path):
        return
    old = json.load(open(path, encoding='utf-8'))
    new = {s['key']: s['data'] for s in series}
    problems = []
    for s in old.get('series', []):
        k, od = s['key'], s['data']
        nd = new.get(k) or []
        if not nd:
            problems.append(f'{k}: vacía o ausente (antes {len(od)} períodos)')
        elif len(nd) < len(od):
            problems.append(f'{k}: {len(od)} -> {len(nd)} períodos')
        elif od and nd[-1][0] < od[-1][0]:
            problems.append(f'{k}: último período {od[-1][0]} -> {nd[-1][0]}')
        lost = sorted({p for p, _ in od} - {p for p, _ in nd})
        if nd and lost:
            problems.append(f'{k}: desaparecen {len(lost)} períodos: {", ".join(lost[:10])}' + (' ...' if len(lost) > 10 else ''))
    if problems:
        raise RuntimeError('salvaguarda: la nueva extracción tiene menos datos que ' + os.path.basename(path)
                           + ' (no se escribe): ' + '; '.join(problems))


def parse_file(y):
    df = pd.read_excel(os.path.join(RAW, f'ep_{y}.xls'), header=None)
    out = {}  # (year, month) -> dict
    annual = {}
    cur = None
    stage = 'pre'
    last_annual = None
    for _, row in df.iterrows():
        c0 = str(row[0]).strip()
        if c0.lower().startswith('variaci'):
            break
        if re.fullmatch(r'Enero-\w+', c0):
            stage = 'annual'
            continue
        m = re.fullmatch(r'(20\d\d)(?: \(P\))?', c0)
        if m:
            yy = int(m.group(1))
            if '(P)' in c0:
                cur = yy
                stage = 'months'
            else:
                last_annual = yy
                annual[yy] = {COLS[k]: row[k] for k in COLS}
            continue
        if c0 in MESES:
            if stage == 'annual':
                cur = last_annual
                stage = 'months'
            mm = MESES.index(c0) + 1
            out[(cur, mm)] = {COLS[k]: row[k] for k in COLS}
    return out, annual


def to_int_thousands(v):
    x = float(v) * 1000
    return int(round(x))


def main():
    years = fetch_all()
    last_file = years[-1]
    parsed = {y: parse_file(y) for y in years}
    chosen = {}
    used = {}
    for y in years:
        nxt = parsed.get(y + 1)
        if nxt and len([k for k in nxt[0] if k[0] == y]) == 12:
            src = y + 1
        else:
            src = y
        for (yy, mm), rec in parsed[src][0].items():
            if yy == y:
                chosen[(yy, mm)] = rec
        used[y] = src
    print('source file per year:', used)
    keys = sorted(chosen)
    # sanity: sums of months vs annual rows in later files (where available)
    for y in range(FIRST_YEAR, last_file - 1):
        f = used[y]
        ann = None
        for yy in range(y + 1, last_file + 1):
            if y in parsed[yy][1]:
                ann = parsed[yy][1][y]
                break
        if ann is not None and len([k for k in keys if k[0] == y]) == 12:
            for col in ('turistas', 'visitantes', 'gasto'):
                s = sum(float(chosen[(y, m)][col]) for m in range(1, 13))
                if abs(s - float(ann[col])) > 0.01 * abs(float(ann[col])):
                    print('WARN annual mismatch', y, col, s, ann[col])
    non_int = [(k, chosen[k]['turistas']) for k in keys if abs(float(chosen[k]['turistas']) * 1000 - round(float(chosen[k]['turistas']) * 1000)) > 1e-6]
    if non_int:
        print('non-integer persons (rounded):', non_int[:10])

    def ser(col, conv):
        return [[f'{y}-{m:02d}', conv(chosen[(y, m)][col])] for (y, m) in keys]

    last = keys[-1]
    last_p = f'{last[0]}-{last[1]:02d}'
    base_src = {
        'org': 'Instituto Nacional de Estadística y Censo (INEC), Contraloría General de la República de Panamá – Sección de Balanza de Pagos (datos de Servicio Nacional de Migración, ATP, AMP)',
        'title': 'Principales Indicadores Económicos Mensuales – Entrada de viajeros y sus gastos',
        'page_url': PAGE_URLS[last_file],
        'file_url': FILE_URLS[last_file],
        'format': 'xlsx', # archivo .xls (Excel 97-2003); también .csv
        'update_frequency': 'mensual',
        'release_lag': '~6-7 semanas (julio 2026 publicado 15-09-2026)',
        'last_period': last_p,
        'retrieved': RETRIEVED,
        'access_method': 'Leer el listado INEC Avance en cifras > Indicadores de coyuntura (Default.aspx?ID_CATEGORIA=1) para obtener el ID_CIFRAS de cada "Principales Indicadores Económicos Mensuales - <año>"; en cada página Default2.aspx?ID_CIFRAS=<id> tomar el enlace ../archivos/*entrada_pasajeros.xls (también .csv/.pdf; nombre con timestamp cambia en cada actualización) y descargarlo (año en curso y anterior en cada corrida). Leer XLS (xlrd): filas de meses del año previo y del año en curso (P); cifras en miles de personas con 3 decimales.',
    }
    series = [
        {'key': 'arrivals_stopover', 'category': 'arrivals', 'label': 'Entrada de turistas (visitantes que pernoctan)', 'unit': 'personas',
         'frequency': 'monthly', 'data': ser('turistas', to_int_thousands), 'source': dict(base_src)},
        {'key': 'arrivals_visitors_total', 'category': 'arrivals', 'label': 'Entrada de visitantes (turistas + excursionistas + pasajeros de cruceros)', 'unit': 'personas',
         'frequency': 'monthly', 'data': ser('visitantes', to_int_thousands), 'source': dict(base_src)},
        {'key': 'arrivals_excursionists', 'category': 'arrivals', 'label': 'Entrada de excursionistas (sin pernoctación, excl. cruceros)', 'unit': 'personas',
         'frequency': 'monthly', 'data': ser('excursionistas', to_int_thousands), 'source': dict(base_src)},
        {'key': 'spending_tourism_receipts', 'category': 'spending', 'label': 'Gastos efectuados por los viajeros (visitantes) en Panamá', 'unit': 'US$ millones (balboas; B/.1 = US$1)',
         'frequency': 'monthly', 'data': ser('gasto', lambda v: round(float(v) / 1000, 3)), 'source': dict(base_src)},
    ]
    out = {'id': 'PA', 'name': 'Panamá', 'type': 'country', 'lat': 8.98, 'lon': -79.52, 'series': series,
           'notes': [
               'Turistas = visitantes no residentes que pernoctan (>24 h); excursionistas = visitantes que no pernoctan (estadía menor a un día; ATP los reporta por terminales de Tocumen y fronteras); pasajeros en cruceros se cuentan aparte. Visitantes = turistas + excursionistas + cruceros. Excluye pasajeros en tránsito directo y tripulantes (Tocumen y cruceros).',
               'Cobertura: todos los puertos de entrada (Tocumen y otros aeropuertos, fronteras terrestres, puertos). INEC no publica en este cuadro el desglose solo-Tocumen de turistas; la ATP publica informes estadísticos PDF con detalle por puerto de entrada.',
               'Cifras publicadas en miles de personas con 3 decimales; convertidas a personas (×1000). Algunas celdas traen un cuarto decimal (redondeo del computador).',
               'Gasto: "Gastos efectuados" de la Sección de Balanza de Pagos del INEC, en miles de balboas; convertido a millones (B/. a la par con US$). Base para el crédito de "Viajes" de la balanza de pagos.',
               'Cifras del año más reciente son preliminares (P) y se revisan; para cada año se usa el archivo más reciente con los 12 meses (p.ej. 2019 del archivo 2020).',
               'Desde 2019 los pasajeros de cruceros en tránsito y tripulantes se reclasifican a "Tránsito directo y tripulantes"; la serie de cruceros no es comparable con años previos.',
               'Gasto de INEC no incluye transporte internacional (según ATP, que reproduce la cifra como "ingreso de divisas").',
               'Gasto medio y estadía media: INEC no los publica como serie. La ATP (Informe estadístico enero-abril 2026, https://www.atp.gob.pa/wp-content/uploads/2026/06/Informe-estadistico-enero-a-abril-2026.pdf) indica en texto: estadía promedio ~8 días, gasto promedio por estadía ~B/.2,051 y ~B/.256 diarios (período ene-abr 2026); no se cargan como serie por ser estimaciones narrativas de período irregular.',
               'La ATP publica "Informes/Resúmenes estadísticos" PDF (https://www.atp.gob.pa/estadisticas-e-informacion-del-mercado-2/) con turistas, excursionistas por Tocumen y fronteras y cruceros; sus cifras mensuales difieren levemente de INEC (p.ej. turistas ene-2026: ATP 273,533 vs INEC 273,392).',
           ]}
    guard_against_shrink(OUT, series)
    tmp = OUT + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)
    for s in series:
        print(s['key'], s['data'][0], s['data'][-1], len(s['data']))


if __name__ == '__main__':
    try:
        main()
    except (requests.RequestException, RuntimeError, OSError) as e:
        print(f'ERROR PA: {e}', file=sys.stderr)
        print('PA.json no se modificó.', file=sys.stderr)
        sys.exit(1)
