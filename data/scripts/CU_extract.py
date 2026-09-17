"""Cuba (ONEI) extraction.

Monthly international visitors come from ONEI "Arribo de viajeros. Visitantes internacionales <Mes> <Año>" PDFs.
Page 1 has cumulative totals (hasta el mes). Page 2 has a chart "Llegadas de visitantes internacionales" whose bars
(current year) carry official data labels for each month published so far (rotated text; lines for previous years are
unlabeled). We read those labels from the PDF text layer (no estimation), and validate that the sum of the monthly
labels equals the official cumulative figure on page 1 (and prior-year cumulative in later reports).

Files (WD/raw/CU), fetched with CU_fetch.py from https://onei.gob.cu (no www; expired TLS cert):
  dic<YYYY>.pdf  -> December ("cierre") report for year YYYY  (full-year monthly labels)
  jul2026.pdf    -> latest report (Jan-Jul 2026)
"""
import glob, json, os, re
from collections import defaultdict
import pdfplumber

WD = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(WD, 'raw', 'CU')
RETRIEVED = '2026-09-17'
MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre']
MON_ABBR = ['ENE', 'FEB', 'MAR', 'ABR', 'MAY', 'JUN', 'JUL', 'AGO', 'SEP', 'OCT', 'NOV', 'DIC']


def num(s):
    return int(re.sub(r'[\s.]', '', s))


def chart_labels(pg):
    """Return list of (x_center, value) for rotated numeric labels in chart, sorted by x."""
    rot = [c for c in pg.chars if abs(c['matrix'][0]) < 0.01 and c['text'].strip()]
    g = defaultdict(list)
    for c in rot:
        g[round(c['x0'])].append(c)
    out = []
    for x, cs in g.items():
        cs = sorted(cs, key=lambda c: -c['top'])  # rotated 90deg: text reads bottom->top
        clusters = [[cs[0]]]
        for c in cs[1:]:
            if clusters[-1][-1]['top'] - c['top'] > 9:
                clusters.append([c])
            else:
                clusters[-1].append(c)
        for k in clusters:
            t = ''.join(c['text'] for c in k)
            if re.fullmatch(r'\d+', t):
                out.append((x, int(t)))
    return sorted(out)


def month_axis(pg):
    ws = pg.extract_words()
    ax = {w['text']: (w['x0'] + w['x1']) / 2 for w in ws if w['text'] in MON_ABBR}
    return ax


def parse_report(path):
    pdf = pdfplumber.open(path)
    t1 = pdf.pages[0].extract_text()
    m = re.search(r'Hasta el mes de (\w+) se recibieron ([\d ]+) viajeros', t1)
    mes = MESES.index(m.group(1).lower()) + 1
    m2 = re.search(r'Se han recibido ([\d ]+) visitantes\s+internacionales hasta el mes de', t1)
    cum_visit = num(m2.group(1))
    hdr = re.search(r'Pa[ií]ses (\d{4}) (\d{4}) \d\d/\d\d', t1)
    prev_y, cur_y = int(hdr.group(1)), int(hdr.group(2))
    vis = re.search(r'De ello: Visitantes ([\d ]+?) ([\d ]+?) [\d,]+\n', t1)
    # "De ello: Visitantes 2 203 117 1 810 663 82,2" -> ambiguous split; resolve using cum_visit (current year)
    tail = vis.group(0)
    digits = re.sub(r'De ello: Visitantes ', '', tail).rsplit(' ', 1)[0]
    cur_s = f'{cum_visit:,}'.replace(',', ' ')
    assert digits.endswith(cur_s), (digits, cur_s)
    prev_cum = num(digits[: -len(cur_s)].strip())
    pg = pdf.pages[1]
    labels = chart_labels(pg)
    ax = month_axis(pg)
    monthly = {}
    for x, v in labels:
        # map to nearest month axis label
        name = min(ax, key=lambda k: abs(ax[k] - (x + 3)))
        mm = MON_ABBR.index(name) + 1
        assert mm not in monthly, (path, name)
        monthly[mm] = v
    return {'path': path, 'year': cur_y, 'prev_year': prev_y, 'month': mes, 'cum_visitors': cum_visit,
            'prev_cum_visitors': prev_cum, 'monthly': monthly}


