"""Download the official Bahamas source files into data/raw/BS/ (used by BS_extract.py).

Every file is located from the official listing pages (never from hard-coded file URLs), so the
newest version is always taken:
  * https://www.tourismtoday.com/statistics/foreign-arrivals-air-sea-data
        "Air, Sea Landed & Cruise Arrivals" table: one link per year (link text = year).
        The current-year file name changes with each monthly revision (e.g. '... 2026 (4).pdf').
  * https://www.tourismtoday.com/statistics/frequently-requested-stopover-statistics-trends
        "Yearly Comparison of Stopover Visitors by Island and Month 2015-YYYY" (name changes every year)
        "Stopover Visitors Average Length of Stay 1992-YYYY"
  * https://www.tourismtoday.com/statistics/expenditure
        "Expenditure by Quarter" table: link text "YYYY & YYYY-1"
  * https://www.centralbankbahamas.com/publications/qsd
        Quarterly Statistical Digest PDFs on cdn.centralbankbahamas.com (file names carry a random suffix,
        so old URLs stop working -> always scraped). Digests older than those on the listing page
        (Aug-2021) are taken from their own publication page.

Caching: files for the period in progress (current-year arrivals PDF, stopover PDF, newest expenditure PDF,
newest QSD) are downloaded on every run. Closed-period files (previous years' arrivals, older expenditure
PDFs, older QSD vintages, ALOS) are kept in data/raw/BS/ and re-downloaded only if missing or if the
official link changed (a changed link means the Ministry re-published the file).
Any failure raises FetchError: the caller must stop without writing data/BS.json.
"""
import html
import json
import re
import time
import urllib.parse
from datetime import date
from pathlib import Path

import requests

RAW = Path(__file__).resolve().parents[1] / "raw" / "BS"
MANIFEST = RAW / "_sources.json"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0 Safari/537.36")
TT = "https://www.tourismtoday.com"
ARR_PAGE = f"{TT}/statistics/foreign-arrivals-air-sea-data"
STOP_PAGE = f"{TT}/statistics/frequently-requested-stopover-statistics-trends"
EXP_PAGE = f"{TT}/statistics/expenditure"
CB = "https://www.centralbankbahamas.com"
QSD_PAGE = f"{CB}/publications/qsd"
# QSD vintages the extractor needs besides the newest digest (see BS_extract.qsd_bop); every August
# digest from 2026 on is also used, so each quarter keeps coming from the latest annual vintage.
QSD_FIXED = ["2021-08", "2023-08", "2025-08"]
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
          "september", "october", "november", "december"]


class FetchError(RuntimeError):
    pass


_session = None


def _s():
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers["User-Agent"] = UA
    return _session


def _get(url, tries=3):
    last = None
    for i in range(tries):
        try:
            r = _s().get(url, timeout=90)
            if r.status_code == 200:
                return r
            last = f"HTTP {r.status_code}"
            if r.status_code in (403, 404, 410):
                break
        except requests.RequestException as e:
            last = f"{type(e).__name__}: {e}"
        time.sleep(5 * (i + 1))
    raise FetchError(f"download failed: {url} ({last})")


def load_manifest():
    try:
        return json.loads(MANIFEST.read_text())
    except (OSError, ValueError):
        return {}


def _links(page_url):
    """[(absolute_url, link_text)] for every <a href> on an official listing page."""
    text = _get(page_url).text
    out = []
    # label = text up to </a> or the next <a (the CBOB page has unclosed anchors)
    for m in re.finditer(r'<a\b[^>]*?href="([^"]+)"[^>]*>((?:(?!<a\b|</a>).)*)', text, re.S | re.I):
        url = urllib.parse.urljoin(page_url, html.unescape(m.group(1)).strip())
        url = re.sub(r"^http://www\.tourismtoday\.com", TT, url)
        label = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(m.group(2)))).strip()
        out.append((url, label))
    if not out:
        raise FetchError(f"no links found on {page_url}")
    return out


def _flat(url):
    return re.sub(r"[^a-z0-9]", "", urllib.parse.unquote(url.rsplit("/", 1)[-1]).lower())


