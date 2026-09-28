"""Download ECCB 'Selected Tourism Statistics' (external sector) as CSV for all ECCU members.

Mechanics (Laravel site, no public API):
 1. GET the report page -> session cookie + CSRF _token
 2. POST the report form (country_code[], frequency, start_date, end_date dd/mm/yyyy)
 3. The response contains form #frmCsvDownload with encrypted hidden params;
    POST them with format=csv to /statistics/csv-export -> long-format CSV.
Output: raw/ECCB/eccb_selected_tourism_<freq>_<country>.csv
"""
import re, sys, datetime, pathlib, requests, html as H

WD = pathlib.Path(__file__).resolve().parents[1]
OUT = WD / "raw" / "ECCB"
BASE = "https://www.eccb-centralbank.org"
REPORT = BASE + "/statistics-category/external-sector/selected-tourism-statistics"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
COUNTRIES = {1: "AI", 2: "AG", 3: "DM", 4: "GD", 5: "MS", 6: "KN", 7: "LC", 8: "VC", 9: "ECCU"}


def fetch(country_code: int, freq: str = "M", start="31/01/2019", end=None) -> pathlib.Path:
    """Download one CSV and validate it; raises on any failure (never leaves a partial file)."""
    if end is None:  # through the end of the current year: ECCB returns what is published
        end = f"31/12/{datetime.date.today().year}"
    OUT.mkdir(parents=True, exist_ok=True)
    s = requests.Session()
    s.headers["User-Agent"] = UA
    r = s.get(f"{REPORT}/{freq.lower()}", timeout=60)
    r.raise_for_status()
    m = re.search(r'name="_token" type="hidden" value="([^"]+)"', r.text)
    if not m:
        raise RuntimeError(f"ECCB: no CSRF _token in {REPORT}/{freq.lower()} (page layout changed?)")
    token = m.group(1)
    r = s.post(REPORT, data={"_token": token, "country_code[]": str(country_code), "frequency": freq,
                             "start_date": start, "end_date": end},
               headers={"Referer": f"{REPORT}/{freq.lower()}"}, timeout=120)
    r.raise_for_status()
    m = re.search(r'<form[^>]*id="frmCsvDownload".*?</form>', r.text, flags=re.S)
    if not m:
        raise RuntimeError("ECCB: form #frmCsvDownload not found in report response")
    form = m.group(0)
    data = {n: H.unescape(v) for n, v in re.findall(r'<input[^>]*name="([^"]+)"[^>]*value="([^"]*)"', form)}
    data["format"] = "csv"
    r = s.post(BASE + "/statistics/csv-export", data=data, headers={"Referer": REPORT}, timeout=120)
    r.raise_for_status()
    if "text/csv" not in r.headers.get("content-type", ""):
        raise RuntimeError(f"ECCB: csv-export returned {r.headers.get('content-type')!r}, not CSV")
    txt = r.content.decode("utf-8", errors="replace")
    if ('"Selected Tourism Statistics"' not in txt[:500] or '"Country","Indicator Label"' not in txt
            or txt.count("\n") < 20):
        raise RuntimeError("ECCB: downloaded CSV does not look like a Selected Tourism Statistics export")
    p = OUT / f"eccb_selected_tourism_{freq}_{COUNTRIES[country_code]}.csv"
    tmp = p.with_suffix(".csv.part")
    tmp.write_bytes(r.content)
    tmp.replace(p)
    return p


if __name__ == "__main__":
    codes = [int(c) for c in sys.argv[1:]] or list(COUNTRIES)
    for c in codes:
        for f in ("M", "A"):
            start = "31/01/2019" if f == "M" else "31/12/2019"
            print(fetch(c, f, start=start))