def main():
    reports = {}
    for p in sorted(glob.glob(os.path.join(RAW, '*.pdf'))):
        b = os.path.basename(p)
        if not re.match(r'(ene|feb|mar|abr|may|jun|jul|ago|sep|oct|nov|dic)\w*\d{4}\.pdf$|arribo_', b):
            continue
        try:
            r = parse_report(p)
        except Exception as e:
            print('skip', b, type(e).__name__, e)
            continue
        print(b, r['year'], r['month'], 'cum', r['cum_visitors'], 'prev', r['prev_cum_visitors'], 'labels', len(r['monthly']))
        reports.setdefault(r['year'], []).append(r)
    data = {}
    used = {}
    for y, rs in reports.items():
        r = max(rs, key=lambda r: r['month'])  # latest report for that year
        months = sorted(r['monthly'])
        assert months == list(range(1, r['month'] + 1)), (y, months)
        s = sum(r['monthly'].values())
        assert s == r['cum_visitors'], (y, s, r['cum_visitors'])
        for m, v in r['monthly'].items():
            data[(y, m)] = v
        used[y] = os.path.basename(r['path'])
    # cross-check: prior-year cumulative in later reports equals sum of our months
    for y, rs in reports.items():
        for r in rs:
            py = r['prev_year']
            ms = [data.get((py, m)) for m in range(1, r['month'] + 1)]
            if all(v is not None for v in ms):
                if sum(ms) != r['prev_cum_visitors']:
                    print('NOTE prior-year cum differs', os.path.basename(r['path']), py, sum(ms), r['prev_cum_visitors'])
    keys = sorted(data)
    print('used', used, 'range', keys[0], keys[-1], len(keys))
    series_data = [[f'{y}-{m:02d}', data[(y, m)]] for y, m in keys]
    last = f'{keys[-1][0]}-{keys[-1][1]:02d}'
    series = [{
        'key': 'arrivals_visitors_total', 'category': 'arrivals', 'label': 'Llegadas de visitantes internacionales',
        'unit': 'personas', 'frequency': 'monthly', 'data': series_data,
        'source': {'org': 'Oficina Nacional de Estadística e Información (ONEI), con datos de la Dirección de Identificación, Inmigración y Extranjería (DIIE)',
                   'title': 'Arribo de viajeros. Visitantes internacionales <Mes> <Año> (información preliminar mensual; cierre de diciembre)',
                   'page_url': 'https://www.onei.gob.cu/arribo-de-viajeros-visitantes-internacionales-julio-2026',
                   'file_url': 'https://www.onei.gob.cu/sites/default/files/publicaciones/2026-08/arribo-de-viajeros-visitantes-internacionales-julio-2026.pdf',
                   'format': 'pdf', 'update_frequency': 'mensual',
                   'release_lag': '~2-5 semanas tras cierre de mes (carpeta /publicaciones/<AAAA-MM> del mes de publicación)',
                   'last_period': last, 'retrieved': RETRIEVED,
                   'access_method': 'Página de nodo https://onei.gob.cu/arribo-de-viajeros-visitantes-internacionales-<mes>-<año> enlaza PDF (+ .rar y visitantes-por-paises-<mes>-<año>.xlsx). Usar host sin "www" (www.onei.gob.cu HTTPS da error de proxy y HTTP sirve página JS intermedia); certificado TLS vencido y conexiones intermitentes → reintentos. El PDF (texto) solo trae acumulados en tabla; los valores mensuales del año en curso están como etiquetas de datos del gráfico de barras (texto rotado) → pdfplumber agrupando caracteres por x; se valida suma = acumulado oficial. Nombres de archivo varían (p.ej. "arribo-de-viajeros.-visitantes-..." en 2023-2024).'}}]
    return series, used


# ---------------------------------------------------------------------------
# Anuario Estadístico de Cuba (AEC), capítulo 15 Turismo (PDF). AEC 2024 (ed. 2025) uses subset fonts whose text
# layer comes out as (cid:N); mapping is chr(N+29) (validated against identical figures in AEC 2023).
AEC = {
    'aec2024_15_turismo.pdf': 'https://www.onei.gob.cu/sites/default/files/publicaciones/2025-08/15-turismo_aec2024.pdf',
    'aec2023_15_turismo.pdf': 'https://www.onei.gob.cu/sites/default/files/publicaciones/2024-10/15-turismo.pdf',
}
MES_FULL = [m.capitalize() for m in MESES]


