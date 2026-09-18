"""Merge data/<ID>.json + config.json + basemap into dist/index.html (template.html)."""
import json, os, sys, datetime, copy
import project as projection
import signals as data_signals
ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, '..', 'data')
cfg = json.load(open(os.path.join(ROOT, 'config.json')))
basemap = json.load(open(os.path.join(ROOT, 'basemap.json')))

dests = []
for did in cfg['order']:
    path = os.path.join(DATA, f'{did}.json')
    if not os.path.exists(path):
        print('skip (no file):', did); continue
    d = json.load(open(path))
    h = cfg['headline'].get(did, {})
    keys = {s['key'] for s in d['series']}
    for role in ('arrivals', 'spending'):
        if h.get(role) and h[role] not in keys:
            print(f'WARN {did}: headline {role} "{h[role]}" not found; keys={sorted(keys)}'); h[role] = None
    # fixed-peg conversions to US$
    for s in d['series']:
        fx = h.get('fx', {}).get(s['key'])
        if fx:
            factor, peg = fx
            s['unit_local'] = s['unit']
            s['unit'] = 'US$ millones' if 'millones' in s['unit'] else 'US$'
            s['fx_note'] = f'Convertido a US$ con paridad fija ({peg}).'
            s['data'] = [[p, round(v * factor, 3) if v is not None else None] for p, v in s['data']]
        s['data'] = [[p, v] for p, v in s['data'] if v is not None]
    for extra in ('air',):
        ep = os.path.join(DATA, extra, f'{did}.json')
        if os.path.exists(ep):
            have = {x['key'] for x in d['series']}
            add = [x for x in json.load(open(ep))['series'] if x['key'] not in have]
            d['series'] += add
            for n in json.load(open(ep)).get('notes', []):
                d['notes'].append(n)
            print(f'  {did}: +{len(add)} series de {extra}')
    pp = os.path.join(DATA, 'poles', f'{did}.json')
    if os.path.exists(pp):
        pj = json.load(open(pp))
        d['poles'] = pj['poles']
        d['pole_notes'] = pj.get('notes', [])
        print(f'  {did}: {len(d["poles"])} polos turísticos')
    mp = os.path.join(DATA, 'markets', f'{did}.json')
    if os.path.exists(mp):
        d['markets'] = json.load(open(mp))['markets']
        print(f'  {did}: mercados de origen ({len(d["markets"]["data"])} mercados)')
    pcfg = cfg.get('project', {}).get(did)
    if pcfg:
        pr = projection.build_projection(d, pcfg, d.get('markets'))
        if pr and not pr.get('skipped'):
            pr['sim_h1'] = projection.simulate_h1(d, pcfg)
            d['projection'] = pr
            e = pr['backtest'].get('mape_h1')
            err = 'n/d' if e is None else f'{e * 100:.1f}%'
            print(f'  {did}: proyeccion {pr["last_actual"]} -> {pr["points"][-1][0]}'
                  f' ({len(pr["points"])} meses, error h1 {err})')
        else:
            print(f'  {did}: sin proyeccion ({pr.get("skipped") if pr else "n/d"})')
    d['short'] = cfg['short'].get(did, d['name'])
    d['focus'] = did in cfg['focus']
    d['headline'] = h
    dests.append(d)

# ---- registro histórico de proyecciones ------------------------------------
# Cada corrida guarda lo que el dashboard proyectó. Sólo se agrega un registro
# cuando la proyección cambia (llegó data nueva); si en el mismo día se vuelve a
# construir, se reemplaza el registro de ese día. Así el archivo no se llena de
# duplicados en las semanas sin publicaciones nuevas.
HIST_PATH = os.path.join(DATA, 'projections', 'history.json')
history = json.load(open(HIST_PATH)) if os.path.exists(HIST_PATH) else {}
today = datetime.date.today().isoformat()
for d in dests:
    pr = d.get('projection')
    if not pr:
        continue
    rec = {'made_on': today, 'last_actual': pr['last_actual'], 'series_key': pr['series_key'],
           'points': [p[:5] for p in pr['points']], 'year_close': pr.get('year_close')}
    recs = history.setdefault(d['id'], [])
    same = recs and recs[-1]['last_actual'] == rec['last_actual'] and recs[-1]['points'] == rec['points']
    if same:
        pass
    elif recs and recs[-1]['made_on'] == today:
        recs[-1] = rec
    else:
        recs.append(rec)
    pr['history'] = recs
