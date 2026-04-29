from flask import Flask, jsonify, request, make_response, send_from_directory
from flask_cors import CORS
import requests
import os
import random
import time
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from concurrent.futures import ThreadPoolExecutor, as_completed
import jwt
import hashlib
import hmac
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
import re
import psycopg2
from psycopg2.extras import RealDictCursor
import gzip
import json as _json_mod
import urllib.request


# ─────────────────────────────────────────────────────────────────────────────
#  FUTEBOL — WatchFooty API (sem chave, sem CF, requests puro)
#
#  Endpoint principal: GET https://api.watchfooty.st/api/v1/matches/football/live
#  Streams já vêm junto com o match — sem segundo request necessário.
#  URLs de stream: https://sportsembed.su/embed/{id}/{slug}/{source}/{n}
# ─────────────────────────────────────────────────────────────────────────────

FUTEBOL_OK = True

_WF_BASE = "https://api.watchfooty.st/api/v1"


def _wf_get(path: str, timeout: int = 15):
    """GET na WatchFooty API retornando JSON ou None."""
    try:
        r = requests.get(f"{_WF_BASE}{path}", timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[WATCHFOOTY] {path} erro: {e}")
        return None


def _wf_normalize(m: dict) -> dict | None:
    """Converte match WatchFooty → formato do frontend."""
    try:
        title   = m.get("title", "") or ""
        liga    = m.get("league", "") or ""
        sport   = m.get("sport", "football")
        raw     = m.get("status", "pre")
        if raw == "in":
            status = "live"
        elif raw in ("post", "post-pens"):
            status = "finished"
        else:
            status = "upcoming"

        streams_raw = m.get("streams") or []
        streams = []
        seen = set()
        for s in streams_raw:
            url = s.get("url", "")
            if url and url not in seen:
                seen.add(url)
                streams.append({
                    "url":     url,
                    "lang":    s.get("language") or "EN",
                    "hd":      s.get("quality", "").upper() == "HD",
                    "source":  s.get("id") or "",
                    "ads":     bool(s.get("ads")),
                })

        teams      = m.get("teams") or {}
        home       = teams.get("home") or {}
        away       = teams.get("away") or {}
        home_badge = f"https://api.watchfooty.st{home['logoUrl']}" if home.get("logoUrl") else ""
        away_badge = f"https://api.watchfooty.st{away['logoUrl']}" if away.get("logoUrl") else ""

        home_score = m.get("homeScore", -1)
        away_score = m.get("awayScore", -1)
        score = f"{home_score} - {away_score}" if home_score >= 0 else ""

        # Tenta capturar horário de início (campo pode variar conforme a API)
        start_ts = (
            m.get("startTime") or
            m.get("start_time") or
            m.get("kickoff") or
            m.get("matchTime") or
            m.get("date") or
            ""
        )

        return {
            "nome":       title,
            "match_id":   str(m.get("matchId", "")),
            "categoria":  liga,
            "sport":      sport,
            "status":     status,
            "streams":    streams,
            "iframe_url": streams[0]["url"] if streams else "",
            "home_badge": home_badge,
            "away_badge": away_badge,
            "score":      score,
            "start_time": start_ts,
        }
    except Exception as e:
        print(f"[WATCHFOOTY] _normalize erro: {e}")
        return None


def scrape_eventos(query: str) -> list:
    """Busca partidas filtrando por query — live + pre (agendados) do dia."""
    q    = query.strip().lower()
    # Busca todos do dia (inclui pre, in, post)
    data = _wf_get("/matches/football") or []

    resultado = []
    for m in data:
        title = m.get("title", "") or ""
        liga  = m.get("league", "") or ""
        if q and q not in title.lower() and q not in liga.lower():
            continue
        item = _wf_normalize(m)
        if item:
            resultado.append(item)

    ordem = {"live": 0, "upcoming": 1, "finished": 2}
    resultado.sort(key=lambda x: ordem.get(x["status"], 9))
    print(f"[WATCHFOOTY] {len(resultado)} eventos para query='{query}'")
    return resultado


def scrape_canais_futebol() -> list:
    """
    Retorna partidas ao vivo como canais para o frontend.
    Usa /live — streams já vêm junto, sem segundo request.
    """
    data   = _wf_get("/matches/football/live") or []
    canais = []
    for m in data:
        item = _wf_normalize(m)
        if item and item["streams"]:
            canais.append(item)
    print(f"[WATCHFOOTY] {len(canais)} canais ao vivo")
    return canais


def scrape_canais_por_pais() -> dict:
    """
    Agrupa partidas ao vivo por liga/competição.
    Retorna {grupo: [{nome, match_id, iframe_url, streams}]}
    """
    data     = _wf_get("/matches/football/live") or []
    por_grupo: dict = {}
    for m in data:
        item = _wf_normalize(m)
        if not item or not item["streams"]:
            continue
        grupo = item["categoria"].upper() if item["categoria"] else "GERAL"
        por_grupo.setdefault(grupo, []).append(item)
    print(f"[WATCHFOOTY] {sum(len(v) for v in por_grupo.values())} partidas em {len(por_grupo)} grupos")
    return por_grupo


def _wf_get_streams(match_id: str) -> list:
    """
    Busca streams de uma partida pelo match_id.
    Tenta no cache de partidas; se não achar, chama /match/{id}.
    """
    # Tenta achar no cache de canais via detail endpoint
    data = _wf_get(f"/match/{match_id}")
    if not data:
        return []
    item = _wf_normalize({**data, "matchId": match_id})
    return item["streams"] if item else []

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=BASE_DIR, static_url_path='')

# Database configuration
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://hbo_admin:9f8IRMBEkyjDJHOvyJX0PtciKonDbrZZ@dpg-d7l61vugvqtc738br6a0-a.oregon-postgres.render.com/hbo_users")

def get_db_connection():
    try:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
        return conn
    except Exception as e:
        print(f"[DEBUG] Erro ao conectar ao banco: {e}")
        return None

def init_db():
    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id SERIAL PRIMARY KEY,
                        email VARCHAR(255) UNIQUE NOT NULL,
                        password VARCHAR(255) NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cur.execute("""
                    ALTER TABLE users ADD COLUMN IF NOT EXISTS profiles TEXT DEFAULT NULL
                """)
                cur.execute("""
                    ALTER TABLE users ADD COLUMN IF NOT EXISTS name VARCHAR(255) DEFAULT NULL
                """)
                cur.execute("""
                    ALTER TABLE users ADD COLUMN IF NOT EXISTS picture TEXT DEFAULT NULL
                """)
                # Códigos temporários de verificação por e-mail (não retorna para o cliente)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS email_verification_codes (
                        email VARCHAR(255) PRIMARY KEY,
                        code_hash VARCHAR(128) NOT NULL,
                        expires_at BIGINT NOT NULL,
                        attempts INT NOT NULL DEFAULT 0,
                        last_sent_at BIGINT NOT NULL DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                conn.commit()
                print("[DEBUG] Tabela 'users' criada/verificada com sucesso")
        except Exception as e:
            print(f"[DEBUG] Erro ao criar tabela: {e}")
        finally:
            conn.close()

# Initialize database on startup (wrapped so gunicorn doesn't crash if DB is unreachable)
try:
    init_db()
except Exception as _e:
    print(f"[DEBUG] init_db falhou na inicialização: {_e}")

@app.after_request
def add_cors_headers(response):
    print(f"[DEBUG] Requisição: {request.method} {request.path} - Status: {response.status_code}")
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS, HEAD'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    # Bloqueia popups e redirecionamentos abertos por iframes de terceiros
    response.headers['Permissions-Policy'] = 'popup=()'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response

API_KEY = "aa315849db169c9aff378a6b27389a3a"
BASE    = "https://api.themoviedb.org/3"
IMG     = "https://image.tmdb.org/t/p/w500"
LANG    = "pt-BR"
JWT_SECRET = os.environ.get("JWT_SECRET", "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")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "905316006434-vlm47kl9u63anp749u4d34c27cuptq19.apps.googleusercontent.com")

