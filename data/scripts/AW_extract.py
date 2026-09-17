"""Aruba (AW): monthly stay-over visitors, avg length of stay and tourism credits
from Centrale Bank van Aruba (CBA) 'Monthly Tables' PDFs, Table 10 'Tourism'.
Site is behind Sucuri CloudProxy (JS cookie challenge) -> solved in _sucuri.py.
Each edition carries ~2-3 years of monthly rows; later editions override earlier ones (revisions)."""
import os, re, json, sys
import pdfplumber
sys.path.insert(0, os.path.dirname(__file__))
import _sucuri as S

BASE = os.path.join(os.path.dirname(__file__), "..")
RAW = os.path.join(BASE, "raw", "AW")
# (period of edition, CBA readBlob id) - ordered oldest -> newest
EDITIONS = [("2020-12", 10005), ("2021-12", 11032), ("2022-12", 13255), ("2023-12", 16483),
            ("2024-12", 17505), ("2025-12", None), ("2026-07", 19203)]
MONTHS = {m: i + 1 for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July",
                                           "August", "September", "October", "November", "December"])}

def fetch(s, tag, bid):
    fn = os.path.join(RAW, f"cba_monthly_tables_{tag}.pdf")
    if not os.path.exists(fn):
        r = S.get(s, f"https://www.cbaruba.org/readBlob.do?id={bid}")
        assert r.content[:4] == b"%PDF", (tag, bid)
        open(fn, "wb").write(r.content)
    return fn

def num(tok):
    tok = tok.replace(",", "")
    return None if tok.lower().startswith("n.a") else float(tok)

def parse(fn):
    out = {}
    with pdfplumber.open(fn) as pdf:
        txt = next(p.extract_text() for p in pdf.pages if (p.extract_text() or "").startswith("TABLE 10: TOURISM"))
    year = None
    for line in txt.splitlines():
        m = re.match(r"^(?:(\d{4})\s+)?([A-Z][a-z]+)\s*(?:\S*\))?\s+([\d,.\sna]+)$", line.strip())
        if not m or m.group(2) not in MONTHS:
            continue
        if m.group(1): year = int(m.group(1))
        toks = m.group(3).split()
        if len(toks) < 14: continue
        vals = [num(t) for t in toks]
        key = f"{year}-{MONTHS[m.group(2)]:02d}"
        out[key] = {"nights": vals[0], "stayover": vals[1], "alos": vals[10],
                    "credits": vals[14] if len(vals) >= 15 else None}
    return out

def main():
    s = S.session()
    merged = {}
    for tag, bid in EDITIONS:
        if bid is None: continue
        for k, v in parse(fetch(s, tag, bid)).items():
            old = merged.get(k, {})
            # keep older non-null credits if newer edition shows n.a.
            if v["credits"] is None and old.get("credits") is not None: v["credits"] = old["credits"]
            merged[k] = v
    ks = sorted(k for k in merged if k >= "2019-01")
    arr = [[k, int(merged[k]["stayover"])] for k in ks if merged[k]["stayover"] is not None]
    alos = [[k, merged[k]["alos"]] for k in ks if merged[k]["alos"] not in (None,) and merged[k]["stayover"]]
    cred = [[k, merged[k]["credits"]] for k in ks if merged[k]["credits"] is not None]
    page = "https://www.cbaruba.org/monthly-tables/"
    def src(title, last, extra=""):
        return {"org": "Centrale Bank van Aruba (CBA); datos de Aruba Tourism Authority / CBS Aruba",
                "title": title, "page_url": page, "file_url": "https://www.cbaruba.org/readBlob.do?id=19203",
                "format": "pdf", "update_frequency": "mensual", "release_lag": "~30-45 días",
                "last_period": last, "retrieved": "2026-09-17",
                "access_method": "Página anual https://www.cbaruba.org/document/monthly-tables-YYYY/ lista PDFs (readBlob.do?id=N); "
                                 "sitio tras Sucuri JS challenge (resolver cookie). Parsear 'TABLE 10: TOURISM' con pdfplumber; "
                                 "cada edición trae ~2-3 años, usar la más reciente para revisiones." + extra}
    series = [
        {"key": "arrivals_stopover", "category": "arrivals", "label": "Llegadas de turistas stopover (visitantes que pernoctan)",
         "unit": "personas", "frequency": "monthly", "data": arr,
         "source": src("Monthly Tables – Table 10: Tourism (col. 2 Total visitors)", arr[-1][0])},
        {"key": "spending_tourism_receipts", "category": "spending", "label": "Ingresos por turismo (créditos turísticos vía bancos cambiarios)",
         "unit": "Afl. millones", "frequency": "monthly", "data": cred,
         "source": src("Monthly Tables – Table 10: Tourism (col. 15 Tourism credits foreign exchange banks, Afl. million)", cred[-1][0])},
        {"key": "spending_avg_stay", "category": "spending", "label": "Estadía promedio de turistas stopover",
         "unit": "noches", "frequency": "monthly", "data": alos,
         "source": src("Monthly Tables – Table 10: Tourism (col. 11 Average stay)", alos[-1][0])},
    ]
    doc = {"id": "AW", "name": "Aruba", "type": "country", "lat": 12.52, "lon": -69.97, "series": series,
           "notes": [
               "Fuente primaria de llegadas: Aruba Tourism Authority (ATA); la CBA las republica en sus Monthly Tables. Cifras de la edición más reciente de CBA sobrescriben ediciones previas (revisiones; p.ej. total 2023 pasó de 1.243.554 a 1.260.402).",
               "Abril-junio 2020: 0 llegadas (cierre de fronteras COVID-19), tal como publica la CBA.",
               "Ingresos turísticos en florines arubeños (Afl.); tipo de cambio fijo 1,79 Afl. por US$ (no convertido aquí). Compilados de transacciones reportadas por bancos cambiarios locales; excluyen cuentas en bancos extranjeros notificadas e intercompañía, por lo que difieren del crédito 'Viajes' de balanza de pagos.",
               "Ingresos turísticos 2026: la CBA los muestra como n.a. en la edición julio-2026; la serie mensual publicada llega a 2025-12. Las Monthly Tables sólo publican esta columna desde 2021-01 (sin mensual 2019-2020 en esta fuente); valores 2021 revisados entre ediciones, se usa la más reciente.",
               "No hay gasto promedio por visitante mensual oficial; ATA/CBS publican encuestas de gasto (Visitor Profile) de forma anual/irregular.",
               "Datos 2026 provisionales. ATA publica comunicados mensuales (gobierno.aw) con ~3-4 semanas de rezago; pueden diferir de la tabla CBA en meses recientes por revisiones — aquí se usa CBA."]}
    json.dump(doc, open(os.path.join(BASE, "AW.json"), "w"), ensure_ascii=False, indent=1)
    print(len(arr), arr[0], arr[-1], len(cred), cred[0], cred[-1], len(alos))

if __name__ == "__main__":
    main()
