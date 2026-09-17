"""Proyecciones de llegadas mensuales para los destinos foco.

Dos métodos, ambos deterministas y auditables:

1. NOWCAST (1-2 meses): usa un indicador adelantado oficial que se publica antes
   que la serie objetivo (p. ej. las operaciones de la Junta de Aviación Civil de
   RD salen ~4 semanas antes que las llegadas del Banco Central). Se aplica la
   razón media de los últimos 12 meses solapados.

2. PROYECCIÓN ESTACIONAL (hasta 12 meses): participación media de cada mes dentro
   de su año, calculada con los últimos 3 años completos, aplicada sobre el nivel
   de los últimos 12 meses con el crecimiento reciente amortiguado a la mitad.

Las bandas NO son teóricas: salen de correr el método hacia atrás (origen móvil,
24 orígenes) y tomar los percentiles 10 y 90 de los errores relativos reales por
horizonte. El backtest se guarda junto a la proyección para mostrarlo en la página.

Los meses de la pandemia (2020-03 a 2021-06) se excluyen del cálculo estacional.
Ningún valor proyectado se mezcla con los datos publicados: van en su propio
bloque, con su tipo (nowcast/estacional) y su período.
"""
import statistics

PANDEMIC = set()
for _y, _m0, _m1 in ((2020, 3, 12), (2021, 1, 6)):
    for _m in range(_m0, _m1 + 1):
        PANDEMIC.add(_y * 12 + _m - 1)

HORIZON = 12
MIN_MONTHS = 48
GROWTH_DAMPING = 0.5
GROWTH_CAP = 0.15
BACKTEST_ORIGINS = 24


BIAS_CAP = 0.10


def _calibrate(raw, band):
    """Corrige el sesgo sistemático del método y devuelve (p50, p10, p90) ordenados.

    `band` trae los percentiles de los errores relativos reales del backtest
    (error = proyectado/real - 1). Dividir por (1+error) traduce cada percentil
    del error en un valor de la serie. La corrección del sesgo se limita a ±10%
    para que un backtest con pocos casos no distorsione la proyección.
    """
    if not band:
        return round(raw), None, None
    bias = max(-BIAS_CAP, min(BIAS_CAP, band.get('p50') or 0.0))
    p50 = raw / (1 + bias)
    vals = [raw / (1 + band[k]) for k in ('p10', 'p90') if band.get(k) is not None]
    if not vals:
        return round(p50), None, None
    lo, hi = min(vals + [p50]), max(vals + [p50])
    return round(p50), round(lo), round(hi)


def _idx(period):
    y, m = period.split('-')
    return int(y) * 12 + int(m) - 1


def _period(idx):
    return f'{idx // 12:04d}-{idx % 12 + 1:02d}'


def _series_map(series):
    return {_idx(p): v for p, v in series['data'] if len(p) == 7 and '-Q' not in p and '-H' not in p}


