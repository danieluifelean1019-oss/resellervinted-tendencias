#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Analiza MI STOCK frente al mercado de Vinted.

- Entra con una cuenta "robot" (solo lectura) y lee tu inventario de la nube.
- Coge cada artículo EN STOCK (título + talla), busca similares en Vinted y calcula:
    * nº de anuncios parecidos (competencia)
    * precio mínimo / mediano / máximo del mercado
    * demanda (me gusta)
    * veredicto de tu precio (barato / de mercado / caro)
- Genera un TOP de qué te conviene comprar según demanda, competencia y margen.

Salida: public/stock_analysis.json
Se usa la búsqueda pública de Vinted a través de un lector (r.jina.ai).
"""
import argparse
import json
import os
import re
import statistics
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
OUT = BASE_DIR / "public" / "stock_analysis.json"

PROJECT = "vintedresseller-b204c"
# Clave pública del cliente de Firebase (NO es una contraseña; va en la propia web).
# Se pasa por variable de entorno para no dejarla escrita en el repositorio público.
API_KEY = os.environ.get("FIREBASE_API_KEY", "")
OWNER_EMAIL = os.environ.get("OWNER_EMAIL", "dankeluif@gmail.com")
ROBOT_EMAIL = os.environ.get("ROBOT_EMAIL", "")
ROBOT_PASS = os.environ.get("ROBOT_PASS", "")

MAX_ANALIZAR = 40          # máximo de artículos en stock a analizar por pasada
TOP_N = 8                  # tamaño del top de compra

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
    "Accept": "*/*",
}

# Palabras que no aportan a la hora de comparar anuncios
STOP = {
    "the", "and", "with", "for", "new", "used", "nuevo", "nueva", "nuevos",
    "como", "para", "con", "sin", "los", "las", "del", "por", "muy", "mas",
    "talla", "size", "eu", "uk", "us", "cm", "mm", "uds", "unidades",
    "negro", "negra", "blanco", "blanca", "beige", "azul", "rojo", "roja",
    "verde", "rosa", "gris", "grey", "gray", "marron", "morado", "amarillo",
    "unisex", "hombre", "mujer", "original", "nuevo", "buen", "estado",
}


# ------------------------------------------------------------------
# AUTENTICACIÓN Y LECTURA DEL INVENTARIO
# ------------------------------------------------------------------
def sign_in():
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={API_KEY}"
    body = json.dumps({"email": ROBOT_EMAIL, "password": ROBOT_PASS, "returnSecureToken": True}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)["idToken"]


def fs_val(v):
    if not isinstance(v, dict):
        return None
    if "stringValue" in v:
        return v["stringValue"]
    if "integerValue" in v:
        try:
            return int(v["integerValue"])
        except Exception:
            return None
    if "doubleValue" in v:
        try:
            return float(v["doubleValue"])
        except Exception:
            return None
    if "booleanValue" in v:
        return v["booleanValue"]
    if "nullValue" in v:
        return None
    if "arrayValue" in v:
        return [fs_val(x) for x in v["arrayValue"].get("values", [])]
    return None


def load_inventory(token):
    url = (f"https://firestore.googleapis.com/v1/projects/{PROJECT}"
           f"/databases/(default)/documents/inventories?pageSize=100")
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=40) as r:
        data = json.load(r)

    best = None
    for doc in data.get("documents", []):
        f = doc.get("fields", {})
        owner = fs_val(f.get("ownerEmail"))
        items = f.get("items", {}).get("arrayValue", {}).get("values", [])
        parsed = []
        for v in items:
            m = v.get("mapValue", {}).get("fields", {})
            parsed.append({
                "name": (fs_val(m.get("name")) or "").strip(),
                "size": (fs_val(m.get("size")) or "").strip(),
                "image": fs_val(m.get("image")) or "",
                "costPrice": float(fs_val(m.get("costPrice")) or 0),
                "targetPrice": float(fs_val(m.get("targetPrice")) or 0),
                "quantity": int(fs_val(m.get("quantity")) or 1),
                "soldQty": int(fs_val(m.get("soldQty")) or 0),
                "status": fs_val(m.get("status")) or "stock",
            })
        if owner == OWNER_EMAIL:
            best = parsed
            break
        if owner and (best is None or len(parsed) > len(best)):
            best = parsed
    return best or []


# ------------------------------------------------------------------
# BÚSQUEDA PÚBLICA EN VINTED
# ------------------------------------------------------------------
def fetch(path, retries=3):
    req = urllib.request.Request("https://r.jina.ai/" + path,
                                 headers={**HEADERS, "x-return-format": "html"})
    last = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:
            last = e
            if attempt < retries:
                time.sleep(3 * (attempt + 1))
    raise last


def _find_array(html, key='"items":{"items":['):
    # El HTML viene doblemente escapado: \\ \" \/  deben volver a \ " /
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


def to_num(x):
    try:
        return float(x)
    except Exception:
        return None


def parse_catalog(html):
    out = []
    for box in _find_array(html):
        it = box.get("productItem")
        if not it:
            continue
        num = to_num((it.get("price") or {}).get("amount"))
        ib = it.get("itemBox") or {}
        out.append({
            "titulo": it.get("title", ""),
            "marca": ib.get("firstLine") or "",
            "precio_num": num,
            "likes": int(it.get("favouriteCount") or 0),
            "url": "https://www.vinted.es" + (it.get("url") or ""),
        })
    return out


def buscar(term):
    path = "https://www.vinted.es/catalog?search_text=" + urllib.parse.quote(term)
    try:
        return parse_catalog(fetch(path))
    except Exception as e:
        print(f"      (búsqueda falló: {e})")
        return []


def limpiar_termino(nombre):
    t = nombre
    t = re.sub(r"\b(talla|size|eu|uk|us|cm|mm)\b\.?\s*\d+(\.\d+)?", " ", t, flags=re.I)
    t = re.sub(r"\b\d{2}([.,]5)?\b", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t or nombre


def tokens(texto):
    return [w for w in re.findall(r"[a-z0-9]+", (texto or "").lower())
            if len(w) >= 3 and w not in STOP]


def es_similar(base_tokens, titulo):
    if not base_tokens:
        return True
    comunes = len(set(tokens(titulo)) & set(base_tokens))
    return comunes >= min(2, len(base_tokens))


# ------------------------------------------------------------------
# ANÁLISIS DE UN ARTÍCULO
# ------------------------------------------------------------------
def analizar(item):
    nombre = item["name"]
    base = tokens(nombre)
    term = limpiar_termino(nombre)
    res_all = buscar(term)
    if not res_all and len(base) >= 2:
        res_all = buscar(" ".join(base[:2]))
    if not res_all:
        return None

    similares = [r for r in res_all if es_similar(base, r["titulo"]) and r["precio_num"]]
    otros = [r for r in res_all if r["precio_num"]]
    usar = similares if len(similares) >= 3 else otros
    if not usar:
        return None

    precios = sorted(r["precio_num"] for r in usar)
    likes = [r["likes"] for r in usar]
    mediana = statistics.median(precios)
    p25 = precios[max(0, len(precios) // 4)]
    target = item["targetPrice"]
    likes_med = statistics.median(likes) if likes else 0

    if target <= 0:
        veredicto = "sin precio"
    elif target <= mediana * 0.95:
        veredicto = "barato"
    elif target <= mediana * 1.12:
        veredicto = "de mercado"
    else:
        veredicto = "caro"

    return {
        "nombre": nombre,
        "talla": item["size"],
        "imagen": item["image"],
        "unidades": max(0, item["quantity"] - item["soldQty"]),
        "mi_precio": target,
        "coste": item["costPrice"],
        "mercado": {
            "anuncios": len(similares) or len(usar),
            "vistos": len(res_all),
            "min": round(precios[0], 2),
            "mediana": round(mediana, 2),
            "max": round(precios[-1], 2),
            "p25": round(p25, 2),
            "likes_mediana": round(likes_med, 1),
            "likes_max": max(likes) if likes else 0,
        },
        "veredicto": veredicto,
        "ejemplos": [{"titulo": r["titulo"][:60], "precio": round(r["precio_num"], 2),
                      "likes": r["likes"], "url": r["url"]}
                     for r in sorted(usar, key=lambda x: -x["likes"])[:3]],
    }


def top_compras(analisis):
    """Ranking de qué te conviene comprar: alta demanda y poca competencia."""
    filas = []
    for a in analisis:
        m = a["mercado"]
        med = m["mediana"]
        if med <= 0:
            continue
        demanda = m["likes_mediana"]
        competencia = m["anuncios"]
        score = (demanda + 1) / (1 + competencia / 10.0)
        filas.append({
            "nombre": a["nombre"],
            "score": round(score, 2),
            "demanda": demanda,
            "competencia": competencia,
            "precio_venta_sugerido": round(med, 2),
            "precio_compra_max": round(med * 0.55, 2),
            "motivo": f"{int(demanda)} ❤ de media · {competencia} anuncios parecidos",
        })
    # Quitar duplicados (mismo producto en varias fichas) quedándonos con el mejor
    unicos = {}
    for f in filas:
        k = f["nombre"].strip().lower()
        if k not in unicos or f["score"] > unicos[k]["score"]:
            unicos[k] = f
    filas = sorted(unicos.values(), key=lambda x: -x["score"])
    return filas[:TOP_N]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=MAX_ANALIZAR, help="máximo de artículos a analizar")
    args = ap.parse_args()

    if not ROBOT_EMAIL or not ROBOT_PASS or not API_KEY:
        print("Faltan ROBOT_EMAIL / ROBOT_PASS / FIREBASE_API_KEY")
        return 1

    print("Entrando como cuenta robot…")
    token = sign_in()
    inventario = load_inventory(token)
    if not inventario:
        print("No se encontró inventario.")
        return 0

    en_stock = [i for i in inventario if i["name"] and (i["quantity"] - i["soldQty"]) > 0]
    print(f"Artículos en stock: {len(en_stock)} (de {len(inventario)} totales)")

    analisis = []
    for i, item in enumerate(en_stock[:args.limit], 1):
        try:
            a = analizar(item)
            if a is None:
                a = {
                    "nombre": item["name"], "talla": item["size"], "imagen": item["image"],
                    "unidades": max(0, item["quantity"] - item["soldQty"]),
                    "mi_precio": item["targetPrice"], "coste": item["costPrice"],
                    "mercado": {"anuncios": 0, "vistos": 0, "min": 0, "mediana": 0,
                                "max": 0, "p25": 0, "likes_mediana": 0, "likes_max": 0},
                    "veredicto": "sin datos", "ejemplos": [],
                }
                print(f"  [{i}] {item['name'][:34]:34} -> sin resultados")
            analisis.append(a)
            if a.get("mercado", {}).get("mediana"):
                m = a["mercado"]
                print(f"  [{i}] {item['name'][:34]:34} -> mediana {m['mediana']}€ · "
                      f"{m['anuncios']} similares · {int(m['likes_mediana'])}❤ · {a['veredicto']}")
        except Exception as e:
            print(f"  [{i}] ERROR {item['name'][:30]}: {e}")
        time.sleep(1.0)

    orden = {"caro": 0, "de mercado": 1, "barato": 2, "sin datos": 3}
    data = {
        "actualizado": datetime.now(timezone(timedelta(hours=2))).isoformat(timespec="seconds"),
        "fuente": "Tu inventario (Firestore) + búsqueda pública de Vinted",
        "total_stock": len(en_stock),
        "analizados": len(analisis),
        "articulos": sorted(analisis, key=lambda x: orden.get(x["veredicto"], 9)),
        "top_compras": top_compras(analisis),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nGuardado: {len(analisis)} artículos analizados · top de {len(data['top_compras'])} para comprar")
    return 0


if __name__ == "__main__":
    sys.exit(main())
