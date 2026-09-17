import json, glob, os, re, sys
REQ_TOP = ["id", "name", "type", "lat", "lon", "series", "notes"]
REQ_S = ["key", "category", "label", "unit", "frequency", "data", "source"]
REQ_SRC = ["org", "title", "page_url", "file_url", "format", "update_frequency", "release_lag", "last_period", "retrieved", "access_method"]
ids = sys.argv[1:]
for f in sorted(glob.glob(os.path.join(os.path.dirname(__file__), "..", "*.json"))):
    d = json.load(open(f)); i = d.get("id")
    if ids and i not in ids: continue
    errs = [k for k in REQ_TOP if k not in d]
    for s in d.get("series", []):
        errs += [f"{s.get('key')}.{k}" for k in REQ_S if k not in s]
        errs += [f"{s.get('key')}.source.{k}" for k in REQ_SRC if k not in s.get("source", {})]
        pat = {"monthly": r"^\d{4}-\d{2}$", "quarterly": r"^\d{4}-Q[1-4]$", "annual": r"^\d{4}(-\d{2})?$"}.get(s.get("frequency"), r".*")
        bad = [p for p, v in s["data"] if not re.match(pat, str(p)) or not isinstance(v, (int, float))]
        if bad: errs.append(f"{s['key']} bad periods/values {bad[:3]}")
        ks = [p for p, _ in s["data"]]
        if ks != sorted(ks) or len(ks) != len(set(ks)): errs.append(f"{s['key']} unsorted/dupes")
        if s["data"] and s["source"].get("last_period") != ks[-1]: errs.append(f"{s['key']} last_period {s['source'].get('last_period')} != {ks[-1]}")
        print(f"  {i} {s['key']:45s} {s['frequency']:9s} {len(ks):3d} {ks[0] if ks else ''}..{ks[-1] if ks else ''} [{s['unit']}]")
    print(os.path.basename(f), "OK" if not errs else errs)
