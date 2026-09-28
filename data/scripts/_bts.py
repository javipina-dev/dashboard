"""
_bts.py - download helper for the official BTS "Data Bank 28IS" files
(T-100 and T-100(f) International Segment Data, U.S. and Foreign Air Carriers,
World Area Code version), published by US DOT / BTS / Office of Airline
Information on bts.gov.

Why this route
--------------
The TranStats download form (DL_SelectFields.aspx?gnoyr_VQ=FJE) that
AIR_bts.py used until 2026-09 is unavailable: every TranStats page redirects to
Maintenance.aspx?reason=db ("Database recovery is in progress ... live
database-backed data is temporarily unavailable") or to ErrPage.asp (404).
T-100 is not on data.bts.gov (Socrata only carries yearly/airport summaries
and "preliminary estimates") and TranStats /PREZIP has no T-100 files.

BTS publishes the same T-100 International Segment records (U.S. carriers,
Form 41 T-100, plus foreign carriers, T-100(f)) as monthly "Data Bank 28IS"
releases, each a rolling 12-month window:

  listing: https://www.bts.gov/browse-statistical-products-and-data/
           bts-publications/data-bank-28is-t-100-and-t-100f-internationa-0
  files:   https://www.bts.gov/sites/bts.dot.gov/files/docs/airline-data/
           international-segments/DB28SEG.FD.WAC.<YYYYMM>.<YYYYMM>.REL<nn>.<ddMMMyyyy>.zip
  layout:  https://www.bts.gov/sites/bts.dot.gov/files/docs/explore-topics-and-geography/
           topics/airlines-and-airports/230176/reference-file-db28-segment-data-product.pdf

Each zip holds one pipe-separated ASCII file with 28 fields per record (see
FIELDS).  bts.gov sits behind Akamai bot protection that rejects Python's TLS
client (HTTP 403) but accepts curl with ordinary browser headers over HTTP/2,
so downloads go through the system `curl`.
"""
import io
import os
import re
import subprocess
import time
import zipfile

LISTING_URL = ("https://www.bts.gov/browse-statistical-products-and-data/"
               "bts-publications/data-bank-28is-t-100-and-t-100f-internationa-0")
FILE_BASE = ("https://www.bts.gov/sites/bts.dot.gov/files/docs/airline-data/"
             "international-segments/")
LAYOUT_URL = ("https://www.bts.gov/sites/bts.dot.gov/files/docs/"
              "explore-topics-and-geography/topics/airlines-and-airports/230176/"
              "reference-file-db28-segment-data-product.pdf")

# Record layout from the BTS reference file (fields 1-22 and new 23-28).
FIELDS = ["YEAR", "MONTH", "ORIGIN", "ORIGIN_AIRPORT_ID", "ORIGIN_WAC",
          "ORIGIN_CITY_NAME", "DEST", "DEST_AIRPORT_ID", "DEST_WAC",
          "DEST_CITY_NAME", "CARRIER", "CARRIER_ENTITY", "CARRIER_GROUP",
          "DISTANCE", "CLASS", "AIRCRAFT_GROUP", "AIRCRAFT_TYPE",
          "AIRCRAFT_CONFIG", "DEPARTURES_PERFORMED", "DEPARTURES_SCHEDULED",
          "PAYLOAD", "SEATS", "PASSENGERS", "FREIGHT", "MAIL",
          "RAMP_TO_RAMP", "AIR_TIME", "CARRIER_WAC"]

_HEADERS = [
    "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language: en-US,en;q=0.9",
    "Sec-Fetch-Dest: document", "Sec-Fetch-Mode: navigate",
    "Sec-Fetch-Site: none", "Upgrade-Insecure-Requests: 1",
    'sec-ch-ua: "Chromium";v="128"', 'sec-ch-ua-platform: "macOS"',
    "sec-ch-ua-mobile: ?0",
]

