"""Costa Rica (ICT) extraction.

Usage:  python data/scripts/CR_extract.py [--no-download]
  --no-download  re-parse the files already present in data/raw/CR/ (fails if any is missing)

By default every run downloads the four ICT documents again (all of them cover the current or
previous year and are replaced in place by the ICT, so none is cached). The documents are located
on the official ICT listing pages instead of fixed IDs, so a new document (new ID) is picked up:

  recientes_<YYYY>.pdf <- https://www.ict.go.cr/es/estadisticas/informes-estadisticos.html
        section "Información Reciente" (Movimientos Migratorios): the doclink with the highest year
        (e.g. "2026" -> /en/documents/estadísticas/informes-estadísticos/recientes/2893-2025.html).
        ICT monthly report "Llegadas internacionales <YYYY>": Cuadro 11 todas las vías /
        Cuadro 12 vía aérea, 2019-<YYYY> by month.
  divisas.pdf   <- https://www.ict.go.cr/es/estadisticas/cifras-turisticas.html, link "Divisas Turismo <YYYY>"
        (e.g. .../cifras-económicas/costa-rica/3286-divisas-turismo-2025-bccr.html)
  gmp_todas.pdf <- same page, link "GMP TODAS VIAS <YYYY> <YYYY>" (e.g. 2589-gmp-todas-vias-2015-2025)
  gmp_aerea.pdf <- same page, link "GMP y Estadia Media VIA AEREA <YYYY> <YYYY>" (e.g. 2590-...-2006-2025)
The file itself is served at <document page without .html>/file.html (Joomla DOCman).
If a listing page or a download fails, the script exits with an error and CR.json is not written.
"""
import json, os, re, sys, unicodedata
from datetime import date
from urllib.parse import urljoin, quote, unquote
import pdfplumber
import requests
from bs4 import BeautifulSoup

WD = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(WD, 'raw', 'CR')
OUT = os.path.join(WD, 'CR.json')
MESES = ['Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio', 'Julio', 'Agosto', 'Setiembre', 'Octubre', 'Noviembre', 'Diciembre']
ABREV = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Set', 'Oct', 'Nov', 'Dic']
RETRIEVED = date.today().isoformat()
DOWNLOAD = '--no-download' not in sys.argv
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'
ICT = 'https://www.ict.go.cr'
LIST_INFORMES = ICT + '/es/estadisticas/informes-estadisticos.html'
LIST_CIFRAS = ICT + '/es/estadisticas/cifras-turisticas.html'
META = os.path.join(RAW, 'sources.json')  # where each raw file came from (used by --no-download)


def _fold(s):
    return unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode().lower()


def _file_url(href):
    """DOCman document page (.../2893-2025.html) -> direct file URL (.../2893-2025/file.html), percent-encoded."""
    u = urljoin(ICT, href)
    if not u.endswith('.html'):
        raise RuntimeError(f'enlace ICT inesperado (no termina en .html): {u}')
    u = u[:-5] + '/file.html'
    scheme, rest = u.split('://', 1)
    host, path = rest.split('/', 1)
    return f'{scheme}://{host}/' + quote(unquote(path), safe='/-._~')


def _get(sess, url):
    r = sess.get(url, timeout=120)
    r.raise_for_status()
    return r


