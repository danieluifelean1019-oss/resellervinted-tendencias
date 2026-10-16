#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Genera public/trends.json con:
  - Tendencias generales (búsqueda por relevancia)
  - "Tendencia rápida": artículos RECIÉN subidos, con MUCHOS me gusta y
    con los filtros de la watchlist (categoría, precio mínimo, me gusta).

Lee la búsqueda pública de Vinted a través de un lector intermedio
(r.jina.ai) porque Vinted bloquea el acceso directo desde servidores.
NO se saltan protecciones: se usa la web pública tal cual.
"""
import json
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ------------------------------------------------------------------
# CONFIGURACIÓN
# ------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
OUT = BASE_DIR / "public" / "trends.json"
WATCHLIST_FILE = BASE_DIR / "watchlist.json"

# Términos para las "Tendencias" generales
TERMS = [
    "nike air force 1",
    "adidas samba",
    "new balance 530",
    "asics gel kayano 14",
    "nike p-6000",
    "carhartt",
]

# Grupos por defecto si no existe watchlist.json
DEFAULT_GROUPS = [
    {"nombre": "Zapatillas", "icono": "fa-shoe-prints", "min_precio": 55, "min_likes": 20, "pref_likes": 50,
     "terminos": ["asics gel", "nike air force 1", "new balance 530", "adidas samba", "jordan 4"],
     "requerir": ["zapatilla", "sneaker", "samba", "gel", "air force", "530", "jordan", "dunk", "trainer",
                  "shoes", "basket", "yeezy", "forum", "campus"],
     "excluir": ["mochila", "bag", "gorra", "calcetin", "sock", "pantalon", "pants", "short", "camiseta",
                 "t-shirt", "chaqueta", "jacket", "sudadera", "hoodie"]},
    {"nombre": "Chaquetas de invierno", "icono": "fa-vest", "min_precio": 45, "min_likes": 20, "pref_likes": 50,
     "terminos": ["the north face", "north face nuptse", "moncler", "patagonia"],
     "requerir": ["chaqueta", "jacket", "parka", "pluma", "puffer", "gilet", "nuptse", "abrigo", "coat",
                  "giubbotto", "doudoune", "windbreaker", "chaqueton", "down"],
     "excluir": ["mochila", "bag", "gorra", "calcetin", "sock", "pantalon", "pants", "short", "sudadera",
                 "hoodie", "zapatilla", "sneaker", "camiseta", "t-shirt"]},
    {"nombre": "Sudaderas", "icono": "fa-shirt", "min_precio": 45, "min_likes": 20, "pref_likes": 50,
     "terminos": ["sudadera", "hoodie", "nike tech fleece"],
     "requerir": ["sudadera", "hoodie", "hoody", "sweatshirt", "tech fleece", "crewneck", "pullover", "sweater"],
     "excluir": ["pantalon", "pants", "mochila", "bag", "gorra", "calcetin", "sock", "short", "chaqueta",
                 "jacket", "zapatilla", "sneaker", "camiseta", "t-shirt"]},
]

MAX_PER_TERM = 2          # artículos por término en "Tendencias"
HOT_PER_GROUP = 6         # máximo por grupo en "Tendencia rápida"
PER_GROUP_CHECKS = 7      # a cuántos por grupo se comprueba la antigüedad
RECENT_MAX_HOURS = 168    # "recién subido" hasta 7 días
RECENT_RELAX_HOURS = 336  # si hay pocos, relajar a 14 días

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
    "Accept": "*/*",
}


def fetch(path, order=None, retries=2):
    """Descarga el HTML (crudo) de una URL de Vinted a través del lector."""
    url = path
    if order:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}order={order}"
    req = urllib.request.Request("https://r.jina.ai/" + url,
                                 headers={**HEADERS, "x-return-format": "html"})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=75) as r:
                return r.read().decode("utf-8", "replace")
        except Exception:
            if attempt == retries:
                raise
            time.sleep(2)


def _find_array(html, key='"items":{"items":['):
    h = html.replace('\\"', '"')
    s = h.find(key)
    if s < 0:
        return []
    s += len(key) - 1
    depth = 0
    for i in range(s, len(h)):
        c = h[i]
        if c == '[':
            depth += 1
        elif c == ']':
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(h[s:i + 1])
                except Exception:
                    return []
    return []


def to_num(amount):
    try:
        return float(amount)
    except Exception:
        return None


def norm(s):
    """Minúsculas y sin acentos, para comparar palabras."""
    s = unicodedata.normalize("NFKD", (s or "").lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def matches(title, requerir, excluir):
    t = norm(title)
    if requerir and not any(k in t for k in requerir):
        return False
    if excluir and any(k in t for k in excluir):
        return False
    return True


def parse_catalog(html, term):
    out = []
    for box in _find_array(html):
        it = box.get("productItem")
        if not it:
            continue
        amount = (it.get("price") or {}).get("amount")
        num = to_num(amount)
        brand = ""
        ib = it.get("itemBox") or {}
        if ib.get("firstLine"):
            brand = ib["firstLine"]
        out.append({
            "id": it.get("id"),
            "titulo": it.get("title", ""),
            "marca": brand,
            "precio": (f"{num:.2f}".replace(".", ",") + "€") if num is not None else "",
            "precio_num": num,
            "likes": int(it.get("favouriteCount") or 0),
            "imagen": it.get("thumbnailUrl") or "",
            "url": "https://www.vinted.es" + (it.get("url") or ""),
            "busqueda": term,
        })
    return out


MONTHS_RE = re.compile(
    r"hace\s+(un|una|unos|unas|\d+)\s*(minutos?|horas?|d[ií]as?|semanas?|mes(?:es)?|a[ñn]os?)",
    re.I,
)


def recency_hours(txt):
    """Convierte 'hace 11 horas' en horas aproximadas. None si no se sabe."""
    if not txt:
        return None
    m = MONTHS_RE.match(txt)
    if not m:
        return None
    q = m.group(1).lower()
    qty = 1 if q in ("un", "una", "unos", "unas") else int(q)
    u = m.group(2).lower()
    if u.startswith("minuto"):
        return qty / 60
    if u.startswith("hora"):
        return qty
    if u.startswith("d"):
        return qty * 24
    if u.startswith("semana"):
        return qty * 24 * 7
    if u.startswith("mes"):
        return qty * 24 * 30
    if u.startswith("a"):
        return qty * 24 * 365
    return None


def get_recency(item_url):
    """Devuelve el texto 'hace X' de la página del artículo (o '' si no se puede)."""
    try:
        m = MONTHS_RE.search(fetch(item_url))
        if m:
            return f"hace {m.group(1)} {m.group(2).lower()}"
    except Exception:
        pass
    return ""


def load_groups():
    if WATCHLIST_FILE.exists():
        try:
            data = json.loads(WATCHLIST_FILE.read_text(encoding="utf-8"))
            groups = data.get("grupos") if isinstance(data, dict) else data
            if isinstance(groups, list) and groups:
                return groups
        except Exception as e:
            print(f"  Aviso: watchlist.json no válido ({e}); usando la de por defecto")
    WATCHLIST_FILE.write_text(json.dumps({"grupos": DEFAULT_GROUPS}, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    return list(DEFAULT_GROUPS)


def main():
    print("== Tendencias generales ==")
    general, used_terms = [], []
    for term in TERMS:
        try:
            html = fetch("https://www.vinted.es/catalog?search_text=" + urllib.parse.quote(term), order="relevance")
            res = parse_catalog(html, term)[:MAX_PER_TERM]
            if res:
                general.extend(res)
                used_terms.append(term)
                print(f"  OK  {term}: {len(res)}")
            else:
                print(f"  --  {term}: sin resultados")
        except Exception as e:
            print(f"  ERR {term}: {e}")
        time.sleep(1.0)

    print("== Tendencia rapida por grupos (reciente + me gusta) ==")
    groups = load_groups()
    hot_grupos = []
    for g in groups:
        nombre = g.get("nombre", "Grupo")
        icono = g.get("icono", "fa-tag")
        min_precio = g.get("min_precio", 0)
        min_likes = g.get("min_likes", 0)
        requerir = [norm(x) for x in g.get("requerir", [])]
        excluir = [norm(x) for x in g.get("excluir", [])]
        pool = {}
        for term in g.get("terminos", []):
            try:
                html = fetch("https://www.vinted.es/catalog?search_text=" + urllib.parse.quote(term),
                             order="newest")
                n = 0
                for it in parse_catalog(html, term):
                    if it["likes"] < min_likes:
                        continue
                    if it["precio_num"] is not None and it["precio_num"] < min_precio:
                        continue
                    if not matches(it["titulo"], requerir, excluir):
                        continue
                    if it["url"] in pool:
                        continue
                    it["grupo"] = nombre
                    it["grupo_icono"] = icono
                    pool[it["url"]] = it
                    n += 1
                print(f"  [{nombre}] {term}: {n}")
            except Exception as e:
                print(f"  ERR [{nombre}] {term}: {e}")
            time.sleep(1.0)

        # Ordenados por me gusta -> los de +50 salen primero (preferencia automática)
        candidates = sorted(pool.values(), key=lambda x: x["likes"], reverse=True)[:PER_GROUP_CHECKS]
        for it in candidates:
            it["subido"] = get_recency(it["url"])
            time.sleep(0.8)

        def hrs(it):
            return recency_hours(it.get("subido", ""))

        recent = [it for it in candidates if (hrs(it) is not None and hrs(it) <= RECENT_MAX_HOURS)]
        if len(recent) < 4:
            recent = [it for it in candidates if (hrs(it) is not None and hrs(it) <= RECENT_RELAX_HOURS)]
        if len(recent) < 4:
            recent = candidates

        picks = recent[:HOT_PER_GROUP]
        n50 = sum(1 for it in picks if it["likes"] >= g.get("pref_likes", 50))
        print(f"  -> {nombre}: {len(picks)} artículos ({n50} con 50+ me gusta)")
        if picks:
            hot_grupos.append({
                "nombre": nombre,
                "icono": icono,
                "min_precio": min_precio,
                "min_likes": min_likes,
                "pref_likes": g.get("pref_likes", 50),
                "items": picks,
            })

    hot = [it for grp in hot_grupos for it in grp["items"]]

    if not general and not hot:
        print("No se obtuvo nada. Se conserva el trends.json anterior.")
        return 1

    tz = timezone(timedelta(hours=2))
    data = {
        "actualizado": datetime.now(tz).isoformat(timespec="seconds"),
        "fuente": "Busqueda publica de Vinted (via lector)",
        "terminos": used_terms,
        "grupos": [{"nombre": g.get("nombre"), "icono": g.get("icono", "fa-tag"),
                    "min_precio": g.get("min_precio", 0), "min_likes": g.get("min_likes", 0),
                    "pref_likes": g.get("pref_likes", 50)} for g in groups],
        "items": general[:16],
        "hot_grupos": hot_grupos,
        "hot": hot,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nGuardado: {len(data['items'])} generales + {len(hot)} en tendencia rapida "
          f"({len(hot_grupos)} grupos)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
