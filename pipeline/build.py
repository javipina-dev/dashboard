"""Merge data/<ID>.json + config.json + basemap into dist/index.html (template.html)."""
import json, os, sys, datetime, copy
import project as projection
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

payload = {
    'generated_at': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
    'destinations': dests,
    'basemap': basemap,
    'share': {'universe': cfg.get('share_universe', []), 'excluded': cfg.get('share_excluded', {})},
    'basemap_do': json.load(open(os.path.join(ROOT, 'basemap_do.json'))),
}
tpl = open(os.path.join(ROOT, 'template.html')).read()
js = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')
out = tpl.replace('/*__DATA__*/null', js)
os.makedirs(os.path.join(ROOT, 'dist'), exist_ok=True)
open(os.path.join(ROOT, 'dist', 'index.html'), 'w').write(out)
print('built', len(dests), 'destinations,', round(len(out)/1024), 'KB')