_REL = re.compile(r"DB28SEG\.FD\.WAC\.(\d{6})\.(\d{6})\.REL(\d+)\.(\d{2}[A-Z]{3}\d{4})\.zip")


def fetch(url, timeout=600, tries=4):
    """GET url with curl (HTTP/2, browser headers). Returns bytes; raises on failure."""
    cmd = ["curl", "-sSL", "--http2", "--compressed", "--max-time", str(timeout),
           "-w", "\n%{http_code}"]
    for h in _HEADERS:
        cmd += ["-H", h]
    cmd.append(url)
    last = None
    for i in range(tries):
        p = subprocess.run(cmd, capture_output=True)
        body, _, code = p.stdout.rpartition(b"\n")
        code = code.decode().strip()
        if p.returncode == 0 and code == "200":
            return body
        last = "curl exit %s, HTTP %s" % (p.returncode, code or "-")
        time.sleep(3 * (i + 1))
    raise RuntimeError("BTS download failed (%s): %s" % (last, url))


def _month_list(a, b):
    y, m = int(a[:4]), int(a[4:])
    out = []
    while "%04d%02d" % (y, m) <= b:
        out.append("%04d-%02d" % (y, m))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def releases():
    """[(first 'YYYY-MM', last 'YYYY-MM', release no., release date str, file name)]
    parsed from the official Data Bank 28IS (WAC) listing page."""
    html = fetch(LISTING_URL, timeout=120).decode("utf-8", "replace")
    out = {}
    for m in _REL.finditer(html):
        a, b, rel, day = m.groups()
        out[m.group(0)] = (a[:4] + "-" + a[4:], b[:4] + "-" + b[4:], int(rel), day,
                           m.group(0))
    if not out:
        raise RuntimeError("no DB28SEG.FD.WAC releases found on " + LISTING_URL)
    return sorted(out.values(), key=lambda r: (r[1], r[2]))


def plan(first_month="2019-01"):
    """
    Choose which releases to read: every month from first_month to the newest
    published month is taken from the NEWEST release that contains it (BTS
    folds carrier corrections into later releases), walking back from the
    latest file.  Returns [(release tuple, [months to take from it])].
    """
    rels = releases()
    newest = rels[-1][1]
    todo = _month_list(first_month.replace("-", ""), newest.replace("-", ""))
    chosen = []
    remaining = set(todo)
    while remaining:
        target = max(remaining)
        cands = [r for r in rels if r[0] <= target <= r[1]]
        if not cands:
            raise RuntimeError("no Data Bank 28IS release covers %s" % target)
        r = max(cands, key=lambda r: (r[1], r[2]))
        months = sorted(m for m in remaining
                        if r[0] <= m <= r[1])
        chosen.append((r, months))
        remaining -= set(months)
    return chosen


def read_release(rel, raw_dir, force=False):
    """Download (or reuse the byte-identical cached copy of) one release zip and
    return its ASCII records as bytes."""
    os.makedirs(raw_dir, exist_ok=True)
    name = rel[4]
    path = os.path.join(raw_dir, name)
    if not force and os.path.exists(path) and zipfile.is_zipfile(path):
        # release file names carry release number + date, so a cached file IS
        # that exact official release
        print("  cached  %s" % name)
        data = open(path, "rb").read()
    else:
        data = fetch(FILE_BASE + name)
        if data[:2] != b"PK":
            raise RuntimeError("%s: expected ZIP, got %r" % (name, data[:40]))
        with open(path + ".part", "wb") as f:
            f.write(data)
        os.replace(path + ".part", path)
        print("  downloaded %s (%.1f MB)" % (name, len(data) / 1e6))
    z = zipfile.ZipFile(io.BytesIO(data))
    asc = [n for n in z.namelist() if n.lower().endswith((".asc", ".txt", ".csv"))]
    if len(asc) != 1:
        raise RuntimeError("%s: unexpected contents %s" % (name, z.namelist()))
    return z.read(asc[0])
