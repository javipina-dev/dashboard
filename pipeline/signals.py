"""Señales en los datos: patrones detectados con reglas fijas sobre las series oficiales.

No hay juicio ni fuentes externas: cada señal dice qué regla la disparó y con qué valores,
para que cualquiera pueda verificarla contra la serie. Las reglas:

R1 · Llegadas: variación interanual de ±10% o más, del mismo signo, en los 3 últimos meses.
R2 · Mercados de origen: la cuota de un mercado cambia 1 punto porcentual o más en 3 años
     (ventanas de 12 meses).
R3 · Capacidad aérea desde EE.UU.: asientos ±10% o más interanual en los 3 últimos meses.
     Es un indicador adelantado: suele anticipar las llegadas.
R4 · Ocupación hotelera: el promedio de los 3 últimos meses cambia 5 puntos o más contra los
     mismos meses del año anterior.
"""

MES = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic']


def _idx(p):
    y, m = p.split('-')
    return int(y) * 12 + int(m) - 1


def _lab(i):
    return f'{MES[i % 12]} {i // 12}'


def _monthly(series):
    return {_idx(p): v for p, v in series['data'] if len(p) == 7 and p[4] == '-' and p[5:].isdigit()}


def _pct(x):
    return f'{"+" if x > 0 else "−" if x < 0 else ""}{abs(x) * 100:.1f}%'


def _yoy_run(m, n=3):
    if not m:
        return None
    last = max(m)
    out = []
    for i in range(last - n + 1, last + 1):
        if i not in m or (i - 12) not in m or not m[i - 12]:
            return None
        out.append((i, m[i] / m[i - 12] - 1))
    return out


def _r1_arrivals(d, series, threshold=0.10):
    run = _yoy_run(_monthly(series))
    if not run:
        return None
    vals = [v for _, v in run]
    if all(v >= threshold for v in vals) or all(v <= -threshold for v in vals):
        up = vals[0] > 0
        last_i, last_v = run[-1]
        return {
            'rule': 'R1', 'dest': d['id'], 'series_key': series['key'],
            'direction': 'positivo' if up else 'negativo',
            'title': f'{d["short"]}: llegadas {"arriba" if up else "abajo"} más de 10% interanual tres meses seguidos',
            'detail': f'{series["label"]}. Variación interanual: ' +
                      ', '.join(f'{_lab(i)} {_pct(v)}' for i, v in run) + '.',
            'period': _lab(last_i), 'magnitude': abs(sum(vals) / len(vals)),
        }
    return None


def _r2_markets(d, threshold_pp=1.0):
    mk = d.get('markets')
    if not mk:
        return []
    ttm = {}
    for name, pairs in mk['data'].items():
        m = {_idx(p): v for p, v in pairs if len(p) == 7}
        if not m:
            continue
        last = max(m)
        def window(end):
            if all((end - k) in m for k in range(12)):
                return sum(m[end - k] for k in range(12))
            return None
        ttm[name] = (window(last), window(last - 36), last)
    if not ttm:
        return []
    now_tot = sum(v[0] for v in ttm.values() if v[0])
    old_tot = sum(v[1] for v in ttm.values() if v[1])
    if not now_tot or not old_tot:
        return []
    out = []
    for name, (now, old, last) in ttm.items():
        if name == 'Otros' or not now or not old:
            continue
        s_now, s_old = now / now_tot * 100, old / old_tot * 100
        delta = s_now - s_old
        if abs(delta) >= threshold_pp:
            out.append({
                'rule': 'R2', 'dest': d['id'], 'series_key': None,
                'direction': 'positivo' if delta > 0 else 'negativo',
                'title': f'{d["short"]}: {name} {"gana" if delta > 0 else "pierde"} {abs(delta):.1f} puntos de cuota en tres años',
                'detail': f'Cuota de las llegadas de los últimos 12 meses: {s_old:.1f}% hace tres años, '
                          f'{s_now:.1f}% a {_lab(last)}. Crecimiento de ese mercado en el período: {_pct(now / old - 1)}.',
                'period': _lab(last), 'magnitude': abs(delta) / 100,
                'market': name,
            })
    return out


def _r3_seats(d, threshold=0.10):
    s = next((x for x in d['series'] if x['key'] == 'air_us_seats'), None)
    if not s:
        return None
    run = _yoy_run(_monthly(s))
    if not run:
        return None
    vals = [v for _, v in run]
    if all(v >= threshold for v in vals) or all(v <= -threshold for v in vals):
        up = vals[0] > 0
        return {
            'rule': 'R3', 'dest': d['id'], 'series_key': 'air_us_seats',
            'direction': 'positivo' if up else 'negativo',
            'title': f'{d["short"]}: asientos desde EE.UU. {"arriba" if up else "abajo"} más de 10% interanual',
            'detail': 'Capacidad aérea ofrecida desde Estados Unidos (US DOT). Variación interanual: ' +
                      ', '.join(f'{_lab(i)} {_pct(v)}' for i, v in run) +
                      '. La capacidad suele anticipar las llegadas de los meses siguientes.',
            'period': _lab(run[-1][0]), 'magnitude': abs(sum(vals) / len(vals)),
        }
    return None


def _r4_occupancy(owner_id, owner_name, series, threshold_pp=5.0):
    m = _monthly(series)
    if not m:
        return None
    last = max(m)
    cur = [m.get(last - k) for k in range(3)]
    prev = [m.get(last - 12 - k) for k in range(3)]
    if any(v is None for v in cur + prev):
        return None
    delta = sum(cur) / 3 - sum(prev) / 3
    if abs(delta) < threshold_pp:
        return None
    return {
        'rule': 'R4', 'dest': owner_id, 'series_key': series['key'],
        'direction': 'positivo' if delta > 0 else 'negativo',
        'title': f'{owner_name}: ocupación hotelera {"+" if delta > 0 else "−"}{abs(delta):.1f} puntos contra el año anterior',
        'detail': f'Promedio de los 3 meses a {_lab(last)}: {sum(cur) / 3:.1f}%, contra {sum(prev) / 3:.1f}% en los mismos '
                  f'meses del año anterior.',
        'period': _lab(last), 'magnitude': abs(delta) / 100,
    }


def compute_signals(dests, max_signals=14):
    out = []
    for d in dests:
        h = d.get('headline', {})
        key = h.get('arrivals') or h.get('arrivals_proxy')
        s = next((x for x in d['series'] if x['key'] == key), None) if key else None
        if s:
            r = _r1_arrivals(d, s)
            if r:
                if not h.get('arrivals') and h.get('arrivals_proxy'):
                    r['detail'] += ' (serie sustituta: ' + (h.get('arrivals_proxy_note') or 'proxy') + ')'
                out.append(r)
        out.extend(_r2_markets(d))
        r = _r3_seats(d)
        if r:
            out.append(r)
        occ = next((x for x in d['series'] if x['key'] == 'hotel_occupancy_rate'), None)
        if occ:
            r = _r4_occupancy(d['id'], d['short'], occ)
            if r:
                out.append(r)
        for pole in d.get('poles', []):
            po = next((x for x in pole['series'] if x['key'] == 'hotel_occupancy_rate'), None)
            if po:
                r = _r4_occupancy(d['id'], pole['name'], po)
                if r:
                    r['pole'] = pole['id']
                    out.append(r)
    out.sort(key=lambda x: -x['magnitude'])
    for i, x in enumerate(out):
        x['id'] = f'{x["rule"].lower()}-{x["dest"].lower()}-{i}'
    return out[:max_signals]
