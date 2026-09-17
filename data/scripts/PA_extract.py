"""Panamá (INEC) extraction.
Inputs (WD/raw/PA): ep_<YYYY>.xls = INEC "Principales Indicadores Económicos Mensuales <YYYY>" cuadro
"Entrada de viajeros y sus gastos" (Sección de Balanza de Pagos, INEC). Links are scraped from
https://www.inec.gob.pa/avance/Default2.aspx?ID_CATEGORIA=1&ID_CIFRAS=<id>&ID_IDIOMA=1
  ids: 2019=40, 2020=43, 2021=45, 2022=47, 2023=48, 2024=50, 2025=51, 2026=52  (file names contain 'entrada_pasajeros').
Each file has annual rows for 4 previous years, then monthly rows for previous year and current year (P).
For each year we use the most recent file that contains all published months (year Y+1 file if it has 12 months, else year Y file).
"""
import json, os, re
import pandas as pd

WD = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(WD, 'raw', 'PA')
MESES = ['Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio', 'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre']
COLS = {1: 'total_viajeros', 2: 'visitantes', 3: 'turistas', 4: 'excursionistas', 5: 'cruceros', 6: 'transito_total', 7: 'transito_tocumen', 8: 'transito_cruceros', 9: 'gasto'}
RETRIEVED = '2026-09-17'
FILE_URLS = {}


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
    parsed = {y: parse_file(y) for y in range(2019, 2027)}
    chosen = {}
    used = {}
    for y in range(2019, 2027):
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
    for y in range(2019, 2025):
        f = used[y]
        ann = None
        for yy in range(y + 1, 2027):
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
        'page_url': 'https://www.inec.gob.pa/avance/Default2.aspx?ID_CATEGORIA=1&ID_CIFRAS=52&ID_IDIOMA=1',
        'file_url': 'https://www.inec.gob.pa/archivos/A07055475202609141439162026_entrada_pasajeros.xls',
        'format': 'xlsx', # archivo .xls (Excel 97-2003); también .csv
        'update_frequency': 'mensual',
        'release_lag': '~6-7 semanas (julio 2026 publicado 15-09-2026)',
        'last_period': last_p,
        'retrieved': RETRIEVED,
        'access_method': 'Scrapear la página "Avance en cifras > Principales Indicadores Económicos Mensuales <año>" (ID_CIFRAS por año) y tomar el enlace ../archivos/*entrada_pasajeros.xls (también .csv/.pdf; nombre con timestamp cambia en cada actualización). Leer XLS (xlrd): filas de meses del año previo y del año en curso (P); cifras en miles de personas con 3 decimales.',
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
    with open(os.path.join(WD, 'PA.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    for s in series:
        print(s['key'], s['data'][0], s['data'][-1], len(s['data']))


if __name__ == '__main__':
    main()
