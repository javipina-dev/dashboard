"""Download ECCB 'Selected Tourism Statistics' (external sector) as CSV for all ECCU members.

Mechanics (Laravel site, no public API):
 1. GET the report page -> session cookie + CSRF _token
 2. POST the report form (country_code[], frequency, start_date, end_date dd/mm/yyyy)
 3. The response contains form #frmCsvDownload with encrypted hidden params;
    POST them with format=csv to /statistics/csv-export -> long-format CSV.
Output: raw/ECCB/eccb_selected_tourism_<freq>_<country>.csv
"""
import re, sys, pathlib, requests, html as H

WD = pathlib.Path(__file__).resolve().parents[1]
OUT = WD / "raw" / "ECCB"
OUT.mkdir(parents=True, exist_ok=True)
BASE = "https://www.eccb-centralbank.org"
REPORT = BASE + "/statistics-category/external-sector/selected-tourism-statistics"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
COUNTRIES = {1: "AI", 2: "AG", 3: "DM", 4: "GD", 5: "MS", 6: "KN", 7: "LC", 8: "VC", 9: "ECCU"}


def fetch(country_code: int, freq: str = "M", start="31/01/2019", end="31/12/2026") -> pathlib.Path:
    s = requests.Session()
    s.headers["User-Agent"] = UA
    r = s.get(f"{REPORT}/{freq.lower()}", timeout=60)
    r.raise_for_status()
    token = re.search(r'name="_token" type="hidden" value="([^"]+)"', r.text).group(1)
    r = s.post(REPORT, data={"_token": token, "country_code[]": str(country_code), "frequency": freq,
                             "start_date": start, "end_date": end},
               headers={"Referer": f"{REPORT}/{freq.lower()}"}, timeout=120)
    r.raise_for_status()
    form = re.search(r'<form[^>]*id="frmCsvDownload".*?</form>', r.text, flags=re.S).group(0)
    data = {n: H.unescape(v) for n, v in re.findall(r'<input[^>]*name="([^"]+)"[^>]*value="([^"]*)"', form)}
    data["format"] = "csv"
    r = s.post(BASE + "/statistics/csv-export", data=data, headers={"Referer": REPORT}, timeout=120)
    r.raise_for_status()
    assert "text/csv" in r.headers.get("content-type", ""), r.headers
    p = OUT / f"eccb_selected_tourism_{freq}_{COUNTRIES[country_code]}.csv"
    p.write_bytes(r.content)
    return p


if __name__ == "__main__":
    codes = [int(c) for c in sys.argv[1:]] or list(COUNTRIES)
    for c in codes:
        for f in ("M", "A"):
            start = "31/01/2019" if f == "M" else "31/12/2019"
            print(fetch(c, f, start=start))
