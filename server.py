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
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
import re
import psycopg2
from psycopg2.extras import RealDictCursor

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
        print(f"[DEBUG] SMTP não configurado - Código para {email}: {code}")
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

        # Criar token JWT de sessão
        user_data = {
            "sub": idinfo["sub"],
            "email": idinfo["email"],
            "name": idinfo.get("name", ""),
            "picture": idinfo.get("picture", ""),
            "exp": int(time.time()) + 3600 * 24 * 7  # 7 dias
        }

        session_token = jwt.encode(user_data, JWT_SECRET, algorithm="HS256")
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
        return jsonify({
            "authenticated": True,
            "user": {
                "name": decoded.get("name"),
                "email": decoded.get("email"),
                "picture": decoded.get("picture")
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
                cur.execute("SELECT id, password FROM users WHERE email = %s", (email,))
                user = cur.fetchone()
        finally:
            conn.close()

        if not user:
            return jsonify({"error": "E-mail não cadastrado"}), 401

        if user["password"] != password:
            return jsonify({"error": "Senha incorreta"}), 401

        user_data = {
            "sub": email.split("@")[0],
            "email": email,
            "name": email.split("@")[0].capitalize(),
            "picture": "",
            "exp": int(time.time()) + 3600 * 24 * 7
        }
        token = jwt.encode(user_data, JWT_SECRET, algorithm="HS256")
        print(f"[DEBUG] Login direto: {email}")
        return jsonify({
            "success": True,
            "token": token,
            "user": {"name": user_data["name"], "email": email, "picture": ""}
        })
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 400


@app.route("/api/auth/email", methods=["POST", "OPTIONS"])
def auth_email():
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

        # Salvar ou atualizar usuário no banco de dados
        conn = get_db_connection()
        if conn:
            try:
                with conn.cursor() as cur:
                    # Check if user exists
                    cur.execute("SELECT id FROM users WHERE email = %s", (email,))
                    existing_user = cur.fetchone()
                    
                    if existing_user:
                        # Update existing user's password
                        cur.execute(
                            "UPDATE users SET password = %s WHERE email = %s",
                            (password, email)
                        )
                    else:
                        # Insert new user
                        cur.execute(
                            "INSERT INTO users (email, password) VALUES (%s, %s)",
                            (email, password)
                        )
                    conn.commit()
                    print(f"[DEBUG] Usuário salvo no banco: {email}")
            except Exception as e:
                print(f"[DEBUG] Erro ao salvar usuário: {e}")
                conn.rollback()
            finally:
                conn.close()

        # Gerar código de verificação (6 dígitos)
        import random
        verification_code = str(random.randint(100000, 999999))

        # Assinar código em JWT stateless (funciona com múltiplos workers)
        code_payload = {
            "email": email,
            "code": verification_code,
            "exp": int(time.time()) + 300  # 5 minutos
        }
        verification_token = jwt.encode(code_payload, JWT_SECRET, algorithm="HS256")

        # Enviar email real
        email_sent = send_verification_email(email, verification_code)

        if not email_sent:
            print(f"[DEBUG] Código para {email}: {verification_code}")

        return jsonify({
            "success": True,
            "message": "Código de verificação enviado para seu email",
            "email": email,
            "verification_token": verification_token
        })
    except Exception as e:
        print(f"[DEBUG] Erro no login por email: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 400


@app.route("/api/auth/verify-code", methods=["POST", "OPTIONS"])
def verify_code():
    if request.method == "OPTIONS":
        return "", 200

    print(f"[DEBUG] verify_code chamado")
    try:
        data = request.get_json()
        email = data.get("email")
        code = data.get("code")
        verification_token = data.get("verification_token")

        if not email or not code or not verification_token:
            return jsonify({"error": "Email, código e token são obrigatórios"}), 400

        # Validar o JWT do código (stateless — funciona com múltiplos workers)
        try:
            payload = jwt.decode(verification_token, JWT_SECRET, algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Código expirado. Solicite um novo."}), 400
        except Exception:
            return jsonify({"error": "Token de verificação inválido"}), 400

        if payload.get("email") != email:
            return jsonify({"error": "Código inválido"}), 400

        if payload.get("code") != str(code):
            return jsonify({"error": "Código inválido"}), 400

        # Código válido - criar sessão
        user_data = {
            "sub": email.split("@")[0],
            "email": email,
            "name": email.split("@")[0].capitalize(),
            "picture": "",
            "exp": int(time.time()) + 3600 * 24 * 7  # 7 dias
        }

        session_token = jwt.encode(user_data, JWT_SECRET, algorithm="HS256")
        print(f"[DEBUG] Login realizado com sucesso: {email}")

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
            print(f"[DEBUG] Código de recuperação para {email}: {verification_code}")

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


@app.errorhandler(404)
def not_found(e):
    print(f"[DEBUG] 404 - Path: {request.path} - Method: {request.method}")
    return jsonify({"error": "Not found"}), 404


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 9012))
    app.run(debug=False, port=port, host='0.0.0.0')