def locate_documents(sess):
    """Find the current document of each source on the official ICT listing pages."""
    docs = {}
    # 1) monthly arrivals report: "Información Reciente" section, highest year
    soup = BeautifulSoup(_get(sess, LIST_INFORMES).text, 'lxml')
    h = next((x for x in soup.find_all('h3') if _fold(x.get_text(' ', strip=True)) == 'informacion reciente'), None)
    if h is None:
        raise RuntimeError(f'no se encontró la sección "Información Reciente" en {LIST_INFORMES}')
    cands = []
    for el in h.find_all_next(['h3', 'a']):
        if el.name == 'h3':
            break
        if not el.get('href') or '/recientes/' not in unquote(el['href']):
            continue
        lab = (el.get('data-title') or el.get_text(' ', strip=True)).strip()
        m = re.search(r'\b(20\d\d)\b', lab)
        if m:
            cands.append((int(m.group(1)), el['href'], lab))
    if not cands:
        raise RuntimeError(f'sin enlaces "recientes" con año en la sección "Información Reciente" de {LIST_INFORMES}')
    y, href, lab = max(cands)
    docs['recientes'] = {'file': f'recientes_{y}.pdf', 'year': y, 'page_url': LIST_INFORMES,
                         'doc_url': urljoin(ICT, href), 'file_url': _file_url(href), 'label': lab}
    # 2) annual spending/stay documents on "Cifras turísticas"
    soup = BeautifulSoup(_get(sess, LIST_CIFRAS).text, 'lxml')
    pats = {'divisas': r'divisas turismo (20\d\d)\b',
            'gmp_todas': r'gmp todas vias (\d{4}) (\d{4})\b',
            'gmp_aerea': r'gmp y estadia media via aerea (\d{4}) (\d{4})\b'}
    found = {k: [] for k in pats}
    for a in soup.find_all('a', href=True):
        t = _fold(re.sub(r'[\s\-]+', ' ', a.get_text(' ', strip=True)))
        for k, pat in pats.items():
            m = re.search(pat, t)
            if m:
                found[k].append((tuple(int(g) for g in m.groups())[::-1], a['href'], a.get_text(' ', strip=True)))
    for k, lst in found.items():
        if not lst:
            raise RuntimeError(f'no se encontró el enlace "{pats[k]}" en {LIST_CIFRAS}')
        yrs, href, lab = max(lst)  # most recent end year
        d = {'file': f'{k}.pdf', 'end_year': yrs[0], 'page_url': LIST_CIFRAS,
             'doc_url': urljoin(ICT, href), 'file_url': _file_url(href), 'label': lab}
        if len(yrs) == 2:
            d['start_year'] = yrs[1]
        docs[k] = d
    return docs


def download(sess, url, dest):
    r = _get(sess, url)
    if not r.content.startswith(b'%PDF'):
        raise RuntimeError(f'la descarga de {url} no es un PDF (content-type {r.headers.get("content-type")})')
    tmp = dest + '.part'
    with open(tmp, 'wb') as f:
        f.write(r.content)
    os.replace(tmp, dest)
    cd = r.headers.get('content-disposition', '')
    print(f'descargado {os.path.basename(dest)} <- {url} ({len(r.content)} bytes) {cd}')


def fetch_all():
    os.makedirs(RAW, exist_ok=True)
    if not DOWNLOAD:
        if not os.path.exists(META):
            raise RuntimeError(f'--no-download: falta {META}; corre sin --no-download primero')
        docs = json.load(open(META, encoding='utf-8'))
        for d in docs.values():
            if not os.path.exists(os.path.join(RAW, d['file'])):
                raise RuntimeError(f'--no-download: falta {os.path.join(RAW, d["file"])}')
        print('usando archivos locales (--no-download), descargados el', docs.get('_retrieved', '?'))
        return docs
    sess = requests.Session()
    sess.headers['User-Agent'] = UA
    docs = locate_documents(sess)
    for d in docs.values():
        download(sess, d['file_url'], os.path.join(RAW, d['file']))
    with open(META, 'w', encoding='utf-8') as f:
        json.dump(docs, f, ensure_ascii=False, indent=1)
    return docs


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


def parse_month_year_table(page, title_regex, totals=None):
    """Parse 'Mes | 2019 ... 2026' table located below a title matching title_regex on page.
    If `totals` is a dict, the printed cumulative rows ('Ene - Dic', 'Ene - Ago', ...) are stored in it
    as totals[last_month_number][year] = value, for validation."""
    words = page.extract_words(x_tolerance=1.5, y_tolerance=2)
    lines = {}
    for w in words:
        lines.setdefault(round(w['top']), []).append(w)
    tops = sorted(lines)
    # locate title
    t0 = None
    for t in tops:
        txt = ' '.join(w['text'] for w in sorted(lines[t], key=lambda w: w['x0']))
        if re.search(title_regex, txt):
            t0 = t
            break
    assert t0 is not None, title_regex
    # header line with years after title
    hdr = None
    for t in tops:
        if t <= t0:
            continue
        ys = [w for w in lines[t] if re.fullmatch(r'20\d\d', w['text'])]
        if len(ys) >= 6:
            hdr = ys
            htop = t
            break
    cols = sorted([(int(w['text']), w['x1'] + 12) for w in hdr], key=lambda c: c[1])
    out = {}
    for t in tops:
        if t <= htop:
            continue
        lw = sorted(lines[t], key=lambda w: w['x0'])
        label = lw[0]['text']
        if label.startswith('Nota') or label.startswith('Cuadro'):
            break
        if totals is not None and label == 'Ene':
            i = 0
            while i < len(lw) and not re.fullmatch(r'\d{1,3}', lw[i]['text']):
                i += 1
            mt = re.fullmatch(r'Ene ?- ?(\w{3})', ' '.join(w['text'] for w in lw[:i]))
            if mt and mt.group(1) in ABREV:
                buckets = {}
                for w in lw[i:]:
                    assert re.fullmatch(r'\d{1,3}', w['text']), w['text']
                    cand = [c for c in cols if c[1] >= w['x1'] - 1]
                    y = min(cand, key=lambda c: c[1])[0]
                    buckets.setdefault(y, []).append(w['text'])
                totals[ABREV.index(mt.group(1)) + 1] = {y: int(''.join(p)) for y, p in buckets.items()}
            continue
        if label not in MESES:
            continue
        m = MESES.index(label) + 1
        buckets = {}
        for w in lw[1:]:
            assert re.fullmatch(r'\d{1,3}', w['text']), w['text']
            cand = [c for c in cols if c[1] >= w['x1'] - 1]
            y = min(cand, key=lambda c: c[1])[0]
            buckets.setdefault(y, []).append(w['text'])
        for y, parts in buckets.items():
            out[(y, m)] = int(''.join(parts))
    return out