class Fetcher:
    def __init__(self):
        RAW.mkdir(parents=True, exist_ok=True)
        self.manifest = load_manifest()
        self.today = date.today().isoformat()

    def _valid(self, p):
        return p.exists() and p.stat().st_size > 10_000 and p.read_bytes()[:5] == b"%PDF-"

    def file(self, name, url, cache):
        """Download url -> RAW/name. cache=True: keep the local copy if it came from the same url."""
        dest = RAW / name
        if cache and self._valid(dest) and self.manifest.get(name, {}).get("url") == url:
            print(f"  cached  {name}")
            return
        r = _get(url)
        if r.content[:5] != b"%PDF-" or len(r.content) < 10_000:
            raise FetchError(f"not a PDF: {url} ({r.headers.get('content-type')}, {len(r.content)} bytes)")
        tmp = dest.with_suffix(".part")
        tmp.write_bytes(r.content)
        tmp.replace(dest)
        self.manifest[name] = {"url": url, "retrieved": self.today}
        print(f"  fetched {name}  <- {url}")

    def save(self):
        MANIFEST.write_text(json.dumps(self.manifest, indent=1, sort_keys=True))

    # ------------------------------------------------------------------ Ministry of Tourism
    def arrivals(self):
        years = {}
        for url, label in _links(ARR_PAGE):
            f = _flat(url)
            if url.lower().endswith(".pdf") and "airsealanded" in f and re.fullmatch(r"\d{4}", label):
                y = int(label)
                if y >= 2019:
                    if y in years and years[y] != url:
                        raise FetchError(f"two 'Air Sea Landed & Cruise Arrivals {y}' links on {ARR_PAGE}")
                    years[y] = url
        if not years:
            raise FetchError(f"no 'Air Sea Landed & Cruise Arrivals' links on {ARR_PAGE}")
        last = max(years)
        missing = [y for y in range(2019, last + 1) if y not in years]
        if missing or last < date.today().year - 1:
            raise FetchError(f"arrivals links incomplete on {ARR_PAGE}: years {sorted(years)}")
        for y in sorted(years):
            self.file(f"as_{y}.pdf", years[y], cache=y < last)
        return last

    def stopover_and_alos(self):
        links = _links(STOP_PAGE)
        stop = [u for u, t in links if re.match(r"Yearly Comparison of Stopover Visitors by Island and Month", t, re.I)]
        alos = [u for u, t in links if re.match(r"Stopover Visitors Average Length of Stay", t, re.I)]
        if len(set(stop)) != 1 or len(set(alos)) != 1:
            raise FetchError(f"expected one stopover-by-island and one ALOS link on {STOP_PAGE}: {stop} {alos}")
        self.file("stopover_month_island.pdf", stop[0], cache=False)
        self.file("alos.pdf", alos[0], cache=True)

    def expenditure(self):
        pairs = {}
        for url, label in _links(EXP_PAGE):
            m = re.fullmatch(r"(\d{4}) & (\d{4})", label)
            if m and url.lower().endswith(".pdf") and int(m.group(1)) == int(m.group(2)) + 1 and int(m.group(1)) >= 2020:
                pairs[int(m.group(1))] = url
        if not pairs or any(y not in pairs for y in range(2020, max(pairs) + 1)):
            raise FetchError(f"expenditure-by-quarter links incomplete on {EXP_PAGE}: {sorted(pairs)}")
        newest = max(pairs)
        for y in sorted(pairs):
            self.file(f"exp_q_{y}_{y - 1}.pdf", pairs[y], cache=y < newest)

    # ------------------------------------------------------------------ Central Bank QSD
    @staticmethod
    def _qsd_period(url):
        name = urllib.parse.unquote(url.rsplit("/", 1)[-1])
        if "digest" not in name.lower():
            return None
        tail = name[name.lower().index("digest"):]
        m = re.search(r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*-*(\d{4})", tail, re.I)
        if not m:
            return None
        mon = [x[:3] for x in MONTHS].index(m.group(1).lower()[:3]) + 1
        return f"{m.group(2)}-{mon:02d}"

    def _qsd_pdfs(self, page, tries=4):
        """{period: pdf_url} of the digest PDFs linked on a CBOB page. The site intermittently serves the page
        without its document list (HTTP 200), so an empty result is retried before giving up."""
        for i in range(tries):
            found = {}
            for url, _ in _links(page):
                if url.startswith("https://cdn.centralbankbahamas.com/") and url.lower().endswith(".pdf"):
                    p = self._qsd_period(url)
                    if p:
                        if p in found and found[p] != url:
                            raise FetchError(f"two QSD PDFs for {p} on {page}")
                        found[p] = url
            if found:
                return found
            time.sleep(10 * (i + 1))
        return {}

    def qsd(self):
        listing = self._qsd_pdfs(QSD_PAGE)
        if not listing:
            raise FetchError(f"no QSD PDF links on {QSD_PAGE} after retries")
        latest = max(listing)
        vintages = sorted(set(QSD_FIXED) | {p for p in listing if p.endswith("-08") and p >= "2026-08"} | {latest})
        for p in vintages:
            name = f"cbob_qsd_{p}.pdf"
            if p == latest:
                self.file(name, listing[p], cache=False)
            elif p in listing:
                self.file(name, listing[p], cache=True)
            elif self._valid(RAW / name):
                print(f"  cached  {name}")
            else:
                self.file(name, self._qsd_from_detail_page(p), cache=True)
        return vintages, latest

    def _qsd_from_detail_page(self, period):
        y, m = period.split("-")
        mon = MONTHS[int(m) - 1]
        for slug in (f"quarterly-statistical-digest-{mon}-{y}-1", f"quarterly-statistical-digest-{mon}-{y}"):
            page = f"{QSD_PAGE}/{slug}"
            try:
                pdfs = self._qsd_pdfs(page)
            except FetchError:
                continue
            if period in pdfs:
                return pdfs[period]
        raise FetchError(f"QSD {period} PDF not found on its publication page under {QSD_PAGE}/")


def fetch_all():
    """Download everything; returns the manifest. Raises FetchError on any failure."""
    f = Fetcher()
    print("Downloading Bahamas sources ->", RAW)
    f.arrivals()
    f.stopover_and_alos()
    f.expenditure()
    f.qsd()
    f.save()
    return f.manifest
