# ResellerVinted · Tendencias de Vinted

Robot automático que busca en la **búsqueda pública de Vinted** los artículos
recién subidos con más "me gusta" (según la watchlist) y guarda el resultado en
[`public/trends.json`](public/trends.json).

- Se ejecuta **solo, en la nube (GitHub Actions), cada 3 horas**.
- **No depende de que tu ordenador esté encendido.**
- Usa la web pública de Vinted a través de un lector (`r.jina.ai`); **no** se
  saltan protecciones ni se rotan IPs.

## Cómo cambiar lo que se vigila

Edita [`watchlist.json`](watchlist.json) y cambia los grupos
(`min_likes`, `min_precio`, `terminos`, `requerir`, `excluir`).
El cambio se aplica en la siguiente ejecución (o lánzala a mano desde la
pestaña **Actions** → *Run workflow*).

## Ejecutar a mano

Pestaña **Actions** → *Actualizar tendencias de Vinted* → **Run workflow**.