def aec_text(fname):
    pdf = pdfplumber.open(os.path.join(RAW, fname))
    t = '\n'.join(pg.extract_text() or '' for pg in pdf.pages)
    return re.sub(r'\(cid:(\d+)\)', lambda m: chr(int(m.group(1)) + 29), t)


def aec_block(text, title_regex, nlines=16):
    m = re.search(title_regex + r'[^\n]*\n((?:.*\n){%d})' % nlines, text)
    assert m, title_regex
    lines = m.group(1).split('\n')
    years = None
    for l in lines:
        ys = re.findall(r'\b(20\d\d)\b', l)
        if len(ys) == 5 and l.strip().startswith('CONCEPTO'):
            years = [int(y) for y in ys]
            break
    return years, lines


def aec_rows(fname, title_regex, max_rows=16):
    """Position-based table parsing: find page containing title; group words per line; merge numeric words
    separated by a small gap (thousands separator) into one value."""
    pdf = pdfplumber.open(os.path.join(RAW, fname))
    dec = lambda t: re.sub(r'\(cid:(\d+)\)', lambda m: chr(int(m.group(1)) + 29), t)
    for pg in pdf.pages:
        ws = [dict(w, text=dec(w['text'])) for w in pg.extract_words(x_tolerance=1.5, keep_blank_chars=True)]
        lines = defaultdict(list)
        for w in ws:
            lines[round(w['top'])].append(w)
        tops = sorted(lines)
        txts = {t: ' '.join(w['text'] for w in sorted(lines[t], key=lambda w: w['x0'])) for t in tops}
        t0 = next((t for t in tops if re.search(title_regex, txts[t])), None)
        if t0 is None:
            continue
        rows = []
        years = None
        for t in tops:
            if t <= t0:
                continue
            lw = sorted(lines[t], key=lambda w: w['x0'])
            txt = txts[t].strip()
            if years is None:
                ys = re.findall(r'\b(20\d\d)\b', txt)
                if len(ys) == 5:
                    years = [int(y) for y in ys]
                continue
            if txt.startswith('Fuente') or txt.startswith('Nota') or re.match(r'15\.\d+', txt):
                break
            # tokens: split words into pieces, keep x positions of pieces approx by word
            pieces = []
            for w in lw:
                for part in re.findall(r'\S+', w['text']):
                    pieces.append((w['x0'], w['x1'], part))
            label = ' '.join(pp for _, _, pp in pieces if not re.fullmatch(r'[\d.,]+', pp))
            nums = []
            prev_x1 = None
            for w in lw:
                parts = re.findall(r'\S+', w['text'])
                if all(re.fullmatch(r'[\d,]+', pp) for pp in parts):
                    val = ''.join(parts)
                    if prev_x1 is not None and w['x0'] - prev_x1 < 4 and nums:
                        nums[-1] += val
                    else:
                        nums.append(val)
                    prev_x1 = w['x1']
                else:
                    prev_x1 = None
            rows.append((label.strip(), nums))
            if len(rows) >= max_rows:
                break
        return years, rows
    raise AssertionError((fname, title_regex))


def aec_monthly(fname, title_regex):
    years, rows = aec_rows(fname, title_regex)
    out, total = {}, None
    for label, nums in rows:
        head = label.split(' ')[0] if label else ''
        if head in MES_FULL or head == 'Total':
            assert len(nums) == 5, (fname, label, nums)
            vals = [int(n) for n in nums]
            if head == 'Total':
                total = vals
            else:
                out[MES_FULL.index(head) + 1] = vals
    assert len(out) == 12 and total, (fname, out.keys())
    return years, out, total


def aec_ingresos(fname, title_regex):
    years, rows = aec_rows(fname, title_regex, max_rows=6)
    out = {}
    for label, nums in rows:
        if len(nums) == 5:
            out[label] = [float(n.replace(',', '.')) for n in nums]
    return years, out


