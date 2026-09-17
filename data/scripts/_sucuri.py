"""Helper: fetch pages behind Sucuri CloudProxy JS challenge (cbs.aw) by solving the cookie."""
import re, base64, requests
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

def _eval_concat(expr):
    out = ""
    for m in re.finditer(r"String\.fromCharCode\((\d+)\)|'([^']*)'|\"([^\"]*)\"", expr):
        if m.group(1): out += chr(int(m.group(1)))
        else: out += m.group(2) if m.group(2) is not None else m.group(3)
    return out

def session():
    s = requests.Session(); s.headers["User-Agent"] = UA; return s

def get(s, url, **kw):
    r = s.get(url, timeout=60, **kw)
    if "sucuri_cloudproxy_js" in r.text[:3000]:
        S = re.search(r"S='([^']+)'", r.text).group(1)
        js = base64.b64decode(S).decode()
        wexpr, rest = js.split(";document.cookie=", 1)
        val = _eval_concat(wexpr[2:])
        name = _eval_concat(rest.split('"="')[0] if '"="' in rest else rest.split("=")[0])
        s.cookies.set(name, val)
        r = s.get(url, timeout=60, **kw)
    return r
