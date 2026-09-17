"""Polite fetcher for ONEI (Cuba). Notes for automation:
- www.onei.gob.cu over HTTPS returns 500 (proxy SSL error, expired cert); over HTTP it intermittently serves a JS redirect page.
- https://onei.gob.cu (without www) serves files directly, but the certificate is expired (verify=False) and connections drop intermittently -> retries with back-off.
Usage: CU_fetch.py <relative_path_or_url> <out_name> [...pairs]
"""
import os, sys, time
import requests, urllib3

urllib3.disable_warnings()
WD = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(WD, 'raw', 'CU')
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'
BASE = 'https://onei.gob.cu'


def fetch(path, out, tries=6):
    url = path if path.startswith('http') else BASE + path
    dest = os.path.join(RAW, out)
    if os.path.exists(dest) and os.path.getsize(dest) > 5000:
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
                with open(dest, 'wb') as f:
                    f.write(r.content)
                print('OK', r.status_code, len(r.content), ct, out)
                return True
            print('retry', i, r.status_code, len(r.content), ct, url)
        except Exception as e:
            print('err', i, type(e).__name__, url)
        time.sleep(10 + 10 * i)
    print('FAIL', url)
    return False


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
