#!/usr/bin/env python
"""
AIR_bts.py - US DOT / BTS T-100 International Segment (All Carriers).

Monthly passengers / seats / departures performed on non-stop segments between
the United States and the Caribbean & Mexican destination airports tracked by
the dashboard.

Source (official, free, no key):
  page:  https://www.transtats.bts.gov/DL_SelectFields.aspx?gnoyr_VQ=FJE
         (TranStats > Aviation > Air Carrier Statistics (Form 41 Traffic) -
          All Carriers > "T-100 International Segment (All Carriers)")

Automation recipe
-----------------
The TranStats PREZIP directory only holds stale 2015 user exports, and T-100 is
NOT on data.bts.gov (Socrata), so the automatable route is the ASP.NET form
postback used by the "Download" button:

  1. GET  DL_SelectFields.aspx?gnoyr_VQ=FJE   -> scrape __VIEWSTATE,
          __VIEWSTATEGENERATOR, __EVENTVALIDATION (cookies must be kept).
  2. POST to the same URL with those three tokens plus
          cboGeography=All, cboYear=<YYYY>, cboPeriod=All,
          chkDownloadZip=on, btnDownload=Download and one
          "<FIELD_NAME>=on" pair per column wanted.
  3. Response is a ZIP containing T_T100I_SEGMENT_ALL_CARRIER.csv
          (+ Documentation.csv).  One request per calendar year.

Table = *All Carriers*: US carriers report on Form 41 T-100, foreign carriers
on BTS Form 41 Schedule T-100(f).  Segment = non-stop flight stage, so a
US->PUJ row counts people physically flown on that leg.
"""
import io, os, re, sys, zipfile
import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "..", "raw", "AIR")
OUT = os.path.join(HERE, "..", "air")

PAGE_URL = ("https://www.transtats.bts.gov/DL_SelectFields.aspx"
            "?gnoyr_VQ=FJE&QO_fu146_anzr=Nv4%20Pn44vr45")
TABLE_PAGE = "https://www.transtats.bts.gov/Tables.asp?QO_VQ=EEE"

VARS = ["DEPARTURES_PERFORMED", "SEATS", "PASSENGERS", "ORIGIN",
        "ORIGIN_COUNTRY", "DEST", "DEST_COUNTRY", "YEAR", "MONTH", "CLASS"]

YEARS = list(range(2019, 2027))

# airport -> (destination id, pretty name)
AIRPORTS = {
    "PUJ": ("DO", "Punta Cana"),
    "SDQ": ("DO", "Santo Domingo Las Américas"),
    "CUN": ("MX-CUN", "Cancún"),
    "SJD": ("MX-SJD", "Los Cabos"),
    "MBJ": ("JM", "Montego Bay"),
    "KIN": ("JM", "Kingston"),
    "NAS": ("BS", "Nassau"),
}


def _tok(html, name):
    m = re.search(r'id="%s"[^>]*value="([^"]*)"' % name, html)
    return m.group(1) if m else ""


def download_year(year, session, force=False):
    """Return the raw CSV bytes for one calendar year (cached on disk)."""
    os.makedirs(RAW, exist_ok=True)
    cache = os.path.join(RAW, "T_T100I_SEGMENT_ALL_CARRIER_%d.csv" % year)
    if os.path.exists(cache) and not force and os.path.getsize(cache) > 1000:
        return open(cache, "rb").read()

    r = session.get(PAGE_URL, timeout=300)
    r.raise_for_status()
    data = {
        "__VIEWSTATE": _tok(r.text, "__VIEWSTATE"),
        "__VIEWSTATEGENERATOR": _tok(r.text, "__VIEWSTATEGENERATOR"),
        "__EVENTVALIDATION": _tok(r.text, "__EVENTVALIDATION"),
        "cboGeography": "All",
        "cboYear": str(year),
        "cboPeriod": "All",
        "chkDownloadZip": "on",
        "btnDownload": "Download",
    }
    for v in VARS:
        data[v] = "on"
    p = session.post(PAGE_URL, data=data, timeout=1800)
    p.raise_for_status()
    if p.content[:2] != b"PK":
        raise RuntimeError("year %d: expected ZIP, got %s" %
                           (year, p.headers.get("Content-Type")))
    z = zipfile.ZipFile(io.BytesIO(p.content))
    name = [n for n in z.namelist() if "SEGMENT" in n.upper()][0]
    raw = z.read(name)
    with open(cache, "wb") as f:
        f.write(raw)
    print("  downloaded %d -> %s (%.1f MB csv)" % (year, cache, len(raw) / 1e6))
    return raw


