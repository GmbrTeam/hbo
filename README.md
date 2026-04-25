# HBO+ | Plataforma de Streaming

Interface de streaming moderna com catálogo completo de filmes e séries via TMDb API, autenticação Google OAuth e modo infantil com proteção por PIN.

---

## Stack

| Camada | Tecnologia |
|---|---|
| Frontend | HTML5, Tailwind CSS, JavaScript vanilla |
| Backend | Python 3 + Flask |
| Servidor | Gunicorn |
| Banco de dados | PostgreSQL (psycopg2-binary) |
| API de catálogo | TMDb (The Movie Database) |
| Autenticação | Google OAuth 2.0 + JWT |
| Deploy | Render |

---

## Funcionalidades

### Catálogo
- Filmes e séries organizados por seções: Trending, Populares, Top Rated, Lançamentos, por gênero
- Busca em tempo real com dropdown de resultados
- Página de detalhes com banner fullscreen, sinopse, elenco, avaliação e episódios (séries)
- Cards de lançamentos futuros com data em formato brasileiro (dd/mm/aaaa)
- Sinopse sempre preenchida: busca tradução humana pt-BR no TMDb, com fallback para tradução automática do inglês

### Player
- Múltiplas fontes de reprodução (VidSrc, MoviesAPI, MultiEmbed, Rivestream)
- Troca automática de fonte em caso de indisponibilidade
- Suporte a filmes e episódios de séries com seleção de temporada

### Autenticação
- Login com Google OAuth 2.0
- Login/cadastro com email e senha
- Verificação de email por código de 6 dígitos
- Recuperação de senha com código temporário
- Sessão via JWT com validade de 7 dias

### Perfis
- Múltiplos perfis por conta com avatar, nome e PIN de acesso
- Troca de perfil via tela de seleção
- Perfis infantis com restrição de conteúdo

### Modo Kids
- Interface com tema diferenciado (amarelo/laranja, estrelas animadas)
- Catálogo filtrado: apenas animações, família e anime infantil
- Busca restrita ao conteúdo kids
- Saída protegida por PIN de 4 dígitos
- Ativação automática ao selecionar perfil infantil

### UX
- Navegação SPA com `history.pushState` e suporte a botão voltar do browser
- Hero carousel com autoplay e dots de navegação
- Rows horizontais com scroll e setas de navegação
- Skeleton loading em todas as seções
- Toast notifications para feedback de ações
- Design responsivo mobile-first com bottom nav

---

## Estrutura do Projeto

```
/
├── server.py          # Backend Flask: rotas API, autenticação, integração TMDb
├── index.html         # Frontend SPA completo
├── requirements.txt   # Dependências Python
└── Procfile           # Comando de start para o Render
```

---

## Instalação e Execução Local

### Pré-requisitos
- Python 3.10+
- PostgreSQL

### Setup

```bash
# Instalar dependências
pip install -r requirements.txt

# Rodar em desenvolvimento
python server.py
```

A aplicação sobe na porta `9012` por padrão. Acesse `http://localhost:9012`.

### Variáveis de Ambiente

| Variável | Descrição | Padrão |
|---|---|---|
| `DATABASE_URL` | URL de conexão PostgreSQL | — |
| `JWT_SECRET` | Chave para assinar tokens JWT | — |
| `GOOGLE_CLIENT_ID` | Client ID do Google OAuth | — |
| `SMTP_SERVER` | Servidor SMTP para envio de emails | `smtp.gmail.com` |
| `SMTP_PORT` | Porta SMTP | `587` |
| `SMTP_EMAIL` | Email remetente | — |
| `SMTP_PASSWORD` | Senha de app do Gmail | — |
| `PORT` | Porta do servidor (injetada pelo Render) | `9012` |

---

## Deploy no Render

O projeto está configurado para deploy direto no Render via `Procfile`:

```
web: gunicorn server:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120
```

**Passos:**

1. Crie um novo **Web Service** no Render apontando para o repositório
2. Configure as variáveis de ambiente listadas acima em **Environment**
3. Certifique-se de que o **Start Command** está em branco (o Procfile é lido automaticamente) ou defina manualmente o comando acima
4. O banco PostgreSQL pode ser provisionado diretamente no Render; copie a `DATABASE_URL` gerada para as variáveis de ambiente do serviço

---

## API Interna

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/api/catalogo` | Catálogo completo por seções (cache 5 min) |
| `GET` | `/api/kids-catalog` | Catálogo infantil por seções |
| `GET` | `/api/detalhes/{tipo}/{id}` | Detalhes de um título (com fallback de sinopse) |
| `GET` | `/api/episodios/{id}/{season}` | Episódios de uma temporada |
| `GET` | `/api/buscar?q=` | Busca geral |
| `GET` | `/api/kids-search?q=` | Busca restrita ao catálogo kids |
| `GET` | `/api/genero?nome=&tipo=` | Títulos por gênero |
| `POST` | `/api/auth/google` | Login via Google OAuth |
| `POST` | `/api/auth/register` | Cadastro com email/senha |
| `POST` | `/api/auth/login` | Login com email/senha |
| `POST` | `/api/auth/verify-email` | Verificação de código de email |
| `POST` | `/api/auth/recover-password` | Envio de código de recuperação |
| `POST` | `/api/auth/reset-password` | Redefinição de senha |

---

## Gêneros Disponíveis

`Ação` · `Terror` · `Comédia` · `Drama` · `Ficção Científica` · `Animação` · `Documentário` · `Romance` · `Suspense` · `Crime`

---

## Direitos Autorais e Aviso Legal

© 2025 HBO+. Todos os direitos reservados.

Este projeto é de uso **estritamente pessoal e educacional**. É proibida a reprodução, distribuição ou uso comercial sem autorização prévia e expressa do autor.

### Dados de Catálogo

As informações de filmes e séries (títulos, sinopses, imagens, avaliações e datas) são fornecidas pela [TMDb API](https://www.themoviedb.org/) e estão sujeitas aos [Termos de Uso da TMDb](https://www.themoviedb.org/terms-of-use).

> Este produto usa a API TMDb, mas não é endossado ou certificado pela TMDb.

### Conteúdo de Terceiros

Os players de vídeo integrados (VidSrc, MoviesAPI, MultiEmbed, Rivestream) são serviços externos independentes. Este projeto não hospeda, armazena nem distribui qualquer conteúdo audiovisual. Toda a responsabilidade pelo conteúdo reproduzido é dos respectivos provedores.

### Marca

O nome **HBO+** e a identidade visual deste projeto são utilizados apenas para fins demonstrativos e educacionais, sem qualquer vínculo, parceria ou endosso da **Home Box Office, Inc.** ou do grupo **Warner Bros. Discovery**.
