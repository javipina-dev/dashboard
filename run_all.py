#!/usr/bin/env python3
"""Actualiza el Radar Turístico del Caribe: descarga, valida y reconstruye el dashboard.

Uso:
    python run_all.py                 # todo: extractores + validación + build
    python run_all.py --only DO,MX    # solo algunos extractores
    python run_all.py --skip KY       # omite alguno (p. ej. el que necesita navegador)
    python run_all.py --build-only    # solo reconstruye dist/index.html con los datos actuales

Cada extractor baja el archivo oficial desde la URL del organismo y reescribe su JSON.
Si un extractor falla, los demás siguen y su JSON anterior se conserva: el dashboard
nunca queda con datos a medias. El resumen se guarda en run_report.json y se imprime.
"""
import argparse, json, os, subprocess, sys, time, datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(ROOT, 'data', 'scripts')
PY = sys.executable

# Orden de ejecución. Los extractores son independientes entre sí.
EXTRACTORS = [
    ('DO', 'DO_extract.py', 'RD · Banco Central + MITUR'),
    ('DO_poles', 'DO_poles.py', 'RD · polos turísticos'),
    ('DO_markets', 'DO_markets.py', 'RD · mercados de origen'),
    ('MX', 'MX_extract.py', 'Cancún y Los Cabos · DataTur, AFAC, Banxico, Economía'),
    ('MX_markets', 'MX_markets.py', 'Cancún y Los Cabos · mercados de origen'),
    ('BS', 'BS_extract.py', 'Bahamas · Ministerio de Turismo + Banco Central'),
    ('JM', 'JM_extract.py', 'Jamaica · Banco de Jamaica + PIOJ'),
    ('PR', 'PR_extract.py', 'Puerto Rico · Compañía de Turismo + Junta de Planificación'),
    ('CU', 'CU_extract.py', 'Cuba · ONEI'),
    ('AW', 'AW_extract.py', 'Aruba · Banco Central'),
    ('CW', 'CW_extract.py', 'Curazao · Curaçao Tourist Board'),
    ('BB', 'BB_extract.py', 'Barbados · Statistical Service + Banco Central'),
    ('LC', 'LC_extract.py', 'Santa Lucía · ECCB + CSO'),
    ('TC', 'TC_extract.py', 'Turcas y Caicos · Statistics Authority'),
    ('KY', 'KY_extract.py', 'Islas Caimán · Department of Tourism (requiere navegador headless)'),
    ('CR', 'CR_extract.py', 'Costa Rica · ICT + BCCR'),
    ('PA', 'PA_extract.py', 'Panamá · INEC'),
    ('AIR_bts', 'AIR_bts.py', 'Conectividad · US DOT T-100'),
    ('AIR_national', 'AIR_national.py', 'Conectividad · JAC (RD) y AFAC (México)'),
]

TIMEOUT = 1800  # 30 min por extractor


def run(cmd, cwd, timeout=TIMEOUT):
    t0 = time.time()
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or '')[-4000:], (r.stderr or '')[-4000:], time.time() - t0
    except subprocess.TimeoutExpired:
        return 124, '', f'timeout tras {timeout}s', time.time() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--only', default='')
    ap.add_argument('--skip', default='')
    ap.add_argument('--build-only', action='store_true')
    args = ap.parse_args()
    only = [x.strip() for x in args.only.split(',') if x.strip()]
    skip = [x.strip() for x in args.skip.split(',') if x.strip()]

    report = {'started_at': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
              'extractors': [], 'validation': None, 'build': None}

    if not args.build_only:
        for key, script, desc in EXTRACTORS:
            if only and key not in only:
                continue
            if key in skip:
                report['extractors'].append({'key': key, 'status': 'skipped', 'desc': desc})
                print(f'[skip] {key}')
                continue
            print(f'[run ] {key}: {desc}', flush=True)
            code, out, err, secs = run([PY, os.path.join(SCRIPTS, script)], ROOT)
            status = 'ok' if code == 0 else 'error'
            print(f'[{status:4}] {key} ({secs:.0f}s)' + ('' if code == 0 else f'\n{err[-1500:]}'), flush=True)
            report['extractors'].append({'key': key, 'desc': desc, 'status': status,
                                         'seconds': round(secs), 'stdout_tail': out[-1200:],
                                         'stderr_tail': err[-1200:] if code else ''})

    # validación de formato y coherencia de todos los JSON
    code, out, err, _ = run([PY, os.path.join(SCRIPTS, '_validate.py')], ROOT, timeout=300)
    report['validation'] = {'status': 'ok' if code == 0 else 'error', 'output': (out + err)[-4000:]}
    print(f'[{"ok" if code == 0 else "error"}] validación\n{(out + err)[-2000:]}', flush=True)

    # build del dashboard
    code, out, err, _ = run([PY, os.path.join(ROOT, 'pipeline', 'build.py')], ROOT, timeout=600)
    report['build'] = {'status': 'ok' if code == 0 else 'error', 'output': (out + err)[-4000:]}
    print(f'[{"ok" if code == 0 else "error"}] build\n{(out + err)[-2000:]}', flush=True)

    # resumen de cobertura: último período de cada serie principal
    cfg = json.load(open(os.path.join(ROOT, 'pipeline', 'config.json')))
    latest = {}
    for did, head in cfg['headline'].items():
        p = os.path.join(ROOT, 'data', f'{did}.json')
        if not os.path.exists(p):
            continue
        series = {s['key']: s for s in json.load(open(p))['series']}
        for role in ('arrivals', 'spending'):
            k = head.get(role)
            if k and k in series and series[k]['data']:
                latest[f'{did}.{role}'] = series[k]['data'][-1][0]
    report['latest_periods'] = latest
    report['finished_at'] = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    with open(os.path.join(ROOT, 'run_report.json'), 'w') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    errs = [e['key'] for e in report['extractors'] if e['status'] == 'error']
    print('\n=== RESUMEN ===')
    print('extractores ok   :', sum(1 for e in report['extractors'] if e['status'] == 'ok'))
    print('extractores error:', ', '.join(errs) or 'ninguno')
    print('validación       :', report['validation']['status'])
    print('build            :', report['build']['status'])
    print('últimos períodos :', json.dumps(latest, ensure_ascii=False))
    return 0 if report['build']['status'] == 'ok' and report['validation']['status'] == 'ok' else 1


if __name__ == '__main__':
    sys.exit(main())