os.makedirs(os.path.dirname(HIST_PATH), exist_ok=True)
with open(HIST_PATH, 'w') as f:
    json.dump(history, f, ensure_ascii=False, indent=1)
print(f'  registro de proyecciones: {sum(len(v) for v in history.values())} registros en {len(history)} destinos')

# ---- tendencias y riesgos ---------------------------------------------------
VALID_IDS = {d['id'] for d in dests} | {'ALL'}
REQ = ('id', 'title', 'summary', 'category', 'direction', 'destinations', 'horizon', 'impact',
       'status', 'first_seen', 'last_confirmed', 'evidence')
ENUMS = {'category': {'macro', 'geopolitica', 'clima', 'salud', 'aviacion', 'regulacion',
                      'mercado_emisor', 'competencia', 'seguridad'},
         'direction': {'positivo', 'negativo', 'incierto'},
         'horizon': {'inmediato', 'corto', 'estructural'},
         'impact': {'alto', 'medio', 'bajo'},
         'status': {'nuevo', 'vigente', 'escalando', 'perdiendo_fuerza', 'cerrado'}}
REG_PATH = os.path.join(DATA, 'trends', 'registry.json')
trends = {'updated': None, 'items': [], 'rejected': []}
if os.path.exists(REG_PATH):
    reg = json.load(open(REG_PATH))
    trends['updated'] = reg.get('updated')
    today_d = datetime.date.today()
    for it in reg.get('items', []):
        problems = [f'falta {k}' for k in REQ if not it.get(k)]
        problems += [f'{k} inválido: {it.get(k)}' for k, ok in ENUMS.items() if it.get(k) and it[k] not in ok]
        problems += [f'destino desconocido: {x}' for x in it.get('destinations', []) if x not in VALID_IDS]
        ev_ok = [e for e in it.get('evidence', []) if e.get('url', '').startswith('http') and e.get('date')
                 and e.get('tier') in ('oficial', 'institucional', 'prensa')]
        if not ev_ok:
            problems.append('sin evidencia válida (url, fecha y nivel)')
        if problems:
            trends['rejected'].append({'id': it.get('id'), 'problems': problems})
            print(f'  tendencia descartada {it.get("id")}: {"; ".join(problems)}')
            continue
        it = dict(it)
        it['evidence'] = ev_ok
        # cierre automático: 8 semanas sin reconfirmar
        try:
            stale = (today_d - datetime.date.fromisoformat(it['last_confirmed'])).days > 56
        except ValueError:
            stale = False
        if it['status'] != 'cerrado' and stale:
            it['status'] = 'cerrado'
            it['closed_on'] = it.get('closed_on') or today_d.isoformat()
            it['auto_closed'] = True
        trends['items'].append(it)
trends['signals'] = data_signals.compute_signals(dests)
print(f'  tendencias: {sum(1 for i in trends["items"] if i["status"] != "cerrado")} activas, '
      f'{len(trends["rejected"])} descartadas, {len(trends["signals"])} señales en los datos')

payload = {
    'generated_at': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
    'destinations': dests,
    'basemap': basemap,
    'share': {'universe': cfg.get('share_universe', []), 'excluded': cfg.get('share_excluded', {})},
    'basemap_do': json.load(open(os.path.join(ROOT, 'basemap_do.json'))),
    'trends': trends,
}
tpl = open(os.path.join(ROOT, 'template.html')).read()
js = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')
out = tpl.replace('/*__DATA__*/null', js)
os.makedirs(os.path.join(ROOT, 'dist'), exist_ok=True)
open(os.path.join(ROOT, 'dist', 'index.html'), 'w').write(out)
print('built', len(dests), 'destinations,', round(len(out)/1024), 'KB')
