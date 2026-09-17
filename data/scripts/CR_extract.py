"""Costa Rica (ICT) extraction.
Inputs (WD/raw/CR):
  recientes_2026.pdf  <- https://www.ict.go.cr/en/documents/estad%C3%ADsticas/informes-estad%C3%ADsticos/recientes/2893-2025/file.html
                         (ICT monthly report "Llegadas internacionales 2026", Cuadro 11 todas las vías / Cuadro 12 vía aérea, 2019-2026 by month)
  divisas.pdf         <- https://www.ict.go.cr/es/documentos-institucionales/estad%C3%ADsticas/cifras-econ%C3%B3micas/costa-rica/3286-divisas-turismo-2025-bccr/file.html
  gmp_todas.pdf       <- .../cifras-tur%C3%ADsticas/gasto-y-estadia-media/2589-gmp-todas-vias-2015-2025/file.html
  gmp_aerea.pdf       <- .../cifras-tur%C3%ADsticas/gasto-y-estadia-media/2590-gmp-y-estadia-media-via-aerea-2006-2025/file.html
"""
import json, os, re
import pdfplumber

WD = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(WD, 'raw', 'CR')
MESES = ['Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio', 'Julio', 'Agosto', 'Setiembre', 'Octubre', 'Noviembre', 'Diciembre']
RETRIEVED = '2026-09-17'


