#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Genera public/trends.json con:
  - "Tendencias por búsqueda": lo más reciente (recién subido) y con más me gusta
    de cada búsqueda guardada.
  - "Tendencia rápida": por grupos (watchlist), artículos recién subidos, con
    muchos me gusta y que cumplen los filtros (precio mínimo, me gusta, palabras).

Lee la búsqueda PÚBLICA de Vinted a través de un lector intermedio (r.jina.ai)
porque Vinted bloquea el acceso directo desde servidores.
NO se saltan protecciones ni se rotan IPs: se usa la web pública tal cual.

En cada ejecución se eligen artículos DISTINTOS a los de la vez anterior cuando
es posible, para que las tendencias cambien cada 3 horas.
"""
import json
import random
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

# Términos para las "Tendencias por búsqueda"
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
    {"nombre": "Zapatillas", "icono": "fa-shoe-prints", "min_precio": 75, "min_likes": 20, "pref_likes": 50,
     "terminos": ["asics gel", "asics gel kayano", "nike air force 1", "new balance 530", "adidas samba", "jordan 4"],
     "requerir": ["zapatilla", "zapatillas", "deportiva", "deportivas", "sneaker", "sneakers", "samba", "gel",
                  "air force", "530", "jordan", "dunk", "trainer", "trainers", "shoes", "basket", "yeezy",
                  "forum", "campus", "gazelle", "campus"],
     "excluir": ["mochila", "mochilas", "bolso", "bolsos", "bolsa", "backpack", "gorra", "gorras", "calcetin",
                 "calcetines", "sock", "socks", "pantalon", "pantalones", "pants", "shorts", "camiseta",
                 "camisetas", "t-shirt", "t-shirts", "chaqueta", "chaquetas", "jacket", "jackets", "sudadera",
                 "sudaderas", "hoodie", "hoodies"]},
    {"nombre": "Chaquetas de invierno", "icono": "fa-vest", "min_precio": 75, "min_likes": 20, "pref_likes": 50,
     "terminos": ["the north face", "north face nuptse", "moncler", "patagonia", "plumas"],
     "requerir": ["chaqueta", "chaquetas", "jacket", "jackets", "parka", "parkas", "pluma", "plumas", "puffer",
                  "gilet", "nuptse", "abrigo", "abrigos", "coat", "coats", "giubbotto", "doudoune",
                  "windbreaker", "chaqueton", "down"],
     "excluir": ["mochila", "mochilas", "bolso", "bolsos", "bolsa", "backpack", "gorra", "gorras", "calcetin",
                 "calcetines", "sock", "socks", "pantalon", "pantalones", "pants", "shorts", "sudadera",
                 "sudaderas", "hoodie", "hoodies", "zapatilla", "zapatillas", "sneaker", "sneakers", "camiseta",
                 "t-shirt"]},
    {"nombre": "Sudaderas", "icono": "fa-shirt", "min_precio": 75, "min_likes": 20, "pref_likes": 50,
     "terminos": ["sudadera", "hoodie", "nike tech fleece"],
     "requerir": ["sudadera", "sudaderas", "hoodie", "hoodies", "hoody", "sweatshirt", "sweatshirts",
                  "tech fleece", "crewneck", "pullover", "sweater", "sweaters"],
     "excluir": ["pantalon", "pantalones", "pants", "jogging", "jogger", "joggers", "mochila", "mochilas", "bolso", "bolsos", "bolsa",
                 "backpack", "gorra", "gorras", "calcetin", "calcetines", "sock", "socks", "shorts", "chaqueta",
                 "chaquetas", "jacket", "jackets", "zapatilla", "zapatillas", "sneaker", "sneakers", "camiseta",
                 "t-shirt"]},
]

MAX_PER_TERM = 3           # artículos por término en "Tendencias por búsqueda"
NEWEST_POOL = 24           # mirar solo los más nuevos
GEN_QUALITY_POOL = 12      # de esos, elegir entre los 12 con más me gusta
HOT_PER_GROUP = 6          # máximo de artículos por grupo en "Tendencia rápida"
HOT_QUALITY_POOL = 20      # candidatos (por me gusta) entre los que elegir
PER_GROUP_CHECKS = 9       # a cuántos por grupo se comprueba la antigüedad
RECENT_MAX_HOURS = 168     # "recién subido" hasta 7 días
RECENT_RELAX_HOURS = 336   # si hay pocos, relajar a 14 días
STALE_SECONDS = 10800      # 3 h: umbral para la "red de seguridad"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
    "Accept": "*/*",
}


# ------------------------------------------------------------------
# UTILIDADES
# ------------------------------------------------------------------
def fetch(path, order=None, retries=2):
    """Descarga el HTML (crudo) de una URL de Vinted a través del lector."""
    url = path
    if order:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}order={order}"
    req = urllib.request.Request("https://r.jina.ai/" + url,
                                 headers={**HEADERS, "x-return-format": "html"})
    last = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=75) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:
            last = e
            if attempt < retries:
                time.sleep(2)
    raise last


def _find_array(html, key='"items":{"items":['):
    """Extrae y convierte la lista de artículos embebida en el HTML."""
    h = re.sub(r'\\(["\\/])', r'\1', html)
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
                cand = h[s:i + 1]
                for attempt in (cand, cand.replace('$undefined', 'null')):
                    try:
                        return json.loads(attempt)
                    except Exception:
                        continue
                return []
    return []


def to_num(amount):
    try:
        return float(amount)
    except Exception:
        return None


def to_int(value):
    try:
        return int(float(value))
    except Exception:
        return 0


def norm(s):
    """Minúsculas y sin acentos, para comparar palabras."""
    s = unicodedata.normalize("NFKD", (s or "").lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def kw_re(keywords):
    """Compila una lista de palabras a una expresión con límites de palabra."""
    if not keywords:
        return None
    words = "|".join(re.escape(norm(k)) for k in keywords)
    return re.compile(r"\b(?:" + words + r")\b")


def matches(title, req_re, exc_re):
    """True si el título cumple los filtros de palabras (límite de palabra)."""
    t = norm(title)
    if req_re and not req_re.search(t):
        return False
    if exc_re and exc_re.search(t):
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
        ib = it.get("itemBox") or {}
        out.append({
            "id": it.get("id"),
            "titulo": it.get("title", ""),
            "marca": ib.get("firstLine") or "",
            "precio": (f"{num:.2f}".replace(".", ",") + "€") if num is not None else "",
            "precio_num": num,
            "likes": to_int(it.get("favouriteCount")),
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


def load_previous_urls():
    """URLs mostradas la última vez, para intentar no repetir."""
    try:
        d = json.loads(OUT.read_text(encoding="utf-8"))
    except Exception:
        return set()
    urls = set()
    for it in d.get("items", []):
        if it.get("url"):
            urls.add(it["url"])
    for grp in d.get("hot_grupos", []):
        for it in grp.get("items", []):
            if it.get("url"):
                urls.add(it["url"])
    return urls


# ------------------------------------------------------------------
# PRINCIPAL
# ------------------------------------------------------------------
def main(argv=None):
    argv = argv or []
    # Modo "red de seguridad": solo actualiza si los datos ya tienen más de 3 h
    if "--only-if-stale" in argv and OUT.exists():
        age = time.time() - OUT.stat().st_mtime
        if age < STALE_SECONDS:
            print(f"Datos recientes ({age / 3600:.1f} h). No hace falta actualizar.")
            return 0

    prev = load_previous_urls()

    print("== Tendencias por búsqueda (recién subido) ==")
    general, used_terms, seen = [], [], set()
    for term in TERMS:
        try:
            html = fetch("https://www.vinted.es/catalog?search_text=" + urllib.parse.quote(term),
                         order="newest")
            nuevos = parse_catalog(html, term)[:NEWEST_POOL]
            # Candidatos: los 12 con más me gusta de los más nuevos, en orden aleatorio
            quality = sorted(nuevos, key=lambda x: x["likes"], reverse=True)[:GEN_QUALITY_POOL]
            random.shuffle(quality)
            # Preferir los que NO salieron la vez anterior
            pref = [x for x in quality if x["url"] not in prev]
            rest = [x for x in quality if x["url"] in prev]
            res = []
            for it in pref + rest:
                if it["url"] in seen:
                    continue
                seen.add(it["url"])
                res.append(it)
                if len(res) >= MAX_PER_TERM:
                    break
            if res:
                general.extend(res)
                used_terms.append(term)
                print(f"  OK  {term}: {len(res)}")
            else:
                print(f"  --  {term}: sin resultados")
        except Exception as e:
            print(f"  ERR {term}: {e}")
        time.sleep(1.0)

    print("== Tendencia rápida por grupos (recién subido + me gusta) ==")
    groups = load_groups()
    hot_grupos = []
    for g in groups:
        nombre = g.get("nombre", "Grupo")
        icono = g.get("icono", "fa-tag")
        min_precio = g.get("min_precio", 0)
        min_likes = g.get("min_likes", 0)
        req_re = kw_re(g.get("requerir", []))
        exc_re = kw_re(g.get("excluir", []))
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
                    if not matches(it["titulo"], req_re, exc_re):
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

        # De los que más me gusta, comprobar la antigüedad de unos cuantos al azar
        quality = sorted(pool.values(), key=lambda x: x["likes"], reverse=True)[:HOT_QUALITY_POOL]
        random.shuffle(quality)
        candidates = quality[:PER_GROUP_CHECKS]
        for it in candidates:
            it["subido"] = get_recency(it["url"])
            time.sleep(0.8)

        def hrs(x):
            return recency_hours(x.get("subido", ""))

        recent = [it for it in candidates if (hrs(it) is not None and hrs(it) <= RECENT_MAX_HOURS)]
        if len(recent) < 4:
            recent = [it for it in candidates if (hrs(it) is not None and hrs(it) <= RECENT_RELAX_HOURS)]
        if len(recent) < 4:
            recent = candidates

        # Preferir los que NO salieron la vez anterior y ordenar por me gusta
        pref = [it for it in recent if it["url"] not in prev]
        rest = [it for it in recent if it["url"] in prev]
        picks = sorted(pref, key=lambda x: x["likes"], reverse=True)[:HOT_PER_GROUP]
        if len(picks) < HOT_PER_GROUP:
            picks += sorted(rest, key=lambda x: x["likes"], reverse=True)[:HOT_PER_GROUP - len(picks)]

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
        return 0

    tz = timezone(timedelta(hours=2))
    data = {
        "actualizado": datetime.now(tz).isoformat(timespec="seconds"),
        "fuente": "Busqueda publica de Vinted (via lector)",
        "terminos": used_terms,
        "grupos": [{"nombre": g.get("nombre"), "icono": g.get("icono", "fa-tag"),
                    "min_precio": g.get("min_precio", 0), "min_likes": g.get("min_likes", 0),
                    "pref_likes": g.get("pref_likes", 50)} for g in groups],
        "items": general[:21],
        "hot_grupos": hot_grupos,
        "hot": hot,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nGuardado: {len(data['items'])} por búsqueda + {len(hot)} en tendencia rápida "
          f"({len(hot_grupos)} grupos)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
