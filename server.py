from flask import Flask, jsonify, request, make_response, send_from_directory
from flask_cors import CORS
import requests
import os
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=BASE_DIR, static_url_path='')
CORS(app, resources={r"/api/*": {"origins": "*"}}, supports_credentials=False)

@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return response

API_KEY = "aa315849db169c9aff378a6b27389a3a"
BASE    = "https://api.themoviedb.org/3"
IMG     = "https://image.tmdb.org/t/p/w500"
LANG    = "pt-BR"

GENEROS = {
    "Ação": 28, "Terror": 27, "Comédia": 35, "Drama": 18,
    "Ficção": 878, "Animação": 16, "Documentário": 99,
    "Romance": 10749, "Suspense": 53, "Crime": 80,
}

_cache = {}
CACHE_TTL = 300

def cache_get(key):
    e = _cache.get(key)
    if e and (time.time() - e["ts"]) < CACHE_TTL:
        return e["data"]
    return None

def cache_set(key, data):
    _cache[key] = {"data": data, "ts": time.time()}

def player_urls(tmdb_id, tipo, season=1, ep=1):
    if tipo == "tv":
        return [
            {"label": "Fonte 1", "url": f"https://vidsrc-embed.ru/embed/tv/{tmdb_id}/{season}-{ep}"},
            {"label": "Fonte 2", "url": f"https://moviesapi.club/tv/{tmdb_id}-{season}-{ep}"},
            {"label": "Fonte 3", "url": f"https://multiembed.mov/directstream.php?video_id={tmdb_id}&tmdb=1&s={season}&e={ep}"},
            {"label": "Fonte 4", "url": f"https://vidsrc.win/watch/{tmdb_id}"},
        ]
    return [
        {"label": "Fonte 1", "url": f"https://vidsrc-embed.ru/embed/movie/{tmdb_id}"},
        {"label": "Fonte 2", "url": f"https://moviesapi.club/movie/{tmdb_id}"},
        {"label": "Fonte 3", "url": f"https://multiembed.mov/directstream.php?video_id={tmdb_id}&tmdb=1"},
        {"label": "Fonte 4", "url": f"https://vidsrc.win/watch/{tmdb_id}"},
    ]

def normalizar(res, tipo_override=None):
    tipo = tipo_override or res.get("media_type", "movie")
    if tipo not in ("movie", "tv"):
        return None
    tmdb_id = res.get("id")
    if not tmdb_id:
        return None
    title   = res.get("title") or res.get("name", "Sem título")
    year    = (res.get("release_date") or res.get("first_air_date") or "----")[:4]
    img     = IMG + res["poster_path"] if res.get("poster_path") else ""
    desc    = res.get("overview") or "Sem descrição disponível."
    rating  = "18+" if res.get("adult") else "12+"
    seasons = res.get("number_of_seasons", 1) if tipo == "tv" else None
    return {
        "tmdb_id":  tmdb_id,
        "tipo":     tipo,
        "title":    title,
        "year":     int(year) if year.isdigit() else 0,
        "img":      img,
        "desc":     desc,
        "rating":   rating,
        "player":   player_urls(tmdb_id, tipo)[0]["url"],
        "sources":  player_urls(tmdb_id, tipo),
        "seasons":  seasons,
        "episodes": tipo == "tv",
        "vote":     res.get("vote_average", 0),
    }

def tmdb_get(path, params={}):
    p = {"api_key": API_KEY, "language": LANG, **params}
    try:
        r = requests.get(f"{BASE}{path}", params=p, timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None

def buscar_pagina(endpoint, tipo, pagina=1, extra={}):
    data = tmdb_get(endpoint, {"page": pagina, **extra})
    if not data:
        return []
    return [x for x in (normalizar(r, tipo) for r in data.get("results", [])) if x and x["img"]]


@app.route("/api/check")
def check_player():
    from urllib.parse import unquote as _unquote
    url = _unquote(request.args.get("url", ""))
    if not url:
        return jsonify({"ok": False, "reason": "URL inválida"}), 400
    try:
        r = requests.get(url, timeout=10, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://www.google.com/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        })
        html_lower = r.text.lower()
        error_patterns = [
            "404 not found", "this media is unavailable", "video not found",
            "movie not found", "not found", "unavailable", "no video",
            "error loading", "cannot be played", "media not available",
            "fa-triangle-exclamation",
        ]
        for pattern in error_patterns:
            if pattern in html_lower:
                return jsonify({"ok": False, "reason": pattern})
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "reason": str(e)})