def entities_revenue():
    """ONEI 'Turismo. Indicadores seleccionados' (enero-diciembre): Ingresos (Entidades Turísticas), turismo internacional, MCUP."""
    files = {'turismo_ind_dic2023.pdf': 'https://www.onei.gob.cu/sites/default/files/publicaciones/2024-03/turismo-indicadores-seleccionados-diciembre-2023.pdf',
             'turismo_trim_dic2024.pdf': 'https://www.onei.gob.cu/sites/default/files/publicaciones/2025-03/turismo-trimestral-diciembre-2024.pdf',
             'turismo_trim_dic2025.pdf': 'https://www.onei.gob.cu/sites/default/files/publicaciones/2026-03/turismo-trimestral-diciembre-2025_0.pdf'}
    res = {}
    for f in files:
        t = aec_text(f)
        m = re.search(r'Principales indicadores del turismo internacional[^\n]*\n(?:.*\n){0,3}?INDICADORES UM (20\d\d) (20\d\d)[^\n]*\n(?:.*\n){0,4}?Ingresos \(Entidades Tur[ií]sticas\) MCUP ([\d ]+,\d) ([\d ]+,\d)', t)
        assert m, f
        y0, y1 = int(m.group(1)), int(m.group(2))
        v0 = float(m.group(3).replace(' ', '').replace(',', '.'))
        v1 = float(m.group(4).replace(' ', '').replace(',', '.'))
        res.setdefault(y0, v0)  # previous-year figure (only if not in its own year's report)
        res[y1] = v1  # own-year figure overrides
    return res, files


NOTES = [
    'Concepto: "visitantes internacionales" (no residentes que visitan Cuba por menos de un año, cualquier motivo salvo actividad remunerada), según registros migratorios (DIIE/MININT); incluye la "Comunidad cubana en el exterior". El Anuario muestra que casi todos pernoctan (turistas 2024: 2 202 540 de 2 203 117 visitantes), pero la serie mensual solo se publica para visitantes, por eso la clave es arrivals_visitors_total.',
    'Fuentes mensuales: 2019 del Anuario Estadístico de Cuba 2023 (tabla 15.5); 2020-2024 del Anuario 2024 (tabla 15.4, cifras revisadas: p.ej. 2020 = 1 085 920 vs 1 084 728 en ediciones previas); 2025-2026 de los PDF mensuales "Arribo de viajeros", donde los valores del año en curso figuran como etiquetas de datos oficiales del gráfico de barras; se validó suma de meses = acumulado oficial y coincidencia con el Anuario en 2023-2024.',
    '2026 es preliminar (último informe: julio 2026; agosto 2026 aún no publicado al 2026-09-17). El informe de cierre de diciembre ajusta el año.',
    'Gasto: "Ingresos por turismo internacional" (Anuario, tabla 15.14/15.15) en millones de USD al tipo de cambio oficial vigente cada año (distorsionado tras la unificación monetaria de 2021, tasa oficial 1:24 y luego 1:120 desde dic-2022); excluye transporte internacional (12,5-41,8 MUSD). Incluye sector privado. Anual, rezago ~8 meses (Anuario 2024 publicado ago-2025).',
    'Gasto alterno más reciente: "Ingresos (Entidades Turísticas)" del turismo internacional en "Turismo. Indicadores seleccionados" (trimestral acumulado), en millones de CUP corrientes; no comparable en el tiempo por cambios cambiarios (2022→2023 +515%). Solo entidades turísticas.',
    'ONEI no publica gasto medio por visitante ni estadía media para visitantes internacionales (solo pernoctaciones en establecimientos de alojamiento); no se derivaron.',
    'Acceso: sitio ONEI con certificado TLS vencido, host www con error de proxy/página JS intermedia, y cortes prolongados (servidor inalcanzable ~30 min el 2026-09-17). Usar https://onei.gob.cu (sin www) con reintentos. PDF del Anuario 2024 con fuentes subset (texto (cid:N) → chr(N+29)). También existen XLS por tabla del Anuario en /turismo-0 (solo edición 2022 enlazada).',
]