def load(force=False):
    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0 (tourism-dashboard/BTS-T100)"
    frames = []
    for y in YEARS:
        try:
            raw = download_year(y, s, force=force)
        except Exception as e:                      # noqa: BLE001
            print("  !! year %d unavailable: %s" % (y, e))
            continue
        df = pd.read_csv(io.BytesIO(raw), encoding="latin-1", low_memory=False)
        df.columns = [c.strip().upper() for c in df.columns]
        frames.append(df)
    if not frames:
        raise SystemExit("no T-100 data downloaded")
    return pd.concat(frames, ignore_index=True)


def aggregate(df):
    """monthly dict: (airport, direction, metric) -> {period: value}"""
    df = df[df["ORIGIN"].notna() & df["DEST"].notna()].copy()
    for c in ("PASSENGERS", "SEATS", "DEPARTURES_PERFORMED"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    df["period"] = (df["YEAR"].astype(int).astype(str) + "-" +
                    df["MONTH"].astype(int).astype(str).str.zfill(2))

    # The month grid is taken from the whole T-100 file, not from the single
    # segment: a month with no US->MBJ row is a month in which no carrier
    # operated that segment (Apr/May 2020 border closures), i.e. a true zero,
    # not a hole in the source.
    grid = sorted(set(df["period"]))

    out = {}
    for ap in AIRPORTS:
        # US -> airport  (arrivals into the destination)
        inb = df[(df["ORIGIN_COUNTRY"] == "US") & (df["DEST"] == ap)]
        # airport -> US  (departures out of the destination)
        outb = df[(df["DEST_COUNTRY"] == "US") & (df["ORIGIN"] == ap)]
        for tag, sub in (("in", inb), ("out", outb)):
            g = sub.groupby("period")[
                ["PASSENGERS", "SEATS", "DEPARTURES_PERFORMED"]].sum()
            for metric in g.columns:
                d = {p: int(round(v)) for p, v in g[metric].items()}
                out[(ap, tag, metric)] = {p: d.get(p, 0) for p in grid}
    out["_grid"] = grid
    return out


def complete_months(agg):
    """
    Months that are fully released.  BTS loads T-100 a month at a time, so the
    tail of the newest year is simply absent; a month counts as released once
    every tracked airport shows non-zero US traffic in it (all seven of these
    airports have year-round US service).
    """
    grid = agg["_grid"]
    ok = []
    for m in grid:
        if all(agg[(ap, "in", "PASSENGERS")].get(m, 0) > 0 or m.startswith("2020-")
               for ap in AIRPORTS):
            ok.append(m)
    # keep only a contiguous run from the start
    res = []
    for m in grid:
        if m in ok:
            res.append(m)
        elif res:
            break
    return res


def series(agg, airports, tag, metric, months):
    tot = {}
    for m in months:
        v = sum(agg[(ap, tag, metric)].get(m, 0) for ap in airports)
        if v:
            tot[m] = v
    return [[m, tot[m]] for m in sorted(tot)]


def main():
    force = "--force" in sys.argv
    df = load(force=force)
    agg = aggregate(df)
    months = complete_months(agg)
    print("T-100 rows:", len(df), "| complete months:",
          months[0], "->", months[-1])
    # per-airport last reported month (to document the lag precisely)
    for ap in AIRPORTS:
        ks = sorted(agg[(ap, "in", "PASSENGERS")])
        print("  %s last=%s  (%s pax)" % (ap, ks[-1] if ks else "-",
              agg[(ap, "in", "PASSENGERS")].get(ks[-1]) if ks else "-"))
    import json
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(RAW, "t100_agg.json"), "w") as f:
        json.dump({"|".join(k): v for k, v in agg.items()}, f)
    print("wrote", os.path.join(RAW, "t100_agg.json"))
    return agg, months


if __name__ == "__main__":
    main()
