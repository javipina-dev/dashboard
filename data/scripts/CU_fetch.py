"""Polite fetcher for ONEI (Cuba). Notes for automation:
- www.onei.gob.cu over HTTPS returns 500 (proxy SSL error, expired cert); over HTTP it intermittently serves a JS redirect page.
- https://onei.gob.cu (without www) serves files directly, but the certificate is expired (verify=False) and connections drop intermittently -> retries with back-off.
Used by CU_extract.py (download_all) to discover and download the ONEI files on every run.
Usage: CU_fetch.py <relative_path_or_url> <out_name> [...pairs]
"""
import os, re, sys, time
import requests, urllib3

urllib3.disable_warnings()
WD = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(WD, 'raw', 'CU')
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'
BASE = 'https://onei.gob.cu'


class FetchError(RuntimeError):
    pass


def to_base(url):
    """Rewrite www.onei.gob.cu / http URLs to the working host https://onei.gob.cu."""
    return re.sub(r'^https?://(www\.)?onei\.gob\.cu', BASE, url)


def fetch(path, out, tries=6, force=False):
    """Download path -> RAW/out. Returns True if saved (or cached and force=False), False on 404/failure.
    The file is written via a temporary name, so a failed download never leaves a truncated file."""
    url = to_base(path) if path.startswith('http') else BASE + path
    os.makedirs(RAW, exist_ok=True)
    dest = os.path.join(RAW, out)
    if not force and os.path.exists(dest) and os.path.getsize(dest) > 5000:
        print('SKIP', out)
        return True
    for i in range(tries):
        try:
            r = requests.get(url, headers={'User-Agent': UA}, timeout=45, verify=False)
            ct = r.headers.get('content-type', '')
            if r.status_code == 404:
                print('404', url)
                return False
            html_ok = out.endswith('.html') and len(r.content) > 5000 and b'<html' in r.content[:2000].lower()
            bin_ok = r.content[:4] in (b'%PDF', b'PK\x03\x04', b'\xd0\xcf\x11\xe0')
            if r.status_code == 200 and (html_ok or bin_ok):
                with open(dest + '.part', 'wb') as f:
                    f.write(r.content)
                os.replace(dest + '.part', dest)
                print('OK', r.status_code, len(r.content), ct, out)
                return True
            print('retry', i, r.status_code, len(r.content), ct, url)
        except Exception as e:
            print('err', i, type(e).__name__, url)
        time.sleep(10 + 10 * i)
    print('FAIL', url)
    return False


def get_html(path, tries=6):
    """GET an ONEI page. Returns its HTML, None on HTTP 404; raises FetchError if the server does not answer."""
    url = to_base(path) if path.startswith('http') else BASE + path
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, headers={'User-Agent': UA}, timeout=60, verify=False)
            if r.status_code == 404:
                return None
            if r.status_code == 200 and len(r.content) > 5000 and b'<html' in r.content[:3000].lower():
                return r.text
            last = f'HTTP {r.status_code}, {len(r.content)} bytes'
        except Exception as e:
            last = type(e).__name__
        print('retry', i, last, url)
        time.sleep(10 + 10 * i)
    raise FetchError(f'ONEI no responde: {url} ({last}, {tries} intentos)')


MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre']
# node aliases seen on onei.gob.cu for "Arribo de viajeros. Visitantes internacionales <Mes> <Año>"
SLUG_PREFIXES = ['arribo-de-viajeros-visitantes-internacionales', 'arribo-de-viajeros-y-visitantes-internacionales',
                 'turismo-arribo-de-viajeros-y-visitantes-internacionales', 'arribo-de-viajerosvisitantes-internacionales']
ARRIBO_NODE = re.compile(r'href="(?:https?://(?:www\.)?onei\.gob\.cu)?/((?:turismo-)?arribo-de-viajeros[a-z.-]*?internacionales-?('
                         + '|'.join(MESES) + r')-?(20\d\d)[a-z0-9-]*)"')


def report_pdf(node_html):
    """Path of the 'Arribo de viajeros' PDF linked from a node page (not the per-country xlsx / rar)."""
    links = re.findall(r'href="((?:https?://(?:www\.)?onei\.gob\.cu)?/sites/default/files/[^"]+\.pdf)"', node_html, re.I)
    links = [l for l in dict.fromkeys(links) if 'arribo' in l.lower() or 'viajeros' in l.lower()]
    return links[0] if links else None


def find_report(month, year, search=True):
    """Find the node page of the monthly report (month 1-12, year). Tries the known aliases first, then the site search.
    Returns (node_path, pdf_path) or None if ONEI has no such report (all probes 404 / not in search)."""
    mes = MESES[month - 1]
    cands = [f'/{p}-{mes}-{year}' for p in SLUG_PREFIXES] + [f'/{p}-{mes}{year}' for p in SLUG_PREFIXES[:3]]
    for c in cands:
        h = get_html(c)
        if h is not None:
            pdf = report_pdf(h)
            if not pdf:
                raise FetchError(f'{BASE}{c}: página del informe sin enlace a PDF')
            return c, pdf
    if search:
        h = get_html(f'/search/node?keys=arribo%20viajeros%20{mes}%20{year}')
        for path, m, y in ARRIBO_NODE.findall(h or ''):
            if m == mes and int(y) == year:
                nh = get_html('/' + path)
                pdf = report_pdf(nh or '')
                if pdf:
                    return '/' + path, pdf
    return None


def latest_report(today, max_back=8):
    """Most recent monthly report: newest 'Arribo de viajeros' link on the ONEI home page, then probe later months
    up to last month. Falls back to probing backwards from last month. Returns (year, month, node_path, pdf_path)."""
    home = get_html('/')
    found = []
    for path, m, y in ARRIBO_NODE.findall(home):
        found.append((int(y), MESES.index(m) + 1, '/' + path))
    best = None
    if found:
        y, m, node = max(found)
        pdf = report_pdf(get_html(node) or '')
        if pdf:
            best = (y, m, node, pdf)
    # months after the home-page one (or backwards from last month if the home page has no link)
    y, m = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    for _ in range(max_back):
        if best and (y, m) <= best[:2]:
            break
        r = find_report(m, y, search=best is None)
        if r:
            best = (y, m) + r
            break
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    if not best:
        raise FetchError('no se encontró ningún informe "Arribo de viajeros" reciente en onei.gob.cu')
    return best


def wait_up(max_wait=2400):
    t0 = time.time()
    while time.time() - t0 < max_wait:
        try:
            requests.head(BASE + '/', headers={'User-Agent': UA}, timeout=20, verify=False)
            return True
        except Exception:
            time.sleep(60)
    return False


if __name__ == '__main__':
    args = sys.argv[1:]
    for p, o in zip(args[::2], args[1::2]):
        if not wait_up():
            print('FAIL server down', p)
            continue
        fetch(p, o, tries=3)
        time.sleep(15)