# Configurações SMTP para envio de email
SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_EMAIL = os.environ.get("SMTP_EMAIL", "guiplayboy18@gmail.com")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "rkcu orwa vypn xioo")

GENEROS = {
    "Ação": 28, "Terror": 27, "Comédia": 35, "Drama": 18,
    "Ficção": 878, "Animação": 16, "Documentário": 99,
    "Romance": 10749, "Suspense": 53, "Crime": 80,
}

_cache = {}
CACHE_TTL = 300

def validate_password(password):
    """
    Valida a senha conforme critérios mínimos de segurança:
    - Mínimo 8 caracteres
    - Pelo menos uma letra maiúscula
    - Pelo menos uma letra minúscula
    - Pelo menos um número
    - Pelo menos um caractere especial
    """
    errors = []
    
    if len(password) < 8:
        errors.append("A senha deve ter no mínimo 8 caracteres")
    
    if not re.search(r'[A-Z]', password):
        errors.append("A senha deve conter pelo menos uma letra maiúscula")
    
    if not re.search(r'[a-z]', password):
        errors.append("A senha deve conter pelo menos uma letra minúscula")
    
    if not re.search(r'\d', password):
        errors.append("A senha deve conter pelo menos um número")
    
    if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
        errors.append("A senha deve conter pelo menos um caractere especial (!@#$%^&*(),.?\":{}|<>)")
    
    return errors