def es_num(s):
    return float(s.replace(' ', '').replace(',', '.'))


def main():
    docs = fetch_all()
    rd = docs['recientes']
    pdf = pdfplumber.open(os.path.join(RAW, rd['file']))
    # page holding Cuadro 11 (todas las vías) and Cuadro 12 (vía aérea); page 12 (index 11) in 2026 reports
    pages = [pg for pg in pdf.pages
             if re.search(r'turistas por TODAS LAS V', pg.extract_text() or '')
             and re.search(r'turistas por V[ÍI]A A[ÉE]REA', pg.extract_text() or '')
             and re.search(r'^Ene - Dic ', pg.extract_text() or '', flags=re.M)]
    assert len(pages) == 1, f'se esperaba 1 página con Cuadros 11-12 en {rd["file"]}, hay {len(pages)}'
    pg = pages[0]
    tot_t, tot_a = {}, {}
    todas = parse_month_year_table(pg, r'turistas por TODAS LAS V', tot_t)
    aerea = parse_month_year_table(pg, r'turistas por V[ÍI]A A[ÉE]REA', tot_a)
    text = pg.extract_text()
    for name, d in (('todas', todas), ('aerea', aerea)):
        print(name, 'months:', len(d), 'first', min(d), 'last', max(d))
    # validate monthly rows against the cumulative rows printed in the same table (Ene - Dic, Ene - <último mes>)
    for name, d, tot in (('todas', todas, tot_t), ('aerea', aerea, tot_a)):
        assert 12 in tot, f'{name}: falta la fila "Ene - Dic"'
        partial = [k for k in tot if k != 12]
        assert len(partial) <= 1, (name, sorted(tot))
        last_y = max(y for y, _ in d)
        last_m = max(m for y, m in d if y == last_y)
        if last_m < 12:
            assert partial == [last_m], f'{name}: fila acumulada Ene-{ABREV[partial[0] - 1] if partial else "?"} no coincide con último mes {last_y}-{last_m:02d}'
            assert last_y in tot[last_m], f'{name}: la fila Ene-{ABREV[last_m - 1]} no trae {last_y}'
        for k, row in tot.items():
            for y, v in row.items():
                s = sum(d[(y, m)] for m in range(1, k + 1))
                assert s == v, (name, f'Ene-{ABREV[k - 1]}', y, s, v)
    print('cumulative-row checks OK')
    exp_t = [3139008, 1011912, 1347055, 2349537, 2751134, 2919483, 2943991]
    exp_a = [2418300, 789833, 1270483, 2117960, 2471150, 2661488, 2689278]
    for d, exp in ((todas, exp_t), (aerea, exp_a)):
        for i, y in enumerate(range(2019, 2026)):
            s = sum(d[(y, m)] for m in range(1, 13))
            assert s == exp[i], (y, s, exp[i])
    print('annual checks OK')

    def ser(d):
        return [[f'{y}-{m:02d}', d[(y, m)]] for (y, m) in sorted(d)]

    last = max(todas)
    last_p = f'{last[0]}-{last[1]:02d}'
    arr_src = {
        'org': 'Instituto Costarricense de Turismo (ICT), Departamento de Estadística Turística, con datos de la Dirección General de Migración y Extranjería (DGME)',
        'title': f'Llegadas internacionales de turistas {rd["year"]} (informe mensual acumulado) – Cuadro 11/12: llegadas por mes {min(todas)[0]}-{last[0]}',
        'page_url': rd['page_url'],
        'file_url': rd['file_url'],
        'format': 'pdf',
        'update_frequency': 'mensual (el mismo PDF se reemplaza cada mes; anuario y Excel de series anuales al cierre del año)',
        'release_lag': '~2-4 semanas tras cierre de mes (DGME entrega base en los primeros 10 días del mes siguiente)',
        'last_period': last_p,
        'retrieved': RETRIEVED,
        'access_method': 'Scrapear la página Movimientos Migratorios (informes-estadisticos.html), sección "Información Reciente": enlace DOCman /recientes/<id>-... con el año más alto; descargar <página>/file.html (PDF de texto nativo) en cada corrida; pdfplumber: página con Cuadros 11/12 (meses × años desde 2019), validada contra las filas acumuladas "Ene - Dic"/"Ene - <mes>", números con espacio como separador de miles → asignar palabras a columnas por posición x. Excel de series (3242-series-sitio-web-ict-2025) solo trae totales anuales.',
    }
    series = [
        {'key': 'arrivals_stopover', 'category': 'arrivals', 'label': 'Llegadas internacionales de turistas (todas las vías)',
         'unit': 'personas', 'frequency': 'monthly', 'data': ser(todas), 'source': dict(arr_src)},
        {'key': 'arrivals_air_international', 'category': 'arrivals', 'label': 'Llegadas internacionales de turistas por vía aérea (SJO, LIR, Tobías Bolaños, Limón)',
         'unit': 'personas', 'frequency': 'monthly', 'data': ser(aerea), 'source': dict(arr_src)},
    ]

    # ---- Spending: BCCR divisas por turismo (balanza de pagos, viajes) ----
    dd = docs['divisas']
    dv = pdfplumber.open(os.path.join(RAW, dd['file']))
    t = dv.pages[1].extract_text()
    rec = []
    for m in re.finditer(r'^(20\d\d)(?: 1/)? (\d{1,3}(?: \d{3})*,\d) (\d{1,3}(?: \d{3})*,\d)$', t, flags=re.M):
        y = int(m.group(1))
        if y >= 2019:
            rec.append([str(y), es_num(m.group(2))])
    assert [r[0] for r in rec] == [str(y) for y in range(2019, dd['end_year'] + 1)], (dd['label'], rec)
    series.append({'key': 'spending_tourism_receipts', 'category': 'spending',
                   'label': 'Ingreso de divisas por turismo (balanza de pagos, excluye cruceristas)',
                   'unit': 'US$ millones', 'frequency': 'annual', 'data': rec,
                   'source': {'org': 'Banco Central de Costa Rica (BCCR), publicado por ICT',
                              'title': f'Divisas por concepto de turismo 2000-{dd["end_year"]} (Cuadro 1)',
                              'page_url': dd['page_url'],
                              'file_url': dd['file_url'],
                              'format': 'pdf', 'update_frequency': 'anual (BCCR compila trimestral en balanza de pagos)',
                              'release_lag': f'~2-3 meses tras cierre del año ({dd["end_year"]} preliminar)', 'last_period': rec[-1][0],
                              'retrieved': RETRIEVED,
                              'access_method': 'Enlace "Divisas Turismo <año>" más reciente en ICT > Estadísticas > Cifras turísticas (documento de la categoría Cifras económicas); PDF de texto descargado en cada corrida; regex sobre Cuadro 1. Alternativa trimestral: BCCR Indicadores Económicos, balanza de pagos, cuenta Viajes (crédito).'}})

    # ---- Spending: GMP total (todas las vías) ----
    gd = docs['gmp_todas']
    g = pdfplumber.open(os.path.join(RAW, gd['file']))
    t = g.pages[1].extract_text()
    line = re.search(r'^GMP TOTAL \(en US\$\) (.*)$', t, flags=re.M).group(1)
    vals = re.findall(r'\d{1,3}(?: \d{3})?,\d', line)
    assert len(vals) == gd['end_year'] - gd['start_year'] + 1, (gd['label'], vals)
    gmp = [[str(gd['start_year'] + i), es_num(v)] for i, v in enumerate(vals) if gd['start_year'] + i >= 2019]
    series.append({'key': 'spending_avg_per_visitor', 'category': 'spending',
                   'label': 'Gasto medio por persona de turistas no residentes (todas las vías, por estadía)',
                   'unit': 'US$', 'frequency': 'annual', 'data': gmp,
                   'source': {'org': 'Instituto Costarricense de Turismo (ICT) – Encuestas de No Residentes aéreas y terrestres',
                              'title': f'Gasto medio por persona (GMP) en US$ de los turistas no residentes {gd["start_year"]}-{gd["end_year"]}',
                              'page_url': gd['page_url'],
                              'file_url': gd['file_url'],
                              'format': 'pdf', 'update_frequency': 'anual', 'release_lag': '~3-6 meses tras cierre del año',
                              'last_period': gmp[-1][0], 'retrieved': RETRIEVED,
                              'access_method': 'Enlace "GMP TODAS VIAS <año> <año>" más reciente en ICT > Cifras turísticas > Gasto y estadía media; PDF de texto descargado en cada corrida; regex fila "GMP TOTAL".'}})

    # ---- Average stay (air) ----
    ga = docs['gmp_aerea']
    g2 = pdfplumber.open(os.path.join(RAW, ga['file']))
    t = g2.pages[1].extract_text()
    stay = []
    for m in re.finditer(r'^(20\d\d)\d? (\d{1,3}(?: \d{3})?,\d) (\d{1,2},\d)$', t, flags=re.M):
        y = int(m.group(1))
        if y >= 2019:
            stay.append([str(y), es_num(m.group(3))])
    assert [s[0] for s in stay] == [str(y) for y in range(2019, ga['end_year'] + 1)], (ga['label'], stay)
    series.append({'key': 'spending_avg_stay', 'category': 'spending',
                   'label': 'Estadía media de turistas no residentes (vía aérea)',
                   'unit': 'noches', 'frequency': 'annual', 'data': stay,
                   'source': {'org': 'Instituto Costarricense de Turismo (ICT) – Encuestas No Residentes en aeropuertos internacionales',
                              'title': f'Gasto medio por persona (GMP) y estadía media, vía aérea {ga["start_year"]}-{ga["end_year"]}',
                              'page_url': ga['page_url'],
                              'file_url': ga['file_url'],
                              'format': 'pdf', 'update_frequency': 'anual', 'release_lag': '~3-6 meses tras cierre del año',
                              'last_period': stay[-1][0], 'retrieved': RETRIEVED,
                              'access_method': 'Enlace "GMP y Estadia Media VIA AEREA <año> <año>" más reciente en ICT > Cifras turísticas; PDF de texto descargado en cada corrida; regex "año GMP estadía" en Cuadro 1 (años 2020-2023 llevan superíndice de nota pegado al año).'}})

    out = {'id': 'CR', 'name': 'Costa Rica', 'type': 'country', 'lat': 9.93, 'lon': -84.08, 'series': series,
           'notes': [
               'Llegadas internacionales de turistas = visitantes que pernoctan (no residentes), registradas por la DGME en puestos migratorios; no incluye cruceristas. Todas las vías = aérea + terrestre/fluvial + marítima (sin cruceros).',
               'La vía aérea incluye Juan Santamaría (SJO), Daniel Oduber (LIR, Guanacaste), Tobías Bolaños y Limón. El PDF mensual también desglosa SJO y LIR (Cuadros 13-14) y vía terrestre (solo año en curso).',
               f'Los datos del año en curso ({last[0]}) son preliminares; el PDF "recientes" se sobreescribe mensualmente (el extractor lo localiza en la página de listado del ICT en cada corrida).',
               f'Gasto: ingreso de divisas por turismo proviene de la balanza de pagos del BCCR (MBP6, sin cruceristas); {dd["end_year"]} preliminar. El GMP es por persona y por estadía completa (no por día), ponderado por vía; 2020-2023 con metodología alterada (encuestas parciales/en línea; vía terrestre estimada por BCCR).',
               'Estadía media publicada solo para vía aérea.',
               'Serie mensual ICT disponible en PDF; el Excel "series sitio web" contiene únicamente totales anuales por país.',
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
        print(f'ERROR CR: {e}', file=sys.stderr)
        print('CR.json no se modificó.', file=sys.stderr)
        sys.exit(1)
