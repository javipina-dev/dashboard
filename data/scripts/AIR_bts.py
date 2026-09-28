#!/usr/bin/env python
"""
AIR_bts.py - US DOT / BTS T-100 International Segment (All Carriers).

Monthly passengers / seats / departures performed on non-stop segments between
the United States and the Caribbean & Mexican destination airports tracked by
the dashboard.

Source (official, free, no key)
-------------------------------
BTS / Office of Airline Information, "Data Bank 28IS - T-100 and T-100(f)
International Segment Data, U.S. and Foreign Air Carriers Traffic and Capacity
Data (World Area Code)":
  https://www.bts.gov/browse-statistical-products-and-data/bts-publications/
  data-bank-28is-t-100-and-t-100f-internationa-0
Same universe as TranStats "T-100 International Segment (All Carriers)": U.S.
carriers report on Form 41 T-100, foreign carriers on T-100(f).  Segment =
non-stop flight stage, so a US->PUJ row counts people physically flown on that
leg.

Until 2026-09 this script used the TranStats download form
(DL_SelectFields.aspx?gnoyr_VQ=FJE, ASP.NET postback).  TranStats now answers
every page with Maintenance.aspx?reason=db / ErrPage.asp, so the data are read
from the Data Bank 28IS releases instead (see _bts.py for the mechanism):
monthly zips with a rolling 12-month window of pipe-separated records; each
month is taken from the newest release that contains it.

"United States" = airport World Area Code < 100 (first WAC digit 0 = U.S.,
including Puerto Rico and the U.S. Virgin Islands), the same definition as
TranStats ORIGIN_COUNTRY='US'.

Any download/parse failure raises: no partial or stale data is ever used.
"""
import io, os, sys
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _bts  # noqa: E402

RAW = os.path.join(HERE, "..", "raw", "AIR")
OUT = os.path.join(HERE, "..", "air")

FIRST_MONTH = "2019-01"
NUM = ("PASSENGERS", "SEATS", "DEPARTURES_PERFORMED")

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

# filled by load(): the releases actually used (for provenance in the JSON)
USED_RELEASES = []


def _parse(raw, name):
    df = pd.read_csv(io.BytesIO(raw), sep="|", header=None, dtype=str,
                     encoding="latin-1", keep_default_na=False)
    # every record ends with a trailing '|', i.e. 28 fields + 1 empty column
    if df.shape[1] == len(_bts.FIELDS) + 1 and (df.iloc[:, -1] == "").all():
        df = df.iloc[:, :-1]
    if df.shape[1] != len(_bts.FIELDS):
        raise RuntimeError("%s: %d fields per record, expected %d"
                           % (name, df.shape[1], len(_bts.FIELDS)))
    df.columns = _bts.FIELDS
    for c in ("YEAR", "MONTH", "ORIGIN_WAC", "DEST_WAC") + NUM:
        v = pd.to_numeric(df[c].str.strip(), errors="coerce")
        if v.isna().any():
            raise RuntimeError("%s: non-numeric %s in %d records"
                               % (name, c, int(v.isna().sum())))
        df[c] = v.astype("int64")
    for c in ("ORIGIN", "DEST", "CLASS"):
        df[c] = df[c].str.strip()
    return df


def load(force=False):
    """All Data Bank 28IS records for FIRST_MONTH .. newest published month."""
    del USED_RELEASES[:]
    frames = []
    for rel, months in _bts.plan(FIRST_MONTH):
        raw = _bts.read_release(rel, RAW, force=force)
        df = _parse(raw, rel[4])
        df["period"] = (df["YEAR"].astype(str) + "-" +
                        df["MONTH"].astype(str).str.zfill(2))
        have = set(df["period"])
        miss = [m for m in months if m not in have]
        if miss:
            raise RuntimeError("%s: no records for %s" % (rel[4], miss))
        df = df[df["period"].isin(months)]
        print("  %s -> %s..%s (%d records)" % (rel[4], months[0], months[-1], len(df)))
        USED_RELEASES.append({"file": rel[4], "months": [months[0], months[-1]]})
        frames.append(df)
    if not frames:
        raise SystemExit("no T-100 data downloaded")
    return pd.concat(frames, ignore_index=True)


def aggregate(df):
    """monthly dict: (airport, direction, metric) -> {period: value}"""
    df = df[(df["ORIGIN"] != "") & (df["DEST"] != "")]
    us_orig = df["ORIGIN_WAC"] < 100     # WAC first digit 0 = United States
    us_dest = df["DEST_WAC"] < 100

    # The month grid is taken from the whole T-100 file, not from the single
    # segment: a month with no US->MBJ row is a month in which no carrier
    # operated that segment (Apr/May 2020 border closures), i.e. a true zero,
    # not a hole in the source.
    grid = sorted(set(df["period"]))

    out = {}
    for ap in AIRPORTS:
        # US -> airport  (arrivals into the destination)
        inb = df[us_orig & (df["DEST"] == ap)]
        # airport -> US  (departures out of the destination)
        outb = df[us_dest & (df["ORIGIN"] == ap)]
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
    print("== BTS T-100 (Data Bank 28IS) ==")
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
