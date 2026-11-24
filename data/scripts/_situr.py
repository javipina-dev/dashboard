"""Helper: Excel files of MITUR/SITUR (República Dominicana), with manual capture.

In nov-2026 SITUR moved its downloads from situr.mitur.gob.do to wp-situr.mitur.gob.do
(same path, /wp-content/uploads/estadisticas/<frecuencia>/<título>.xlsx). The old URLs now
return the site's HTML with HTTP 200, and the new host answers 403 to automated requests.

Manual capture (see README, "Limitaciones"): open SITUR_PAGE in a real browser, download the
files listed in FILES and save them, with the names the site gives them, in
data/raw/DO/mitur_manual/. The extractors use them in this order:
  1. data/raw/DO/mitur_manual/<nombre>.xlsx, if it is a real xlsx;
  2. a download from the new host, kept only if it is a real xlsx (if MITUR reopens it);
  3. the previous valid copy in data/raw/DO/, with a warning.
A file that is not an xlsx (an HTML page, a 403) never overwrites the cache. If nothing valid
is found the extractor stops with an error and run_all keeps the previous JSON.
"""
import datetime as dt
import shutil
from pathlib import Path
from urllib.parse import quote

import requests

SITUR_HOST = "https://wp-situr.mitur.gob.do"
SITUR_PAGE = "https://situr.mitur.gob.do/estadisticas/descargas/"
MANUAL = Path(__file__).resolve().parent.parent / "raw" / "DO" / "mitur_manual"
STALE_DAYS = 40

# name on the site (without .xlsx) -> (frecuencia, local cache name)
FILES = {
    "1. Llegadas marítimas": ("mensual", "mitur_llegadas_maritimas.xlsx"),
    "1. Flujo migratorio": ("mensual", "mitur_flujo_migratorio.xlsx"),
    "1. Actividad hotelera - zonas tradicionales": ("mensual", "mitur_actividad_hotelera_tradicionales.xlsx"),
    "2. Actividad hotelera - zonas MITUR": ("mensual", "mitur_actividad_hotelera_zonas_mitur.xlsx"),
    "3. Hoteles y habitaciones hoteleras - zonas MITUR": ("mensual", "mitur_hoteles_habitaciones.xlsx"),
}


def url(title):
    freq = FILES[title][0]
    return f"{SITUR_HOST}/wp-content/uploads/estadisticas/{freq}/{quote(title + '.xlsx')}"


def _is_xlsx(p):
    try:
        with open(p, "rb") as f:
            return f.read(2) == b"PK"
    except OSError:
        return False


def fetch(title, raw, ua, download=True):
    """Path to a valid xlsx for `title`, copied into `raw` under its cache name."""
    local = Path(raw) / FILES[title][1]
    manual = MANUAL / f"{title}.xlsx"
    if _is_xlsx(manual):
        shutil.copyfile(manual, local)
        age = (dt.datetime.now() - dt.datetime.fromtimestamp(manual.stat().st_mtime)).days
        print(f"  manual  {manual.name} (guardado hace {age} días)")
        if age > STALE_DAYS:
            print(f"  ! la captura manual de '{title}' tiene más de {STALE_DAYS} días: renovarla")
        return local
    if manual.exists():
        print(f"  ! {manual.name} no es un xlsx válido; se ignora")
    if download:
        try:
            r = requests.get(url(title), headers=ua, timeout=180)
            if r.status_code == 200 and r.content[:2] == b"PK":
                local.write_bytes(r.content)
                print(f"  downloaded {local.name} ({len(r.content)} bytes)")
                return local
            print(f"  ! SITUR {r.status_code}, no es xlsx: {url(title)}")
        except requests.RequestException as e:
            print(f"  ! {e} {url(title)}")
    if _is_xlsx(local):
        print(f"  ! usando la copia anterior de {local.name}; falta la captura manual")
        return local
    raise RuntimeError(
        f"MITUR/SITUR: no hay un xlsx válido de '{title}'. Descárgalo desde {SITUR_PAGE} "
        f"y guárdalo como {manual} (ver README, Limitaciones).")
