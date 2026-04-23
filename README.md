# HBO+ - Aplicação de Streaming

Aplicacao de streaming com catalogo de filmes e series via TMDb API, com autenticacao Google OAuth.

## Configuracao do Login com Google

Para que o login com Google funcione, voce precisa configurar um projeto no Google Cloud Console:

### 1. Criar projeto no Google Cloud Console

1. Acesse https://console.cloud.google.com/
2. Clique no seletor de projeto no topo e em "New Project"
3. De um nome ao projeto (ex: "HBO+ Streaming")
4. Clique em "Create"
5. Aguarde alguns segundos e selecione o projeto criado

### 2. Configurar OAuth Consent Screen

Antes de criar as credenciais, voce precisa configurar a tela de consentimento:

1. No menu lateral, va em APIs & Services > OAuth consent screen
2. Escolha "External" (para uso publico) e clique em "Create"
3. Preencha as informacoes:
   - App name: HBO+ Streaming
   - User support email: seu email
   - Developer contact information: seu email
4. Clique em "Save and Continue" (pode pular as outras secoes)
5. Na secao "Scopes", clique em "Add or Remove Scopes" e adicione:
   - openid
   - email
   - profile
6. Clique em "Save and Continue" ate finalizar
7. Clique em "Publish App" (se for teste, pode deixar em modo de teste)

### 3. Criar OAuth Client ID

1. No menu lateral, va em APIs & Services > Credentials
2. Clique em "Create Credentials" > "OAuth client ID"
3. Configure assim:
   - Application type: Web application
   - Name: HBO+ Web Client
4. Em "Authorized JavaScript origins", clique em "Add URI" e adicione:
   - http://localhost:5000 (para desenvolvimento local)
   - https://seu-dominio.com (se tiver dominio em producao)
5. Em "Authorized redirect URIs", clique em "Add URI" e adicione as mesmas URLs:
   - http://localhost:5000
   - https://seu-dominio.com
6. Clique em "Create"
7. Copie o Client ID (sera algo como 123456789-abc...apps.googleusercontent.com)

### 4. Configurar no Backend

No arquivo server.py, substitua o valor padrao:

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "seu-google-client-id")

Voce pode definir como variavel de ambiente ou substituir diretamente:

GOOGLE_CLIENT_ID = "123456789-abcdefghijklmnopqrstuvwxyz.apps.googleusercontent.com"

### 5. Configurar no Frontend

No arquivo index.html, substitua o valor padrao na funcao initGoogleSignIn():

google.accounts.id.initialize({
    client_id: 'seu-google-client-id',
    callback: handleCredentialResponse,
    auto_select: false,
    context: 'signin'
});

### 6. Configurar JWT Secret

No arquivo server.py, configure uma chave secreta forte para JWT:

JWT_SECRET = os.environ.get("JWT_SECRET", "sua-chave-secreta-jwt-em-producao-mude-isso")

Recomenda-se usar uma string longa e aleatoria em producao.

## Instalacao

pip install -r requirements.txt

## Executar

python server.py

O servidor rodara em http://localhost:5000 por padrao.

## Como funciona a autenticacao

1. O usuario clica no avatar na navbar (se nao estiver logado)
2. Abre o modal de login com botao "Entrar com Google"
3. O usuario faz login via Google
4. O Google retorna um token JWT (credential)
5. O frontend envia o token para o backend (/api/auth/google)
6. O backend valida o token com Google
7. O backend cria um token de sessao JWT e retorna
8. O frontend salva o token no localStorage
9. A UI e atualizada com as informacoes do usuario

## Variaveis de Ambiente (Opcional)

Voce pode definir estas variaveis de ambiente em vez de modificar o codigo:

export GOOGLE_CLIENT_ID="seu-google-client-id"
export JWT_SECRET="sua-chave-secreta-jwt"
export PORT=5000

## Importante

- Em producao, use HTTPS obrigatoriamente
- Configure corretamente as Authorized JavaScript origins no Google Cloud
- Use uma JWT_SECRET forte e segura
- Nunca commit credenciais no repositorio