def _full_years(m, upto):
    """Años con 12 meses presentes y sin meses de pandemia, más recientes primero."""
    out = []
    for y in range(upto // 12, 2018, -1):
        idxs = [y * 12 + k for k in range(12)]
        if all(i in m and i <= upto for i in idxs) and not any(i in PANDEMIC for i in idxs):
            out.append(y)
    return out


def _seasonal_shares(m, upto, years=3):
    ys = _full_years(m, upto)[:years]
    if len(ys) < 2:
        return None, []
    shares = []
    for y in ys:
        tot = sum(m[y * 12 + k] for k in range(12))
        if tot <= 0:
            continue
        shares.append([m[y * 12 + k] / tot for k in range(12)])
    if not shares:
        return None, []
    avg = [statistics.fmean(s[k] for s in shares) for k in range(12)]
    ssum = sum(avg)
    return [a / ssum for a in avg], ys


def _ttm(m, upto):
    idxs = [upto - k for k in range(12)]
    if not all(i in m for i in idxs):
        return None
    return sum(m[i] for i in idxs)


def _seasonal_forecast(m, upto, horizon=HORIZON):
    """Devuelve {idx: valor} proyectado para los `horizon` meses siguientes a `upto`."""
    shares, years = _seasonal_shares(m, upto)
    ttm = _ttm(m, upto)
    if not shares or not ttm:
        return {}, None
    prev = _ttm(m, upto - 12)
    growth = 0.0
    if prev:
        growth = max(-GROWTH_CAP, min(GROWTH_CAP, (ttm / prev - 1) * GROWTH_DAMPING))
    out = {}
    for h in range(1, horizon + 1):
        i = upto + h
        # nivel anual esperado, interpolando el crecimiento a lo largo del horizonte
        level = ttm * (1 + growth * h / 12)
        out[i] = shares[i % 12] * level
    return out, {'growth_damped': round(growth, 4), 'years_used': years,
                 'ttm': round(ttm), 'prev_ttm': round(prev) if prev else None}


def _ratio_nowcast(target, lead, upto_target, upto_lead, window=12):
    """Razón media objetivo/adelantado en los últimos `window` meses solapados."""
    ratios = []
    for k in range(window):
        i = upto_target - k
        if i in target and i in lead and lead[i]:
            ratios.append(target[i] / lead[i])
    if len(ratios) < 6:
        return {}, None
    r = statistics.fmean(ratios)
    out = {}
    for i in range(upto_target + 1, upto_lead + 1):
        if i in lead:
            out[i] = lead[i] * r
    return out, {'ratio': round(r, 4), 'ratio_window': len(ratios),
                 'ratio_cv': round(statistics.pstdev(ratios) / r, 4) if r else None}


def _backtest(m, last, lead=None, lead_last=None):
    """Errores relativos reales por horizonte, con origen móvil."""
    errs = {h: [] for h in range(1, HORIZON + 1)}
    nowcast_errs = []
    origins = 0
    for o in range(last - BACKTEST_ORIGINS, last):
        if o - MIN_MONTHS + 1 < min(m):
            continue
        hist = {i: v for i, v in m.items() if i <= o}
        if len(hist) < MIN_MONTHS:
            continue
        fc, _ = _seasonal_forecast(hist, o)
        if not fc:
            continue
        origins += 1
        for h in range(1, HORIZON + 1):
            i = o + h
            if i in m and m[i] and i in fc and i not in PANDEMIC:
                errs[h].append(fc[i] / m[i] - 1)
        if lead:
            nc, _ = _ratio_nowcast(hist, lead, o, o + 1)
            i = o + 1
            if nc.get(i) and i in m and m[i] and i not in PANDEMIC:
                nowcast_errs.append(nc[i] / m[i] - 1)
    def q(vals, p):
        if not vals:
            return None
        s = sorted(vals)
        k = (len(s) - 1) * p
        lo, hi = int(k), min(int(k) + 1, len(s) - 1)
        return s[lo] + (s[hi] - s[lo]) * (k - lo)
    bands = {}
    for h, vals in errs.items():
        if len(vals) >= 6:
            bands[h] = {'p10': q(vals, 0.1), 'p50': q(vals, 0.5), 'p90': q(vals, 0.9),
                        'mape': statistics.fmean(abs(v) for v in vals), 'n': len(vals)}
    bt = {
        'origins': origins,
        'seasonal_bands': bands,
        'mape_h1': bands.get(1, {}).get('mape'),
        'mape_h1_3': (statistics.fmean([abs(v) for h in (1, 2, 3) for v in errs[h]])
                      if all(errs[h] for h in (1, 2, 3)) else None),
        'mape_h12': bands.get(12, {}).get('mape'),
    }
    if nowcast_errs:
        bt['nowcast'] = {'mape': statistics.fmean(abs(v) for v in nowcast_errs),
                         'p10': q(nowcast_errs, 0.1), 'p50': q(nowcast_errs, 0.5),
                         'p90': q(nowcast_errs, 0.9), 'n': len(nowcast_errs)}
    return bt


def build_projection(dest, cfg_project, markets=None):
    """Devuelve el bloque de proyección de un destino, o None si no es proyectable."""
    by_key = {s['key']: s for s in dest['series']}
    target = by_key.get(cfg_project['series'])
    if not target:
        return {'skipped': f'no existe la serie {cfg_project["series"]}'}
    m = _series_map(target)
    last = max(m) if m else None

    lead_key = cfg_project.get('lead')
    lead_series = by_key.get(lead_key) if lead_key else None
    lead = _series_map(lead_series) if lead_series else None
    lead_last = max(lead) if lead else None

    if len(m) < MIN_MONTHS:
        # Historia corta: se proyecta el indicador adelantado (serie larga) y se
        # convierte a la serie objetivo con la razón media de los meses solapados.
        if not lead or len(lead) < MIN_MONTHS:
            return {'skipped': f'sólo {len(m)} meses de historia; se requieren {MIN_MONTHS}'}
        return _chained_projection(target, m, last, lead_series, lead, lead_last, cfg_project, by_key, markets)

    points, kinds = {}, {}
    nowcast_meta = None
    if lead and lead_last and lead_last > last:
        nc, nowcast_meta = _ratio_nowcast(m, lead, last, lead_last)
        for i, v in nc.items():
            points[i], kinds[i] = v, 'nowcast'

    anchor = max(points) if points else last
    # el nowcast se toma como dato de arranque para continuar la estacional
    m_ext = dict(m)
    m_ext.update({i: v for i, v in points.items()})
    fc, seasonal_meta = _seasonal_forecast(m_ext, anchor, HORIZON - (anchor - last))
    for i, v in fc.items():
        points.setdefault(i, v)
        kinds.setdefault(i, 'seasonal')
    if not points:
        return {'skipped': 'no se pudo calcular la estacionalidad'}

    bt = _backtest(m, last, lead, lead_last)

    rows = []
    for i in sorted(points):
        kind = kinds[i]
        h = i - last
        band = bt.get('nowcast') if (kind == 'nowcast' and bt.get('nowcast')) else bt['seasonal_bands'].get(h)
        p50, lo, hi = _calibrate(points[i], band)
        rows.append([_period(i), p50, lo, hi, kind])

    # cierre del año en curso: real acumulado + proyectado de los meses que faltan
    year = last // 12
    ytd = sum(v for i, v in m.items() if i // 12 == year and i <= last)
    rest = [r for r in rows if int(r[0][:4]) == year]
    close = None
    if rest:
        close = {'year': year, 'actual_months': last % 12 + 1, 'actual_ytd': round(ytd),
                 'p50': round(ytd + sum(r[1] for r in rest)),
                 'p10': round(ytd + sum(r[2] for r in rest if r[2] is not None)) if all(r[2] is not None for r in rest) else None,
                 'p90': round(ytd + sum(r[3] for r in rest if r[3] is not None)) if all(r[3] is not None for r in rest) else None}
        prev_year_total = sum(v for i, v in m.items() if i // 12 == year - 1)
        if prev_year_total:
            close['prev_year'] = round(prev_year_total)
            close['yoy'] = round(close['p50'] / prev_year_total - 1, 4)

    # peso del mercado de EE.UU., para la palanca de escenarios
    us = None
    if markets and 'Estados Unidos' in markets.get('data', {}):
        us_map = {_idx(p): v for p, v in markets['data']['Estados Unidos'] if len(p) == 7}
        tot = _ttm(m, last)
        us_ttm = _ttm(us_map, last) if all(last - k in us_map for k in range(12)) else None
        if tot and us_ttm:
            us = {'share': round(us_ttm / tot, 4), 'source': 'mercados de origen oficiales'}
    elif cfg_project.get('us_share_from'):
        alt = _series_map(by_key[cfg_project['us_share_from']]) if cfg_project['us_share_from'] in by_key else None
        if alt:
            k = [(m[i] and alt[i] / m[i]) for i in [last - j for j in range(12)] if i in alt and i in m]
            if len(k) >= 6:
                us = {'share': round(min(1.0, statistics.fmean(k)), 4),
                      'source': 'calculado: pasajeros desde EE.UU. sobre la serie principal'}
    elif cfg_project.get('us_share_fixed') is not None:
        us = {'share': cfg_project['us_share_fixed'], 'source': cfg_project.get('us_share_note', '')}

    return {
        'series_key': target['key'],
        'series_label': target['label'],
        'unit': target['unit'],
        'last_actual': _period(last),
        'points': rows,
        'backtest': bt,
        'year_close': close,
        'us_market': us,
        'method': {
            'nowcast': nowcast_meta,
            'nowcast_lead_label': lead_series['label'] if (lead_series and nowcast_meta) else None,
            'seasonal': seasonal_meta,
            'horizon': HORIZON,
            'growth_damping': GROWTH_DAMPING,
        },
    }

def _chained_projection(target, m, last, lead_series, lead, lead_last, cfg_project, by_key, markets):
    """Proyección encadenada: se proyecta la serie larga y se traduce con la razón."""
    ratios = [m[i] / lead[i] for i in [last - k for k in range(12)] if i in m and i in lead and lead[i]]
    if len(ratios) < 6:
        return {'skipped': 'no hay suficientes meses solapados para encadenar la proyección'}
    r = statistics.fmean(ratios)
    cv = statistics.pstdev(ratios) / r if r else None

    fc_lead, seasonal_meta = _seasonal_forecast(lead, lead_last, HORIZON)
    if not fc_lead:
        return {'skipped': 'no se pudo calcular la estacionalidad del indicador adelantado'}
    bt = _backtest(lead, lead_last)
    bt['chained'] = {'ratio': round(r, 4), 'ratio_cv': round(cv, 4) if cv else None,
                     'ratio_window': len(ratios), 'lead_label': lead_series['label']}

    rows = []
    for i in sorted(fc_lead):
        if i <= last:
            continue
        h = i - lead_last
        p50, lo, hi = _calibrate(fc_lead[i] * r, bt['seasonal_bands'].get(h))
        rows.append([_period(i), p50, lo, hi, 'chained'])
    if not rows:
        return {'skipped': 'la proyección encadenada no produjo meses futuros'}

    year = last // 12
    ytd = sum(v for i, v in m.items() if i // 12 == year and i <= last)
    rest = [x for x in rows if int(x[0][:4]) == year]
    close = None
    if rest:
        close = {'year': year, 'actual_months': last % 12 + 1, 'actual_ytd': round(ytd),
                 'p50': round(ytd + sum(x[1] for x in rest)),
                 'p10': round(ytd + sum(x[2] for x in rest)) if all(x[2] is not None for x in rest) else None,
                 'p90': round(ytd + sum(x[3] for x in rest)) if all(x[3] is not None for x in rest) else None}
        prev = sum(v for i, v in m.items() if i // 12 == year - 1)
        if prev and len([1 for i in m if i // 12 == year - 1]) == 12:
            close['prev_year'] = round(prev)
            close['yoy'] = round(close['p50'] / prev - 1, 4)

    us = None
    if markets and 'Estados Unidos' in markets.get('data', {}):
        us_map = {_idx(p): v for p, v in markets['data']['Estados Unidos'] if len(p) == 7}
        tot = _ttm(m, last)
        us_ttm = _ttm(us_map, last)
        if tot and us_ttm:
            us = {'share': round(us_ttm / tot, 4), 'source': 'mercados de origen oficiales'}

    return {
        'series_key': target['key'], 'series_label': target['label'], 'unit': target['unit'],
        'last_actual': _period(last), 'points': rows, 'backtest': bt, 'year_close': close,
        'us_market': us,
        'method': {'seasonal': seasonal_meta, 'horizon': HORIZON, 'growth_damping': GROWTH_DAMPING,
                   'chained_from': lead_series['label'],
                   'chained_note': 'La serie objetivo tiene historia corta, así que se proyecta el '
                                   'indicador adelantado (con historia desde 2019) y se convierte con '
                                   'la razón media de los últimos 12 meses solapados. La banda proviene '
                                   'del backtest del indicador adelantado.'},
    }