def build(series, extra_notes=()):
    out = {'id': 'CU', 'name': 'Cuba', 'type': 'country', 'lat': 23.11, 'lon': -82.37,
           'series': series, 'notes': NOTES + list(extra_notes)}
    with open(os.path.join(WD, 'CU.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)


if __name__ == '__main__':
    series, used = main()
    pdf_data = {s[0]: s[1] for s in series[0]['data']}
    merged = {}
    # AEC 2023 -> 2019 ; AEC 2024 -> 2020-2024
    for fname, keep in (('aec2023_15_turismo.pdf', {2019}), ('aec2024_15_turismo.pdf', {2020, 2021, 2022, 2023, 2024})):
        years, months, total = aec_monthly(fname, r'15\.\d+ - (?:Llegada de )?Visitantes por meses')
        for ci, y in enumerate(years):
            col = [months[m][ci] for m in range(1, 13)]
            assert sum(col) == total[ci], (fname, y, sum(col), total[ci])
            if y in keep:
                for m in range(1, 13):
                    merged[f'{y}-{m:02d}'] = col[m - 1]
    diffs = [(k, merged[k], pdf_data[k]) for k in merged if k in pdf_data and merged[k] != pdf_data[k]]
    print('AEC vs PDF-label diffs:', diffs)
    for k, v in pdf_data.items():
        merged.setdefault(k, v)
    keys = sorted(merged)
    series[0]['data'] = [[k, merged[k]] for k in keys]
    series[0]['source']['title'] += ' + Anuario Estadístico de Cuba 2023/2024, cap. 15 Turismo (Visitantes por meses) para 2019-2024'
    # spending: ingresos por turismo internacional (USD)
    ing = {}
    for fname, keep in (('aec2023_15_turismo.pdf', {2019}), ('aec2024_15_turismo.pdf', {2020, 2021, 2022, 2023, 2024})):
        years, rows = aec_ingresos(fname, r'15\.\d+ - Ingresos asociados al turismo internacional')
        key = [k for k in rows if k.startswith('Ingresos por turismo internacional')][0]
        for ci, y in enumerate(years):
            if y in keep:
                ing[y] = rows[key][ci]
    print('ingresos USD', ing)
    series.append({'key': 'spending_tourism_receipts', 'category': 'spending',
                   'label': 'Ingresos por turismo internacional (excluye transporte internacional)',
                   'unit': 'US$ millones (tipo de cambio oficial de cada año)', 'frequency': 'annual',
                   'data': [[str(y), ing[y]] for y in sorted(ing)],
                   'source': {'org': 'Oficina Nacional de Estadística e Información (ONEI)',
                              'title': 'Anuario Estadístico de Cuba 2024 (ed. 2025), cap. 15 Turismo, tabla 15.14 Ingresos asociados al turismo internacional (2019 de la ed. 2024)',
                              'page_url': 'https://www.onei.gob.cu/anuario-estadistico-de-cuba-2024',
                              'file_url': AEC['aec2024_15_turismo.pdf'], 'format': 'pdf', 'update_frequency': 'anual',
                              'release_lag': '~8 meses tras cierre del año', 'last_period': str(max(ing)), 'retrieved': RETRIEVED,
                              'access_method': 'Descargar PDF del capítulo 15 desde la página del Anuario; decodificar texto (cid:N)→chr(N+29) y regex sobre la tabla "Ingresos asociados al turismo internacional" (5 años por edición).'}})
    rev, rev_files = entities_revenue()
    print('entities revenue MCUP', rev)
    series.append({'key': 'spending_intl_tourism_entities_revenue', 'category': 'spending',
                   'label': 'Ingresos de entidades turísticas por turismo internacional',
                   'unit': 'CUP millones (pesos cubanos corrientes)', 'frequency': 'annual',
                   'data': [[str(y), round(rev[y] / 1000, 1)] for y in sorted(rev)],
                   'source': {'org': 'Oficina Nacional de Estadística e Información (ONEI)',
                              'title': 'Turismo. Indicadores seleccionados, enero-diciembre (tabla 4. Principales indicadores del turismo internacional)',
                              'page_url': 'https://www.onei.gob.cu/turismo-indicadores-seleccionados-enero-diciembre-2025',
                              'file_url': rev_files['turismo_trim_dic2025.pdf'], 'format': 'pdf',
                              'update_frequency': 'trimestral (acumulado ene-mar, ene-jun, ene-sep, ene-dic)',
                              'release_lag': '~2-3 meses tras cierre del trimestre', 'last_period': str(max(rev)), 'retrieved': RETRIEVED,
                              'access_method': 'PDF de texto con partes en (cid:N)→chr(N+29); regex fila "Ingresos (Entidades Turísticas) MCUP". Cargado solo el cierre anual (ene-dic); los acumulados trimestrales 2026 no se descargaron.'}})
    build(series)
    for s in series:
        print(s['key'], s['data'][0], s['data'][-1], len(s['data']))
