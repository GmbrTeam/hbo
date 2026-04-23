import requests

def buscar():
    # --- CONFIGURAÇÃO ---
    api_key = "aa315849db169c9aff378a6b27389a3a"
    url_tmdb = "https://api.themoviedb.org/3/search/multi"
    
    nome = input("\n🎥 Digite o nome do filme ou série: ").strip()
    
    params = {
        "api_key": api_key,
        "query": nome,
        "language": "pt-BR",
        "include_adult": "false"
    }

    try:
        # PASSO 1: Buscar o ID no TMDb (API de verdade)
        response = requests.get(url_tmdb, params=params, timeout=10)
        
        if response.status_code == 200:
            dados = response.json()
            
            if dados.get('results'):
                print("\n" + "—"*50)
                for res in dados['results'][:3]:
                    id_tmdb = res.get('id')
                    tipo = res.get('media_type', 'movie') # Identifica se é 'movie' ou 'tv'
                    titulo = res.get('title') or res.get('name')
                    ano = (res.get('release_date') or res.get('first_air_date') or "----")[:4]

                    # PASSO 2: Montar a URL do vidsrc conforme a documentação
                    if tipo == "tv":
                        # Padrão Série/Episódio: /embed/tv/{id}/1-1
                        link = f"https://vidsrc-embed.ru/embed/tv/{id_tmdb}/1-1"
                    else:
                        # Padrão Filme: /embed/movie/{id}
                        link = f"https://vidsrc-embed.ru/embed/movie/{id_tmdb}"
                    
                    print(f"✅ {titulo} ({ano})")
                    print(f"   ID TMDB: {id_tmdb}")
                    print(f"   PLAYER:  {link}\n")
                print("—"*50)
            else:
                print("\n[-] Nenhum resultado encontrado no TMDb.")
        else:
            print(f"\n[!] Erro na API TMDb: Status {response.status_code}")

    except Exception as e:
        print(f"\n[!] Erro técnico: {e}")

if __name__ == "__main__":
    buscar()