@app.route('/api/<path:path>', methods=['OPTIONS'])
def handle_options(path):
    r = make_response()
    r.headers['Access-Control-Allow-Origin'] = '*'
    r.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
    r.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    return r, 204


@app.route("/api/catalogo")
def catalogo():
    cached = cache_get("catalogo")
    if cached:
        return jsonify(cached)

    secoes = {
        "trending_movies": ("/trending/movie/week", "movie", {}),
        "trending_series": ("/trending/tv/week",    "tv",    {}),
        "populares_movie": ("/movie/popular",        "movie", {}),
        "populares_tv":    ("/tv/popular",           "tv",    {}),
        "top_movie":       ("/movie/top_rated",      "movie", {}),
        "top_tv":          ("/tv/top_rated",         "tv",    {}),
        "lancamentos":     ("/movie/now_playing",    "movie", {}),
        "terror_movie":    ("/discover/movie",       "movie", {"with_genres": 27}),
        "terror_tv":       ("/discover/tv",          "tv",    {"with_genres": 27}),
        "acao":            ("/discover/movie",       "movie", {"with_genres": 28}),
        "sci_fi":          ("/discover/movie",       "movie", {"with_genres": 878}),
        "drama_tv":        ("/discover/tv",          "tv",    {"with_genres": 18}),
        "anime":           ("/discover/tv",          "tv",    {"with_genres": 16, "with_origin_country": "JP"}),
        "crime":           ("/discover/movie",       "movie", {"with_genres": 80}),
    }

    def fetch_secao(key):
        endpoint, tipo, extra = secoes[key]
        pagina = random.randint(1, 3)
        items  = buscar_pagina(endpoint, tipo, pagina, extra)
        random.shuffle(items)
        return key, items[:15]

    resultado = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(fetch_secao, k): k for k in secoes}
        for f in as_completed(futures):
            key, items = f.result()
            resultado[key] = items

    cache_set("catalogo", resultado)
    return jsonify(resultado)


@app.route("/api/buscar")
def buscar():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"error": "Parâmetro 'q' obrigatório."}), 400
    data = tmdb_get("/search/multi", {"query": q, "include_adult": "false"})
    if not data:
        return jsonify([])
    output = [x for x in (normalizar(r) for r in data.get("results", [])) if x and x["img"]]
    return jsonify(output[:12])


@app.route("/api/genero")
def genero():
    nome   = request.args.get("nome", "")
    tipo   = request.args.get("tipo", "movie")
    pagina = random.randint(1, 5)
    genre_id = GENEROS.get(nome)
    if not genre_id:
        return jsonify({"error": "Gênero desconhecido"}), 400
    items = buscar_pagina(f"/discover/{tipo}", tipo, pagina, {"with_genres": genre_id})
    random.shuffle(items)
    return jsonify(items[:20])


@app.route("/api/episodios/<int:tmdb_id>/<int:season>")
def episodios(tmdb_id, season):
    data = tmdb_get(f"/tv/{tmdb_id}/season/{season}")
    if not data:
        return jsonify({"error": "Temporada não encontrada."}), 404
    eps = [{
        "id":       ep.get("episode_number"),
        "title":    ep.get("name", f"Episódio {ep.get('episode_number')}"),
        "desc":     ep.get("overview", ""),
        "duration": f"{ep.get('runtime') or 42} min",
        "player":   player_urls(tmdb_id, "tv", season, ep.get("episode_number"))[0]["url"],
        "sources":  player_urls(tmdb_id, "tv", season, ep.get("episode_number")),
        "img":      IMG + ep["still_path"] if ep.get("still_path") else "",
    } for ep in data.get("episodes", [])]
    return jsonify({"season": season, "episodes": eps})


@app.route("/api/detalhes/<tipo>/<int:tmdb_id>")
def detalhes(tipo, tmdb_id):
    if tipo not in ("movie", "tv"):
        return jsonify({"error": "tipo inválido"}), 400
    data = tmdb_get(f"/{tipo}/{tmdb_id}")
    if not data:
        return jsonify({"error": "Não encontrado"}), 404
    item = normalizar(data, tipo)
    if not item:
        return jsonify({"error": "Erro ao normalizar"}), 500
    return jsonify(item)


@app.route('/')
@app.route('/index.html')
def serve_index():
    return send_from_directory(BASE_DIR, 'index.html')


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, port=port, host='0.0.0.0')