def send_verification_email(email, code):
    """Envia email de verificação usando SMTP"""
    if not SMTP_EMAIL or not SMTP_PASSWORD:
        # Nunca exibe o código em logs para evitar vazamento
        print(f"[DEBUG] SMTP não configurado - tentativa de envio para {email}")
        return False

    try:
        msg = MIMEMultipart()
        msg['From'] = SMTP_EMAIL
        msg['To'] = email
        msg['Subject'] = 'Código de Verificação - HBO+'

        body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; background-color: #1a1a2e; color: white; padding: 20px;">
            <div style="max-width: 600px; margin: 0 auto; background-color: #16213e; padding: 30px; border-radius: 10px;">
                <h2 style="color: #9d50bb;">Código de Verificação</h2>
                <p>Seu código de verificação é:</p>
                <div style="background-color: #9d50bb; color: white; font-size: 32px; font-weight: bold; padding: 20px; text-align: center; border-radius: 5px; margin: 20px 0;">
                    {code}
                </div>
                <p>Este código expira em 5 minutos.</p>
                <p style="color: #888; font-size: 12px;">Se você não solicitou este código, ignore este email.</p>
            </div>
        </body>
        </html>
        """

        msg.attach(MIMEText(body, 'html'))

        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_EMAIL, SMTP_PASSWORD)
        server.send_message(msg)
        server.quit()

        print(f"[DEBUG] Email enviado para {email}")
        return True
    except Exception as e:
        print(f"[DEBUG] Erro ao enviar email: {str(e)}")
        return False

def _hash_email_code(email: str, code: str) -> str:
    """
    Hash do código por e-mail com HMAC para evitar armazenamento em claro.
    O segredo vem de JWT_SECRET para manter a implantação simples.
    """
    secret = (JWT_SECRET or "secret").encode("utf-8")
    msg = f"{email.strip().lower()}:{str(code).strip()}".encode("utf-8")
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()

def _issue_login_token(email: str):
    user_data = {
        "sub": email.split("@")[0],
        "email": email,
        "name": email.split("@")[0].capitalize(),
        "picture": "",
        "exp": int(time.time()) + 3600 * 24 * 7  # 7 dias
    }
    return jwt.encode(user_data, JWT_SECRET, algorithm="HS256"), user_data

def _default_name_from_email(email: str) -> str:
    base = (email.split("@")[0] if "@" in email else email).strip()
    if not base:
        return "Usuário"
    return base[:1].upper() + base[1:]

def _get_user_from_db(email: str):
    conn = get_db_connection()
    if not conn:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT email, name, picture FROM users WHERE email = %s", (email,))
            row = cur.fetchone()
        return row
    except Exception as e:
        print(f"[DEBUG] Erro ao buscar usuário no banco: {e}")
        return None
    finally:
        conn.close()

def _issue_login_token_for_user(user_row: dict):
    email = user_row.get("email")
    name = user_row.get("name") or _default_name_from_email(email or "")
    picture = user_row.get("picture") or ""
    user_data = {
        "sub": (email.split("@")[0] if email and "@" in email else email or "user"),
        "email": email,
        "name": name,
        "picture": picture,
        "exp": int(time.time()) + 3600 * 24 * 7
    }
    return jwt.encode(user_data, JWT_SECRET, algorithm="HS256"), user_data

def _upsert_verification_code(email: str, code: str, ttl_seconds: int = 300):
    conn = get_db_connection()
    if not conn:
        return False, "Erro interno ao conectar ao banco"
    now = int(time.time())
    try:
        code_hash = _hash_email_code(email, code)
        expires_at = now + ttl_seconds
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO email_verification_codes (email, code_hash, expires_at, attempts, last_sent_at)
                VALUES (%s, %s, %s, 0, %s)
                ON CONFLICT (email) DO UPDATE
                SET code_hash = EXCLUDED.code_hash,
                    expires_at = EXCLUDED.expires_at,
                    attempts = 0,
                    last_sent_at = EXCLUDED.last_sent_at
            """, (email, code_hash, expires_at, now))
        conn.commit()
        return True, None
    except Exception as e:
        print(f"[DEBUG] Erro ao salvar código de verificação: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        return False, "Erro ao salvar código de verificação"
    finally:
        conn.close()

def _verify_code_from_db(email: str, code: str):
    conn = get_db_connection()
    if not conn:
        return False, "Erro interno ao conectar ao banco"
    now = int(time.time())
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT code_hash, expires_at, attempts FROM email_verification_codes WHERE email = %s", (email,))
            row = cur.fetchone()
            if not row:
                return False, "Código expirado ou inválido"
            if now > int(row["expires_at"]):
                cur.execute("DELETE FROM email_verification_codes WHERE email = %s", (email,))
                conn.commit()
                return False, "Código expirado. Solicite um novo."
            if int(row.get("attempts") or 0) >= 10:
                return False, "Muitas tentativas. Solicite um novo código."

            expected = row["code_hash"]
            got = _hash_email_code(email, code)
            if not hmac.compare_digest(expected, got):
                cur.execute(
                    "UPDATE email_verification_codes SET attempts = attempts + 1 WHERE email = %s",
                    (email,)
                )
                conn.commit()
                return False, "Código inválido"

            cur.execute("DELETE FROM email_verification_codes WHERE email = %s", (email,))
            conn.commit()
            return True, None
    except Exception as e:
        print(f"[DEBUG] Erro ao validar código no banco: {e}")
        return False, "Erro ao validar código"
    finally:
        conn.close()

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
            {"label": "VidSrc",      "url": f"https://vidsrc-embed.ru/embed/tv/{tmdb_id}/{season}-{ep}"},
            {"label": "MoviesAPI",   "url": f"https://moviesapi.club/tv/{tmdb_id}-{season}-{ep}"},
            {"label": "MultiEmbed",  "url": f"https://multiembed.mov/directstream.php?video_id={tmdb_id}&tmdb=1&s={season}&e={ep}"},
            {"label": "VidSrc Win",  "url": f"https://vidsrc.win/watch/{tmdb_id}"},
            {"label": "Rivestream",  "url": f"https://rivestream.org/embed?type=tv&id={tmdb_id}&season={season}&episode={ep}"},
        ]
    return [
        {"label": "VidSrc",      "url": f"https://vidsrc-embed.ru/embed/movie/{tmdb_id}"},
        {"label": "MoviesAPI",   "url": f"https://moviesapi.club/movie/{tmdb_id}"},
        {"label": "MultiEmbed",  "url": f"https://multiembed.mov/directstream.php?video_id={tmdb_id}&tmdb=1"},
        {"label": "VidSrc Win",  "url": f"https://vidsrc.win/watch/{tmdb_id}"},
        {"label": "Rivestream",  "url": f"https://rivestream.org/embed?type=movie&id={tmdb_id}"},
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
    desc    = res.get("overview") or ""
    rating  = "18+" if res.get("adult") else "12+"
    seasons = res.get("number_of_seasons", 1) if tipo == "tv" else None
    from datetime import date as _date
    release_str = res.get("release_date") or res.get("first_air_date") or ""
    try:
        released = _date.fromisoformat(release_str) <= _date.today() if release_str else True
    except:
        released = True
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
        "released": released,
        "release_date": release_str,
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

def tmdb_get_en(path, params={}):
    """Busca na API TMDb sem forçar pt-BR (retorna inglês como fallback)."""
    p = {"api_key": API_KEY, **params}
    try:
        r = requests.get(f"{BASE}{path}", params=p, timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None

def traduzir_para_pt(texto):
    """
    Traduz texto do inglês para pt-BR usando a API pública do Google Translate
    (endpoint não-oficial, sem chave). Retorna o texto original se falhar.
    """
    if not texto:
        return texto
    try:
        url = "https://translate.googleapis.com/translate_a/single"
        params = {
            "client": "gtx",
            "sl": "en",
            "tl": "pt-BR",
            "dt": "t",
            "q": texto,
        }
        r = requests.get(url, params=params, timeout=8)
        if r.status_code == 200:
            data = r.json()
            # Resposta é lista de listas: [[["texto traduzido", "original", ...], ...], ...]
            traduzido = "".join(
                part[0] for part in data[0] if part and part[0]
            )
            if traduzido:
                return traduzido
    except Exception as e:
        print(f"[TRANSLATE] Falha ao traduzir: {e}")
    return texto  # retorna original se falhar

def resolver_desc(tmdb_id, tipo):
    """
    Tenta obter sinopse para um item sem descrição em pt-BR:
    1. Endpoint /translations do TMDb (sinopse humana em pt-BR)
    2. Busca direta em inglês + tradução automática
    3. Fallback: sinopse em inglês sem tradução
    """
    # 1. Tentar tradução humana pt-BR via TMDb /translations
    trans_data = tmdb_get_en(f"/{tipo}/{tmdb_id}/translations")
    if trans_data:
        translations = trans_data.get("translations", [])
        pt_trans = next(
            (t for t in translations if t.get("iso_639_1") == "pt" and t.get("iso_3166_1") == "BR"),
            None
        ) or next(
            (t for t in translations if t.get("iso_639_1") == "pt"),
            None
        )
        if pt_trans:
            overview = pt_trans.get("data", {}).get("overview", "")
            if overview:
                return overview

    # 2. Buscar em inglês e traduzir automaticamente
    en_data = tmdb_get_en(f"/{tipo}/{tmdb_id}")
    if en_data and en_data.get("overview"):
        return traduzir_para_pt(en_data["overview"])

    return "Sem descrição disponível."

def resolver_desc_episodio(tmdb_id, season, episode_number):
    """
    Tenta obter sinopse de um episódio sem descrição em pt-BR:
    1. Endpoint /translations do TMDb (sinopse humana em pt-BR)
    2. Busca direta em inglês + tradução automática
    3. Fallback: string vazia (sem descrição)
    """
    # 1. Tentar tradução humana pt-BR via TMDb /translations
    trans_data = tmdb_get_en(f"/tv/{tmdb_id}/season/{season}/episode/{episode_number}/translations")
    if trans_data:
        translations = trans_data.get("translations", [])
        pt_trans = next(
            (t for t in translations if t.get("iso_639_1") == "pt" and t.get("iso_3166_1") == "BR"),
            None
        ) or next(
            (t for t in translations if t.get("iso_639_1") == "pt"),
            None
        )
        if pt_trans:
            overview = pt_trans.get("data", {}).get("overview", "")
            if overview:
                return overview

    # 2. Buscar em inglês e traduzir automaticamente
    en_data = tmdb_get_en(f"/tv/{tmdb_id}/season/{season}/episode/{episode_number}")
    if en_data and en_data.get("overview"):
        return traduzir_para_pt(en_data["overview"])

    return ""

def buscar_pagina(endpoint, tipo, pagina=1, extra={}):
    data = tmdb_get(endpoint, {"page": pagina, **extra})
    if not data:
        return []

    items = [x for x in (normalizar(r, tipo) for r in data.get("results", [])) if x and x["img"]]

    # Resolve sinopse para itens sem descrição em pt-BR
    sem_desc = [item for item in items if not item["desc"]]
    if sem_desc:
        def fetch_desc(item):
            item["desc"] = resolver_desc(item["tmdb_id"], item["tipo"])
            return item

        with ThreadPoolExecutor(max_workers=min(len(sem_desc), 8)) as ex:
            futures = {ex.submit(fetch_desc, item): item for item in sem_desc}
            for f in as_completed(futures):
                f.result()

    return items


@app.route('/api/<path:path>', methods=['OPTIONS'])
def handle_options(path):
    print(f"[DEBUG] OPTIONS handler chamado para: {path}")
    r = make_response()
    r.headers['Access-Control-Allow-Origin'] = '*'
    r.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS, POST'
    r.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    print(f"[DEBUG] Retornando 204 para OPTIONS")
    return r, 204


@app.route("/api/auth/google", methods=["POST"])
def auth_google():
    print(f"[DEBUG] auth_google chamado. Method: {request.method}")
    print(f"[DEBUG] Headers: {dict(request.headers)}")
    try:
        data = request.get_json()
        print(f"[DEBUG] Data recebida: {data}")
        token = data.get("token")

        if not token:
            print("[DEBUG] Token não fornecido")
            return jsonify({"error": "Token não fornecido"}), 400

        # Validar token com Google
        print("[DEBUG] Validando token com Google...")
        import time
        print(f"[DEBUG] Hora do servidor: {time.time()}")
        print(f"[DEBUG] Token recebido: {token[:30]}...")

        # Decodificar token sem validar para ver timestamps
        import jwt as pyjwt
        decoded = pyjwt.decode(token, options={"verify_signature": False})
        print("[DEBUG] iat:", decoded.get("iat"))
        print("[DEBUG] nbf:", decoded.get("nbf"))
        print("[DEBUG] exp:", decoded.get("exp"))

        idinfo = id_token.verify_oauth2_token(
            token,
            google_requests.Request(),
            GOOGLE_CLIENT_ID,
            clock_skew_in_seconds=60
        )
        print(f"[DEBUG] Token validado. Info: {idinfo}")

        if idinfo["iss"] not in ["accounts.google.com", "https://accounts.google.com"]:
            print("[DEBUG] Issuer inválido")
            return jsonify({"error": "Token inválido"}), 400

        # Persistir/atualizar dados do usuário no banco (nome/foto)
        email = (idinfo.get("email") or "").strip().lower()
        name = idinfo.get("name") or _default_name_from_email(email)
        picture = idinfo.get("picture") or ""

        conn = get_db_connection()
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT id FROM users WHERE email = %s", (email,))
                    existing_user = cur.fetchone()
                    if existing_user:
                        cur.execute(
                            "UPDATE users SET name = %s, picture = %s WHERE email = %s",
                            (name, picture, email)
                        )
                    else:
                        # Usuário Google não precisa de senha local; usa placeholder não-vazio
                        cur.execute(
                            "INSERT INTO users (email, password, name, picture) VALUES (%s, %s, %s, %s)",
                            (email, "__google_oauth__", name, picture)
                        )
                conn.commit()
            except Exception as e:
                print(f"[DEBUG] Erro ao salvar usuário Google: {e}")
                try:
                    conn.rollback()
                except Exception:
                    pass
            finally:
                conn.close()

        # Criar token JWT de sessão (com nome/foto atuais)
        user_row = _get_user_from_db(email) or {"email": email, "name": name, "picture": picture}
        session_token, user_data = _issue_login_token_for_user(user_row)
        print(f"[DEBUG] Token JWT criado com sucesso")

        return jsonify({
            "success": True,
            "token": session_token,
            "user": {
                "name": user_data["name"],
                "email": user_data["email"],
                "picture": user_data["picture"]
            }
        })
    except Exception as e:
        print(f"[DEBUG] Erro: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 400


@app.route("/api/auth/verify", methods=["GET"])
def verify_auth():
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return jsonify({"authenticated": False}), 401

    token = auth_header.split(" ")[1]
    try:
        decoded = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        email = (decoded.get("email") or "").strip().lower()
        user_row = _get_user_from_db(email) or {}
        name = user_row.get("name") or decoded.get("name")
        picture = user_row.get("picture") or decoded.get("picture")
        return jsonify({
            "authenticated": True,
            "user": {
                "name": name,
                "email": email or decoded.get("email"),
                "picture": picture
            }
        })
    except jwt.ExpiredSignatureError:
        return jsonify({"authenticated": False}), 401
    except jwt.InvalidTokenError:
        return jsonify({"authenticated": False}), 401


@app.route("/api/auth/login", methods=["POST", "OPTIONS"])
def auth_login():
    """Login direto com email+senha — sem código de verificação."""
    if request.method == "OPTIONS":
        return "", 200
    try:
        data = request.get_json()
        email = data.get("email", "").strip()
        password = data.get("password", "")

        if not email or not password:
            return jsonify({"error": "Email e senha são obrigatórios"}), 400

        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Erro interno ao conectar ao banco"}), 500

        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id, password, name, picture FROM users WHERE email = %s", (email,))
                user = cur.fetchone()
        finally:
            conn.close()

        if not user:
            return jsonify({"error": "E-mail não cadastrado"}), 401

        if user["password"] != password:
            return jsonify({"error": "Senha incorreta"}), 401

        # Garante nome persistido
        name = user.get("name") or _default_name_from_email(email)
        picture = user.get("picture") or ""
        # Atualiza o nome padrão no banco se ainda não existir
        if not user.get("name"):
            conn2 = get_db_connection()
            if conn2:
                try:
                    with conn2.cursor() as cur:
                        cur.execute("UPDATE users SET name = %s WHERE email = %s", (name, email))
                    conn2.commit()
                except Exception:
                    try:
                        conn2.rollback()
                    except Exception:
                        pass
                finally:
                    conn2.close()

        token, user_data = _issue_login_token_for_user({"email": email, "name": name, "picture": picture})
        print(f"[DEBUG] Login direto: {email}")
        return jsonify({
            "success": True,
            "token": token,
            "user": {"name": user_data["name"], "email": email, "picture": user_data["picture"]}
        })
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 400


@app.route("/api/auth/email", methods=["POST", "OPTIONS"])
def auth_email():
    """
    Endpoint legado (compatibilidade).
    Envia código de verificação por e-mail para cadastro/login por e-mail.
    NÃO retorna código nem token equivalente.
    """
    if request.method == "OPTIONS":
        return "", 200

    print(f"[DEBUG] auth_email chamado - Method: {request.method}")
    print(f"[DEBUG] Headers: {dict(request.headers)}")
    try:
        data = request.get_json()
        print(f"[DEBUG] Data recebida: {data}")
        email = data.get("email")
        password = data.get("password")

        if not email or not password:
            return jsonify({"error": "Email e senha são obrigatórios"}), 400

        # Validar senha
        password_errors = validate_password(password)
        if password_errors:
            return jsonify({
                "error": "Senha não atende aos requisitos de segurança",
                "password_errors": password_errors
            }), 400

        # Gerar código e persistir no servidor (nunca retorna ao cliente)
        verification_code = str(random.randint(100000, 999999))
        ok, err = _upsert_verification_code(email, verification_code, ttl_seconds=300)
        if not ok:
            return jsonify({"error": err or "Erro ao gerar código"}), 500

        # Enviar email
        send_verification_email(email, verification_code)

        return jsonify({
            "success": True,
            "message": "Código de verificação enviado para seu email",
            "email": email
        })
    except Exception as e:
        print(f"[DEBUG] Erro no login por email: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 400


@app.route("/api/auth/send-reg-code", methods=["POST", "OPTIONS"])
def send_reg_code():
    """Envia código de verificação para cadastro (somente por e-mail)."""
    if request.method == "OPTIONS":
        return "", 200
    try:
        data = request.get_json() or {}
        email = (data.get("email") or "").strip().lower()
        if not email:
            return jsonify({"error": "Email é obrigatório"}), 400

        verification_code = str(random.randint(100000, 999999))
        ok, err = _upsert_verification_code(email, verification_code, ttl_seconds=300)
        if not ok:
            return jsonify({"error": err or "Erro ao gerar código"}), 500

        send_verification_email(email, verification_code)

        return jsonify({"success": True, "message": "Código enviado para seu email"})
    except Exception as e:
        print(f"[DEBUG] Erro ao enviar código de registro: {e}")
        return jsonify({"error": "Erro ao enviar código"}), 400


@app.route("/api/auth/register", methods=["POST", "OPTIONS"])
def auth_register():
    """Verifica código e cria/atualiza usuário (não retorna código)."""
    if request.method == "OPTIONS":
        return "", 200
    try:
        data = request.get_json() or {}
        email = (data.get("email") or "").strip().lower()
        password = data.get("password") or ""
        code = (data.get("code") or "").strip()

        if not email or not password or not code:
            return jsonify({"error": "Email, senha e código são obrigatórios"}), 400

        password_errors = validate_password(password)
        if password_errors:
            return jsonify({
                "error": "Senha não atende aos requisitos de segurança",
                "password_errors": password_errors
            }), 400

        ok, err = _verify_code_from_db(email, code)
        if not ok:
            return jsonify({"error": err or "Código inválido"}), 400

        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Erro interno ao conectar ao banco"}), 500
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM users WHERE email = %s", (email,))
                existing_user = cur.fetchone()
                name = _default_name_from_email(email)
                if existing_user:
                    cur.execute("UPDATE users SET password = %s, name = COALESCE(name, %s) WHERE email = %s", (password, name, email))
                else:
                    cur.execute("INSERT INTO users (email, password, name) VALUES (%s, %s, %s)", (email, password, name))
            conn.commit()
        except Exception as e:
            print(f"[DEBUG] Erro ao salvar usuário após verificação: {e}")
            conn.rollback()
            return jsonify({"error": "Erro ao criar conta"}), 500
        finally:
            conn.close()

        return jsonify({"success": True, "message": "Conta criada com sucesso"})
    except Exception as e:
        print(f"[DEBUG] Erro no registro: {e}")
        return jsonify({"error": "Erro ao criar conta"}), 400


@app.route("/api/auth/verify-email", methods=["POST", "OPTIONS"])
def verify_email_and_login():
    """
    Verifica código e retorna sessão (token JWT).
    Útil para fluxo de login/2-step onde a conta já existe.
    """
    if request.method == "OPTIONS":
        return "", 200
    try:
        data = request.get_json() or {}
        email = (data.get("email") or "").strip().lower()
        code = (data.get("code") or "").strip()
        if not email or not code:
            return jsonify({"error": "Email e código são obrigatórios"}), 400

        ok, err = _verify_code_from_db(email, code)
        if not ok:
            return jsonify({"error": err or "Código inválido"}), 400

        user_row = _get_user_from_db(email) or {"email": email, "name": _default_name_from_email(email), "picture": ""}
        token, user_data = _issue_login_token_for_user(user_row)
        return jsonify({
            "success": True,
            "token": token,
            "user": {"name": user_data["name"], "email": user_data["email"], "picture": user_data.get("picture") or ""}
        })
    except Exception as e:
        print(f"[DEBUG] Erro ao verificar email: {e}")
        return jsonify({"error": "Erro ao verificar o código"}), 400


@app.route("/api/auth/verify-code", methods=["POST", "OPTIONS"])
def verify_code():
    """Compatibilidade: antigo /verify-code agora só verifica via DB (sem token)."""
    if request.method == "OPTIONS":
        return "", 200

    print(f"[DEBUG] verify_code chamado")
    try:
        data = request.get_json()
        email = data.get("email")
        code = data.get("code")
        email = (email or "").strip().lower()
        code = (code or "").strip()

        if not email or not code:
            return jsonify({"error": "Email e código são obrigatórios"}), 400

        ok, err = _verify_code_from_db(email, code)
        if not ok:
            return jsonify({"error": err or "Código inválido"}), 400

        user_row = _get_user_from_db(email) or {"email": email, "name": _default_name_from_email(email), "picture": ""}
        session_token, user_data = _issue_login_token_for_user(user_row)
        print(f"[DEBUG] Login realizado com sucesso: {email}")

        return jsonify({
            "success": True,
            "token": session_token,
            "user": {"name": user_data["name"], "email": user_data["email"], "picture": user_data.get("picture") or ""}
        })
    except Exception as e:
        print(f"[DEBUG] Erro na verificação: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 400


@app.route("/api/auth/recovery", methods=["POST"])
def password_recovery():
    print(f"[DEBUG] password_recovery chamado")
    try:
        data = request.get_json()
        email = data.get("email")

        if not email:
            return jsonify({"error": "Email é obrigatório"}), 400

        # Gerar código de recuperação (6 dígitos)
        verification_code = str(random.randint(100000, 999999))

        # Salvar código em memória
        if not hasattr(app, 'recovery_codes'):
            app.recovery_codes = {}

        app.recovery_codes[email] = {
            "code": verification_code,
            "expires": int(time.time()) + 300  # 5 minutos
        }

        # Enviar email
        email_sent = send_verification_email(email, verification_code)

        if not email_sent:
            # Nunca exibe o código em logs para evitar vazamento
            print(f"[DEBUG] Falha ao enviar email de recuperação para {email}")

        return jsonify({
            "success": True,
            "message": "Código de recuperação enviado para seu email",
            "email": email
        })
    except Exception as e:
        print(f"[DEBUG] Erro na recuperação: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 400


@app.route("/api/auth/verify-recovery", methods=["POST"])
def verify_recovery_code():
    print(f"[DEBUG] verify_recovery_code chamado")
    try:
        data = request.get_json()
        email = data.get("email")
        code = data.get("code")

        if not email or not code:
            return jsonify({"error": "Email e código são obrigatórios"}), 400

        if not hasattr(app, 'recovery_codes'):
            return jsonify({"error": "Código expirado ou inválido"}), 400

        stored = app.recovery_codes.get(email)
        if not stored:
            return jsonify({"error": "Código expirado ou inválido"}), 400

        if int(time.time()) > stored["expires"]:
            del app.recovery_codes[email]
            return jsonify({"error": "Código expirado"}), 400

        if stored["code"] != code:
            return jsonify({"error": "Código inválido"}), 400

        # Código válido - buscar senha atual do banco de dados
        conn = get_db_connection()
        current_password = "Nenhuma senha cadastrada"
        
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT password FROM users WHERE email = %s", (email,))
                    user = cur.fetchone()
                    if user:
                        current_password = user['password']
            except Exception as e:
                print(f"[DEBUG] Erro ao buscar senha: {e}")
            finally:
                conn.close()

        return jsonify({
            "success": True,
            "current_password": current_password
        })
    except Exception as e:
        print(f"[DEBUG] Erro na verificação de recuperação: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 400


@app.route("/api/auth/reset-password", methods=["POST"])
def reset_password():
    print(f"[DEBUG] reset_password chamado")
    try:
        data = request.get_json()
        email = data.get("email")
        new_password = data.get("new_password")

        if not email or not new_password:
            return jsonify({"error": "Email e nova senha são obrigatórios"}), 400

        # Validar nova senha
        password_errors = validate_password(new_password)
        if password_errors:
            return jsonify({
                "error": "Senha não atende aos requisitos de segurança",
                "password_errors": password_errors
            }), 400

        # Salvar nova senha no banco de dados
        conn = get_db_connection()
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE users SET password = %s WHERE email = %s",
                        (new_password, email)
                    )
                    conn.commit()
                    print(f"[DEBUG] Senha redefinida no banco para: {email}")
            except Exception as e:
                print(f"[DEBUG] Erro ao redefinir senha: {e}")
                conn.rollback()
            finally:
                conn.close()

        # Limpar código de recuperação
        if hasattr(app, 'recovery_codes') and email in app.recovery_codes:
            del app.recovery_codes[email]

        print(f"[DEBUG] Senha redefinida com sucesso para: {email}")

        return jsonify({
            "success": True,
            "message": "Senha redefinida com sucesso"
        })
    except Exception as e:
        print(f"[DEBUG] Erro ao redefinir senha: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 400


def _get_email_from_token():
    """Extrai o email do Bearer token da requisição. Retorna None se inválido."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    token = auth_header.split(" ")[1]
    try:
        decoded = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return decoded.get("email")
    except Exception:
        return None


@app.route("/api/user/me", methods=["GET", "PUT"])
def user_me():
    email = _get_email_from_token()
    if not email:
        return jsonify({"error": "Não autenticado"}), 401
    email = (email or "").strip().lower()

    if request.method == "GET":
        user_row = _get_user_from_db(email)
        if not user_row:
            return jsonify({"error": "Usuário não encontrado"}), 404
        return jsonify({
            "success": True,
            "user": {
                "email": user_row.get("email") or email,
                "name": user_row.get("name") or _default_name_from_email(email),
                "picture": user_row.get("picture") or ""
            }
        })

    # PUT
    data = request.get_json() or {}
    new_name = (data.get("name") or "").strip()
    new_picture = data.get("picture")

    # Regras simples para evitar lixo
    if new_name and len(new_name) > 80:
        return jsonify({"error": "Nome muito longo"}), 400
    # Observação: foto pode vir como dataURL (base64) e ficar grande.
    # Aceita até ~2MB para suportar sincronização entre dispositivos.
    if new_picture is not None and isinstance(new_picture, str) and len(new_picture) > 2_000_000:
        return jsonify({"error": "Foto muito grande"}), 400

    if not new_name and new_picture is None:
        return jsonify({"error": "Nada para atualizar"}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Erro interno ao conectar ao banco"}), 500

    try:
        fields = []
        params = []
        if new_name:
            fields.append("name = %s")
            params.append(new_name)
        if new_picture is not None:
            fields.append("picture = %s")
            params.append(new_picture.strip() if isinstance(new_picture, str) else "")

        params.append(email)
        with conn.cursor() as cur:
            cur.execute(f"UPDATE users SET {', '.join(fields)} WHERE email = %s", tuple(params))
        conn.commit()
    except Exception as e:
        print(f"[DEBUG] Erro ao atualizar user/me: {e}")
        conn.rollback()
        return jsonify({"error": "Erro ao atualizar dados"}), 500
    finally:
        conn.close()

    user_row = _get_user_from_db(email) or {"email": email, "name": new_name, "picture": new_picture or ""}
    return jsonify({
        "success": True,
        "user": {
            "email": user_row.get("email") or email,
            "name": user_row.get("name") or _default_name_from_email(email),
            "picture": user_row.get("picture") or ""
        }
    })


@app.route("/api/profiles", methods=["GET"])
def get_profiles():
    email = _get_email_from_token()
    if not email:
        return jsonify({"error": "Não autenticado"}), 401
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Erro ao conectar ao banco"}), 500
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT profiles FROM users WHERE email = %s", (email,))
            row = cur.fetchone()
        if row and row["profiles"]:
            import json as _json
            return jsonify({"profiles": _json.loads(row["profiles"])})
        return jsonify({"profiles": None})
    except Exception as e:
        print(f"[DEBUG] Erro ao buscar perfis: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


@app.route("/api/profiles", methods=["POST"])
def save_profiles():
    email = _get_email_from_token()
    if not email:
        return jsonify({"error": "Não autenticado"}), 401
    data = request.get_json()
    profiles_data = data.get("profiles")
    if profiles_data is None:
        return jsonify({"error": "Campo 'profiles' é obrigatório"}), 400
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Erro ao conectar ao banco"}), 500
    try:
        import json as _json
        profiles_json = _json.dumps(profiles_data)
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET profiles = %s WHERE email = %s",
                (profiles_json, email)
            )
        conn.commit()
        print(f"[DEBUG] Perfis salvos para: {email}")
        return jsonify({"success": True})
    except Exception as e:
        print(f"[DEBUG] Erro ao salvar perfis: {e}")
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


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
        # Filmes que AINDA vão lançar (upcoming) — seção separada no home
        "em_breve":        ("/movie/upcoming",      "movie", {}),
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


@app.route("/api/kids-catalog")
def kids_catalog():
    cached = cache_get("kids_catalog")
    if cached:
        return jsonify(cached)

    secoes = {
        "animacoes":      ("/discover/movie", "movie", {"with_genres": 16}),
        "animacoes_tv":   ("/discover/tv",    "tv",    {"with_genres": 16}),
        "familia":        ("/discover/movie", "movie", {"with_genres": 10751}),
        "familia_tv":     ("/discover/tv",    "tv",    {"with_genres": 10751}),
        "anime":          ("/discover/tv",    "tv",    {"with_genres": 16, "with_origin_country": "JP"}),
        "populares_kids": ("/movie/popular",  "movie", {"with_genres": 16}),
    }

    def fetch_secao(key):
        endpoint, tipo, extra = secoes[key]
        pagina = random.randint(1, 3)
        items  = buscar_pagina(endpoint, tipo, pagina, extra)
        random.shuffle(items)
        return key, items[:15]

    resultado = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(fetch_secao, k): k for k in secoes}
        for f in as_completed(futures):
            key, items = f.result()
            resultado[key] = items

    cache_set("kids_catalog", resultado)
    return jsonify(resultado)


@app.route("/api/kids-search")
def kids_search():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])
    data = tmdb_get("/search/multi", {"query": q, "include_adult": "false"})
    if not data:
        return jsonify([])
    # Filtrar apenas animação (16) e família (10751)
    kids_genre_ids = {16, 10751}
    results = []
    for r in data.get("results", []):
        genres = set(r.get("genre_ids", []))
        if genres & kids_genre_ids:
            item = normalizar(r)
            if item and item["img"]:
                results.append(item)
    return jsonify(results[:12])


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

    # Busca também em inglês para usar como fallback de título/desc/thumb
    data_en = tmdb_get_en(f"/tv/{tmdb_id}/season/{season}") or {}
    en_eps_map = {ep.get("episode_number"): ep for ep in data_en.get("episodes", [])}

    def build_ep(ep):
        num   = ep.get("episode_number")
        en_ep = en_eps_map.get(num, {})

        # Título: usa pt-BR se não for genérico, senão fallback inglês
        pt_title = ep.get("name", "").strip()
        en_title = en_ep.get("name", "").strip()
        generic  = f"Episódio {num}"
        if pt_title and pt_title.lower() not in (generic.lower(), f"episode {num}"):
            title = pt_title
        elif en_title:
            title = en_title
        else:
            title = generic

        # Thumbnail: tenta pt-BR, depois en
        still = ep.get("still_path") or en_ep.get("still_path")

        return {
            "id":       num,
            "title":    title,
            "desc":     ep.get("overview", "") or en_ep.get("overview", ""),
            "duration": f"{ep.get('runtime') or en_ep.get('runtime') or 42} min",
            "player":   player_urls(tmdb_id, "tv", season, num)[0]["url"],
            "sources":  player_urls(tmdb_id, "tv", season, num),
            "img":      IMG + still if still else "",
            "_en_desc": en_ep.get("overview", ""),  # guardado para tradução se precisar
        }

    eps = [build_ep(ep) for ep in data.get("episodes", [])]

    # Resolve desc para episódios ainda sem sinopse (traduz en_desc ou busca translations)
    sem_desc = [ep for ep in eps if not ep["desc"]]
    if sem_desc:
        def fetch_ep_desc(ep):
            # Se já temos desc em inglês, só traduz (mais rápido)
            if ep.get("_en_desc"):
                ep["desc"] = traduzir_para_pt(ep["_en_desc"])
            else:
                ep["desc"] = resolver_desc_episodio(tmdb_id, season, ep["id"])
            return ep

        with ThreadPoolExecutor(max_workers=min(len(sem_desc), 8)) as ex:
            futures = {ex.submit(fetch_ep_desc, ep): ep for ep in sem_desc}
            for f in as_completed(futures):
                f.result()

    # Remove campo auxiliar antes de retornar
    for ep in eps:
        ep.pop("_en_desc", None)

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
    # Se sinopse em pt-BR veio vazia, busca tradução
    if not item["desc"]:
        item["desc"] = resolver_desc(tmdb_id, tipo)
    return jsonify(item)


@app.route('/')
@app.route('/index.html')
@app.route('/home')
@app.route('/movies')
@app.route('/series')
@app.route('/genres')
@app.route('/search')
@app.route('/profile')
@app.route('/login')
@app.route('/kids')
@app.route('/futebol')
@app.route('/aovivo')
@app.route('/movie/hbo/<path:slug>')
@app.route('/tv/hbo/<path:slug>')
def serve_index(**kwargs):
    return send_from_directory(BASE_DIR, 'index.html')


@app.route('/api/resolve/<tipo>/<slug>')
def resolve_slug(tipo, slug):
    """
    Resolve um slug no formato '<tmdb_id>-nome-do-titulo' ou apenas '<tmdb_id>'
    e retorna os detalhes do item para o frontend abrir direto.
    """
    if tipo not in ('movie', 'tv'):
        return jsonify({'error': 'tipo inválido'}), 400
    # O slug começa com o tmdb_id (inteiro) seguido de '-' e o nome
    parts = slug.split('-')
    try:
        tmdb_id = int(parts[0])
    except (ValueError, IndexError):
        return jsonify({'error': 'slug inválido'}), 400
    data = tmdb_get(f'/{tipo}/{tmdb_id}')
    if not data:
        return jsonify({'error': 'Não encontrado'}), 404
    item = normalizar(data, tipo)
    if not item:
        return jsonify({'error': 'Erro ao normalizar'}), 500
    if not item['desc']:
        item['desc'] = resolver_desc(tmdb_id, tipo)
    return jsonify(item)


# ─────────────────────────────────────────────
#  FUTEBOL — canais via Playwright (integrado)
# ─────────────────────────────────────────────

# Cache em memória
_football_cache    = None
_football_cache_ts = 0
FOOTBALL_CACHE_TTL = 300   # 5 minutos

_football_all_cache    = None
_football_all_cache_ts = 0

_football_pre_cache    = None
_football_pre_cache_ts = 0

@app.route("/api/football/channels")
def football_channels():
    """
    Roda o scraper e devolve JSON com todos os canais da seção BRASIL.
    Cache de 5 min pra não reabrir o browser a cada clique.
    """
    global _football_cache, _football_cache_ts

    if not FUTEBOL_OK:
        return jsonify({"error": "servico temporariamente indisponivel"}), 503

    if _football_cache and (time.time() - _football_cache_ts) < FOOTBALL_CACHE_TTL:
        return jsonify(_football_cache)

    try:
        canais = scrape_canais_futebol()
        _football_cache    = canais
        _football_cache_ts = time.time()
        return jsonify(canais)
    except Exception as e:
        print(f"[FOOTBALL] Erro: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/football/channels/all")
def football_channels_all():
    """
    Retorna todos os canais agrupados por país:
    { "BRASIL": [...], "ARGENTINA": [...], ... }
    Cache de 5 min compartilhado.
    """
    global _football_all_cache, _football_all_cache_ts

    if not FUTEBOL_OK:
        return jsonify({"error": "servico temporariamente indisponivel"}), 503

    if _football_all_cache and (time.time() - _football_all_cache_ts) < FOOTBALL_CACHE_TTL:
        return jsonify(_football_all_cache)

    try:
        data = scrape_canais_por_pais()
        _football_all_cache    = data
        _football_all_cache_ts = time.time()
        # Atualiza também o cache de BRASIL (compatibilidade)
        global _football_cache, _football_cache_ts
        if "BRASIL" in data:
            _football_cache    = data["BRASIL"]
            _football_cache_ts = time.time()
        return jsonify(data)
    except Exception as e:
        print(f"[FOOTBALL/ALL] Erro: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/football/pre")
def football_pre():
    """
    GET /api/football/pre
    Retorna partidas agendadas (status 'pre') do dia — sem streams necessários.
    Cache de 10 min (jogos pré não mudam rápido).
    """
    global _football_pre_cache, _football_pre_cache_ts

    if not FUTEBOL_OK:
        return jsonify({"error": "servico temporariamente indisponivel"}), 503

    if _football_pre_cache and (time.time() - _football_pre_cache_ts) < 600:
        return jsonify(_football_pre_cache)

    try:
        data = _wf_get("/matches/football") or []
        jogos = []
        for m in data:
            raw = m.get("status", "")
            if raw != "pre":
                continue
            item = _wf_normalize(m)
            if item:
                jogos.append(item)
        # ordena por título
        jogos.sort(key=lambda x: x.get("nome", ""))
        _football_pre_cache    = jogos
        _football_pre_cache_ts = time.time()
        print(f"[FOOTBALL/PRE] {len(jogos)} jogos agendados")
        return jsonify(jogos)
    except Exception as e:
        print(f"[FOOTBALL/PRE] Erro: {e}")
        if _football_pre_cache:
            return jsonify(_football_pre_cache)
        return jsonify({"error": str(e)}), 500


@app.route("/api/football/watch")
def football_watch():
    """
    Retorna iframe_url de um canal específico pelo nome.
    Busca no cache primeiro; se não achar, roda o scraper completo.
    """
    if not FUTEBOL_OK:
        return jsonify({"error": "servico temporariamente indisponivel"}), 503

    nome = request.args.get("nome", "").strip()
    if not nome:
        return jsonify({"error": "Parametro 'nome' obrigatorio"}), 400

    # Tenta achar no cache primeiro
    if _football_cache:
        for c in _football_cache:
            if c["nome"].lower() == nome.lower() and c.get("iframe_url"):
                return jsonify(c)

    # Não achou — roda scraper completo e atualiza cache
    try:
        global _football_cache_ts
        canais = scrape_canais_futebol()
        _football_cache    = canais
        _football_cache_ts = time.time()
        for c in canais:
            if c["nome"].lower() == nome.lower() and c.get("iframe_url"):
                return jsonify(c)
        return jsonify({"nome": nome, "iframe_url": "", "erro": "Canal nao encontrado"})
    except Exception as e:
        print(f"[FOOTBALL] watch erro: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/football/streams")
def football_streams():
    """
    GET /api/football/streams?id=401859367
    Retorna streams de uma partida específica pelo matchId.
    """
    if not FUTEBOL_OK:
        return jsonify({"error": "servico temporariamente indisponivel"}), 503
    match_id = request.args.get("id", "").strip()
    if not match_id:
        return jsonify({"error": "Parametro 'id' obrigatorio"}), 400
    try:
        streams = _wf_get_streams(match_id)
        return jsonify({"match_id": match_id, "streams": streams})
    except Exception as e:
        print(f"[FOOTBALL] streams erro: {e}")
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────────
#  EVENTOS — cache de resultados da streamed.su API
# ─────────────────────────────────────────────

_events_cache     = {}
_events_cache_ts  = {}
EVENTS_CACHE_TTL  = 180  # 3 minutos


@app.route("/api/football/events")
def football_events():
    """
    GET /api/football/events?q=Championship
    Busca eventos em la14hd.com/eventos/ filtrando pela query.
    """
    if not FUTEBOL_OK:
        return jsonify({"error": "servico temporariamente indisponivel"}), 503

    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"error": "Parametro 'q' obrigatorio"}), 400

    now = time.time()
    if q in _events_cache and (now - _events_cache_ts.get(q, 0)) < EVENTS_CACHE_TTL:
        print(f"[EVENTS] Cache hit para '{q}'")
        return jsonify(_events_cache[q])

    try:
        eventos = scrape_eventos(q)
        _events_cache[q]    = eventos
        _events_cache_ts[q] = now
        return jsonify(eventos)
    except Exception as e:
        print(f"[EVENTS] Erro: {e}")
        return jsonify({"error": str(e)}), 500



# ─────────────────────────────────────────────
#  TheSportsDB — proxy de logos de times
# ─────────────────────────────────────────────

_logo_cache = {}
LOGO_CACHE_TTL = 3600  # 1 hora

def _fix_logo_url(url):
    """TheSportsDB às vezes retorna URL sem extensão — garante .png"""
    if not url:
        return url
    if not url.lower().endswith((".png", ".jpg", ".jpeg", ".svg", ".webp")):
        return url + ".png"
    return url


@app.route("/api/football/team-logo")
def team_logo():
    """
    GET /api/football/team-logo?name=Flamengo
    Busca logo do time na TheSportsDB (API pública, sem chave).
    Retorna { name, logo_url } ou { error }.
    Faz cache em memória por 1 hora.
    """
    name = request.args.get("name", "").strip()
    if not name:
        return jsonify({"error": "Parametro 'name' obrigatorio"}), 400

    key = name.lower()
    now = time.time()
    if key in _logo_cache and (now - _logo_cache[key]["ts"]) < LOGO_CACHE_TTL:
        return jsonify(_logo_cache[key]["data"])

    # Endpoints a tentar em ordem
    TSDB_ENDPOINTS = [
        f"https://www.thesportsdb.com/api/v2/json/searchteams/{requests.utils.quote(name)}",
        f"https://www.thesportsdb.com/api/v1/json/3/searchteams.php?t={requests.utils.quote(name)}",
        f"https://www.thesportsdb.com/api/v1/json/1/searchteams.php?t={requests.utils.quote(name)}",
    ]
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

    for endpoint in TSDB_ENDPOINTS:
        try:
            res = requests.get(endpoint, timeout=6, headers=headers)
            if res.status_code == 404:
                continue
            res.raise_for_status()
            data = res.json()
            # v2 retorna {teams:[...]} ou {team:{...}}
            teams = data.get("teams") or []
            if not teams and data.get("team"):
                teams = [data["team"]]
            if teams:
                t = teams[0]
                logo = _fix_logo_url(t.get("strTeamBadge") or t.get("strBadge") or t.get("strTeamLogo") or "")
                result = {"name": name, "logo_url": logo, "team_id": t.get("idTeam")}
                _logo_cache[key] = {"data": result, "ts": now}
                print(f"[LOGO] OK {name} via {endpoint}: {logo[:60] if logo else 'sem logo'}")
                return jsonify(result)
        except Exception as e:
            print(f"[LOGO] {endpoint} falhou: {e}")
            continue

    result = {"name": name, "logo_url": ""}
    _logo_cache[key] = {"data": result, "ts": now}
    return jsonify(result)


@app.route("/api/football/event-logos")
def event_logos():
    """
    GET /api/football/event-logos?event=Flamengo+vs+Palmeiras
    Extrai os dois times do nome do evento e retorna logos de ambos.
    """
    event_name = request.args.get("event", "").strip()
    if not event_name:
        return jsonify({"error": "Parametro 'event' obrigatorio"}), 400

    import re
    sep = re.search(r'\s(?:vs\.?|x|X|-)\s', event_name, re.IGNORECASE)
    if sep:
        team_a = event_name[:sep.start()].strip()
        team_b = event_name[sep.end():].strip()
    else:
        team_a = event_name
        team_b = None

    def get_logo(tname):
        if not tname:
            return None
        key = tname.lower()
        now = time.time()
        if key in _logo_cache and (now - _logo_cache[key]["ts"]) < LOGO_CACHE_TTL:
            return _logo_cache[key]["data"].get("logo_url")
        try:
            url = f"https://www.thesportsdb.com/api/v1/json/3/searchteams.php?t={requests.utils.quote(tname)}"
            res = requests.get(url, timeout=5)
            res.raise_for_status()
            data = res.json()
            teams = data.get("teams") or []
            if teams:
                t = teams[0]
                logo = _fix_logo_url(t.get("strTeamBadge") or t.get("strTeamLogo") or "")
                result = {"name": tname, "logo_url": logo}
            else:
                result = {"name": tname, "logo_url": ""}
            _logo_cache[key] = {"data": result, "ts": now}
            return logo if teams else ""
        except Exception:
            return None

    logo_a = get_logo(team_a)
    logo_b = get_logo(team_b) if team_b else None

    return jsonify({
        "event": event_name,
        "team_a": {"name": team_a, "logo_url": logo_a or ""},
        "team_b": {"name": team_b, "logo_url": logo_b or ""} if team_b else None
    })


# ─────────────────────────────────────────────
#  AO VIVO — canais BR via globetv.app (GitHub)
# ─────────────────────────────────────────────

_GLOBETV_CHANNELS_URL = "https://raw.githubusercontent.com/globetvapp/globetv.app/main/channels.json.gz"
_GLOBETV_CACHE        = None
_GLOBETV_CACHE_TS     = 0
_GLOBETV_CACHE_TTL    = 86400   # 24 horas


def _fetch_gz(url: str):
    """Baixa e descomprime um .json.gz retornando list ou dict."""
    headers = {"User-Agent": "Mozilla/5.0"}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = gzip.decompress(resp.read())
    return _json_mod.loads(data)


_TV_LOGO_BASE    = "https://raw.githubusercontent.com/tv-logo/tv-logos/main/countries/brazil"
_GLOBETV_LOGOS_JSON = "https://raw.githubusercontent.com/globetvapp/globetv.app/refs/heads/main/logos.json"

# Mapeamento manual para canais cujo nome/id não bate com o slug automático
_LOGO_OVERRIDE = {
    "Globo.br":        "globo-br.png",
    "GloboNews.br":    "globonews-br.png",
    "SBT.br":          "sbt-br.png",
    "Record.br":       "record-tv-br.png",
    "Band.br":         "band-br.png",
    "RedeTV.br":       "redetv-br.png",
    "TVCultura.br":    "tv-cultura-br.png",
    "CNNBrasil.br":    "cnn-brasil-br.png",
    "Multishow.br":    "multishow-br.png",
    "GNT.br":          "gnt-br.png",
    "Viva.br":         "viva-br.png",
    "TVGlobo.br":      "globo-br.png",
    "SporTV.br":       "sportv-br.png",
    "SporTV2.br":      "sportv-2-br.png",
    "SporTV3.br":      "sportv-3-br.png",
    "Discovery.br":    "discovery-channel-br.png",
    "Animal.br":       "animal-planet-br.png",
    "History.br":      "history-channel-br.png",
    "NatGeo.br":       "national-geographic-br.png",
    "FoxSports.br":    "fox-sports-br.png",
    "ESPN.br":         "espn-br.png",
    "ESPN2.br":        "espn-2-br.png",
    "ESPN3.br":        "espn-3-br.png",
    "Cartoon.br":      "cartoon-network-br.png",
}

def _canal_logo_slug(name: str) -> str:
    """
    Converte nome do canal para slug do tv-logo/tv-logos.
    Ex: 'TV Cultura' -> 'tv-cultura-br.png'
        'CNN Brasil' -> 'cnn-brasil-br.png'
    """
    import unicodedata
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_name = nfkd.encode("ASCII", "ignore").decode("ASCII")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    return f"{slug}-br.png"


def _fetch_globetv_logos() -> dict:
    """
    Baixa logos.json do globetv (JSON puro, sem gzip).
    Retorna dict {channel_id: url} com URLs públicas (imgur, wikimedia etc).
    """
    try:
        req = urllib.request.Request(
            _GLOBETV_LOGOS_JSON,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            data = _json_mod.loads(r.read())
        # Pega a primeira URL de cada channel (pode ter duplicatas)
        logo_map = {}
        for entry in data:
            cid = entry.get("channel", "")
            url = entry.get("url", "")
            if cid and url and cid not in logo_map:
                logo_map[cid] = url
        # logo_map carregado silenciosamente
        return logo_map
    except Exception as e:
        print(f"[AOVIVO] Falha ao carregar logos.json: {e}")
        return {}


def _build_canais_br():
    """
    Baixa channels + logos do globetv/GitHub, filtra BR e monta lista.
    Prioridade de logo:
      1. Override manual (_LOGO_OVERRIDE) -> tv-logo/tv-logos
      2. logos.json do globetv (imgur/wikimedia, sempre acessível)
      3. Slug automático -> tv-logo/tv-logos (fallback final)
    """
    channels  = _fetch_gz(_GLOBETV_CHANNELS_URL)
    logo_map  = _fetch_globetv_logos()   # {channel_id: url}

    br_channels = []
    for ch in channels:
        country = (
            ch.get("country") or ch.get("country_code") or
            ch.get("countryCode") or ch.get("cc") or ""
        ).upper()
        if country != "BR":
            continue

        cid  = ch.get("id") or ch.get("channel_id") or ch.get("channelId") or ""
        name = ch.get("name") or ch.get("title") or cid
        cats = ch.get("categories") or ch.get("category") or []

        # 1. Override manual -> tv-logo/tv-logos (raw GitHub, sem CORS)
        if cid in _LOGO_OVERRIDE:
            logo = f"{_TV_LOGO_BASE}/{_LOGO_OVERRIDE[cid]}"
        # 2. logos.json do globetv (imgur/wikimedia) — proxiado para evitar CORS/hotlink
        elif cid in logo_map and logo_map[cid]:
            import urllib.parse
            logo = f"/api/logo-proxy?url={urllib.parse.quote(logo_map[cid], safe='')}"
        # 3. Slug automático -> tv-logo/tv-logos (raw GitHub, sem CORS)
        else:
            logo = f"{_TV_LOGO_BASE}/{_canal_logo_slug(name)}"

        br_channels.append({
            "id":         cid,
            "name":       name,
            "logo":       logo,
            "categories": cats if isinstance(cats, list) else [cats],
            "embed_url":  f"https://globetv.app/embed/?cc=BR&cid={cid}&lang=por",
        })

    print(f"[AOVIVO] {len(br_channels)} canais BR carregados")
    return br_channels


@app.route("/api/logo-proxy")
def logo_proxy():
    """
    GET /api/logo-proxy?url=<encoded_url>
    Proxy de imagens para evitar CORS/hotlink block do imgur e outros.
    """
    from flask import Response as FlaskResponse
    import urllib.parse

    raw_url = request.args.get("url", "")
    if not raw_url:
        return "", 400

    try:
        url = urllib.parse.unquote(raw_url)
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://globetv.app/",
        })
        with urllib.request.urlopen(req, timeout=8) as r:
            data        = r.read()
            content_type = r.headers.get("Content-Type", "image/png")
        resp = FlaskResponse(data, status=200, mimetype=content_type)
        resp.headers["Cache-Control"] = "public, max-age=86400"
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp
    except Exception as e:
        return "", 404


@app.route("/api/canais")
def canais_aovivo():
    """
    GET /api/canais
    Retorna lista de canais ao vivo BR com embed_url e logo.
    Cache em memória de 24h — os dados mudam raramente.
    """
    global _GLOBETV_CACHE, _GLOBETV_CACHE_TS

    now = time.time()
    if _GLOBETV_CACHE and (now - _GLOBETV_CACHE_TS) < _GLOBETV_CACHE_TTL:
        return jsonify(_GLOBETV_CACHE)

    try:
        canais = _build_canais_br()
        _GLOBETV_CACHE    = canais
        _GLOBETV_CACHE_TS = now
        return jsonify(canais)
    except Exception as e:
        print(f"[AOVIVO] Erro ao buscar canais: {e}")
        # Se tiver cache antigo, devolve mesmo expirado
        if _GLOBETV_CACHE:
            return jsonify(_GLOBETV_CACHE)
        return jsonify({"error": str(e)}), 500


@app.errorhandler(404)
def not_found(e):
    print(f"[DEBUG] 404 - Path: {request.path} - Method: {request.method}")
    return jsonify({"error": "Not found"}), 404


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 9012))
    app.run(debug=False, port=port, host='0.0.0.0')