def parse_month_year_table(page, title_regex):
    """Parse 'Mes | 2019 ... 2026' table located below a title matching title_regex on page."""
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
    pdf = pdfplumber.open(os.path.join(RAW, 'recientes_2026.pdf'))
    pg = pdf.pages[11]
    todas = parse_month_year_table(pg, r'turistas por TODAS LAS V')
    aerea = parse_month_year_table(pg, r'turistas por V[ÍI]A A[ÉE]REA')
    # validate against Ene-Dic totals printed in the table
    text = pg.extract_text()
    for name, d in (('todas', todas), ('aerea', aerea)):
        years = sorted({y for y, _ in d})
        for y in years:
            months = [m for (yy, m) in d if yy == y]
            if len(months) == 12:
                pass
        print(name, 'months:', len(d), 'first', min(d), 'last', max(d))
    # check annual totals from xlsx annual (Todas_vías sheet) values printed on page
    tot_line = re.search(r'TURISTAS POR TODAS LAS V[ÍI]AS\nMes .*\nEne - Dic ([\d ]+)\n', text).group(1)
    tot_air = re.search(r'TURISTAS V[ÍI]A A[ÉE]REA\nMes .*\nEne - Dic ([\d ]+)\n', text).group(1)
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
        'title': 'Llegadas internacionales de turistas 2026 (informe mensual acumulado) – Cuadro 11/12: llegadas por mes 2019-2026',
        'page_url': 'https://www.ict.go.cr/es/estadisticas/informes-estadisticos.html',
        'file_url': 'https://www.ict.go.cr/en/documents/estad%C3%ADsticas/informes-estad%C3%ADsticos/recientes/2893-2025/file.html',
        'format': 'pdf',
        'update_frequency': 'mensual (el mismo PDF se reemplaza cada mes; anuario y Excel de series anuales al cierre del año)',
        'release_lag': '~2-4 semanas tras cierre de mes (DGME entrega base en los primeros 10 días del mes siguiente)',
        'last_period': last_p,
        'retrieved': RETRIEVED,
        'access_method': 'Descargar PDF "Recientes 2026" desde la página Movimientos Migratorios (enlace fijo /recientes/2893-.../file.html, texto nativo); pdfplumber: página "Cuadro 11/12" (meses × años 2019-2026), números con espacio como separador de miles → asignar palabras a columnas por posición x. Excel de series (3242-series-sitio-web-ict-2025) solo trae totales anuales.',
    }
    series = [
        {'key': 'arrivals_stopover', 'category': 'arrivals', 'label': 'Llegadas internacionales de turistas (todas las vías)',
         'unit': 'personas', 'frequency': 'monthly', 'data': ser(todas), 'source': dict(arr_src)},
        {'key': 'arrivals_air_international', 'category': 'arrivals', 'label': 'Llegadas internacionales de turistas por vía aérea (SJO, LIR, Tobías Bolaños, Limón)',
         'unit': 'personas', 'frequency': 'monthly', 'data': ser(aerea), 'source': dict(arr_src)},
    ]

    # ---- Spending: BCCR divisas por turismo (balanza de pagos, viajes) ----
    dv = pdfplumber.open(os.path.join(RAW, 'divisas.pdf'))
    t = dv.pages[1].extract_text()
    rec = []
    for m in re.finditer(r'^(20\d\d)(?: 1/)? (\d{1,3}(?: \d{3})*,\d) (\d{1,3}(?: \d{3})*,\d)$', t, flags=re.M):
        y = int(m.group(1))
        if y >= 2019:
            rec.append([str(y), es_num(m.group(2))])
    assert [r[0] for r in rec] == [str(y) for y in range(2019, 2026)], rec
    series.append({'key': 'spending_tourism_receipts', 'category': 'spending',
                   'label': 'Ingreso de divisas por turismo (balanza de pagos, excluye cruceristas)',
                   'unit': 'US$ millones', 'frequency': 'annual', 'data': rec,
                   'source': {'org': 'Banco Central de Costa Rica (BCCR), publicado por ICT',
                              'title': 'Divisas por concepto de turismo 2000-2025 (Cuadro 1)',
                              'page_url': 'https://www.ict.go.cr/es/estadisticas/cifras-economicas.html',
                              'file_url': 'https://www.ict.go.cr/es/documentos-institucionales/estad%C3%ADsticas/cifras-econ%C3%B3micas/costa-rica/3286-divisas-turismo-2025-bccr/file.html',
                              'format': 'pdf', 'update_frequency': 'anual (BCCR compila trimestral en balanza de pagos)',
                              'release_lag': '~2-3 meses tras cierre del año (2025 preliminar)', 'last_period': rec[-1][0],
                              'retrieved': RETRIEVED,
                              'access_method': 'PDF de texto en ICT > Estadísticas > Cifras económicas; regex sobre Cuadro 1. Alternativa trimestral: BCCR Indicadores Económicos, balanza de pagos, cuenta Viajes (crédito).'}})

    # ---- Spending: GMP total (todas las vías) ----
    g = pdfplumber.open(os.path.join(RAW, 'gmp_todas.pdf'))
    t = g.pages[1].extract_text()
    line = re.search(r'^GMP TOTAL \(en US\$\) (.*)$', t, flags=re.M).group(1)
    vals = re.findall(r'\d{1,3}(?: \d{3})?,\d', line)
    assert len(vals) == 11, vals
    gmp = [[str(2015 + i), es_num(v)] for i, v in enumerate(vals) if 2015 + i >= 2019]
    series.append({'key': 'spending_avg_per_visitor', 'category': 'spending',
                   'label': 'Gasto medio por persona de turistas no residentes (todas las vías, por estadía)',
                   'unit': 'US$', 'frequency': 'annual', 'data': gmp,
                   'source': {'org': 'Instituto Costarricense de Turismo (ICT) – Encuestas de No Residentes aéreas y terrestres',
                              'title': 'Gasto medio por persona (GMP) en US$ de los turistas no residentes 2015-2025',
                              'page_url': 'https://www.ict.go.cr/es/estadisticas/cifras-turisticas.html',
                              'file_url': 'https://www.ict.go.cr/es/documentos-institucionales/estad%C3%ADsticas/cifras-tur%C3%ADsticas/gasto-y-estadia-media/2589-gmp-todas-vias-2015-2025/file.html',
                              'format': 'pdf', 'update_frequency': 'anual', 'release_lag': '~3-6 meses tras cierre del año',
                              'last_period': gmp[-1][0], 'retrieved': RETRIEVED,
                              'access_method': 'PDF de texto (ICT > Cifras turísticas > Gasto y estadía media); regex fila "GMP TOTAL".'}})

    # ---- Average stay (air) ----
    g2 = pdfplumber.open(os.path.join(RAW, 'gmp_aerea.pdf'))
    t = g2.pages[1].extract_text()
    stay = []
    for m in re.finditer(r'^(20\d\d)\d? (\d{1,3}(?: \d{3})?,\d) (\d{1,2},\d)$', t, flags=re.M):
        y = int(m.group(1))
        if y >= 2019:
            stay.append([str(y), es_num(m.group(3))])
    assert [s[0] for s in stay] == [str(y) for y in range(2019, 2026)], stay
    series.append({'key': 'spending_avg_stay', 'category': 'spending',
                   'label': 'Estadía media de turistas no residentes (vía aérea)',
                   'unit': 'noches', 'frequency': 'annual', 'data': stay,
                   'source': {'org': 'Instituto Costarricense de Turismo (ICT) – Encuestas No Residentes en aeropuertos internacionales',
                              'title': 'Gasto medio por persona (GMP) y estadía media, vía aérea 2006-2025',
                              'page_url': 'https://www.ict.go.cr/es/estadisticas/cifras-turisticas.html',
                              'file_url': 'https://www.ict.go.cr/es/documentos-institucionales/estad%C3%ADsticas/cifras-tur%C3%ADsticas/gasto-y-estadia-media/2590-gmp-y-estadia-media-via-aerea-2006-2025/file.html',
                              'format': 'pdf', 'update_frequency': 'anual', 'release_lag': '~3-6 meses tras cierre del año',
                              'last_period': stay[-1][0], 'retrieved': RETRIEVED,
                              'access_method': 'PDF de texto; regex "año GMP estadía" en Cuadro 1 (años 2020-2023 llevan superíndice de nota pegado al año).'}})

    out = {'id': 'CR', 'name': 'Costa Rica', 'type': 'country', 'lat': 9.93, 'lon': -84.08, 'series': series,
           'notes': [
               'Llegadas internacionales de turistas = visitantes que pernoctan (no residentes), registradas por la DGME en puestos migratorios; no incluye cruceristas. Todas las vías = aérea + terrestre/fluvial + marítima (sin cruceros).',
               'La vía aérea incluye Juan Santamaría (SJO), Daniel Oduber (LIR, Guanacaste), Tobías Bolaños y Limón. El PDF mensual también desglosa SJO y LIR (Cuadros 13-14) y vía terrestre (solo año en curso).',
               'Los datos del año en curso (2026) son preliminares; el PDF "recientes" se sobreescribe mensualmente en la misma URL.',
               'Gasto: ingreso de divisas por turismo proviene de la balanza de pagos del BCCR (MBP6, sin cruceristas); 2025 preliminar. El GMP es por persona y por estadía completa (no por día), ponderado por vía; 2020-2023 con metodología alterada (encuestas parciales/en línea; vía terrestre estimada por BCCR).',
               'Estadía media publicada solo para vía aérea.',
               'Serie mensual ICT disponible en PDF; el Excel "series sitio web" contiene únicamente totales anuales por país.',
           ]}
    with open(os.path.join(WD, 'CR.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    for s in series:
        print(s['key'], s['data'][0], s['data'][-1], len(s['data']))


if __name__ == '__main__':
    main()
