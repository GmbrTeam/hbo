# HBO+ - Aplicação de Streaming

Plataforma de streaming com catálogo de filmes e séries via TMDb API, com autenticação Google OAuth.

## Sobre o Projeto

O HBO+ é uma aplicação web de streaming que permite aos usuários:
- Navegar por um catálogo de filmes e séries
- Ver detalhes como sinopse, avaliação e data de lançamento
- Assistir trailers e conteúdo multimídia
- Fazer login com conta Google para salvar preferências
- Interface moderna e responsiva inspirada em grandes plataformas de streaming

## Como Usar

### Acesso

1. Acesse a aplicação através da URL fornecida
2. Para usar todas as funcionalidades, faça login clicando no avatar no canto superior direito
3. Clique em "Entrar com Google" para autenticar sua conta

### Navegação

- **Home**: Mostra os filmes e séries em destaque
- **Busca**: Use a barra de pesquisa para encontrar títulos específicos
- **Detalhes**: Clique em qualquer capa para ver informações completas
- **Player**: Assista trailers e conteúdo multimídia diretamente na plataforma

### Login com Google

O login é necessário para:
- Salvar seus filmes favoritos
- Manter seu histórico de visualização
- Personalizar recomendações

## Modo Kids

O HBO+ possui um **Modo Kids** especial projetado para crianças:

- **Interface Colorida**: Design com cores vibrantes (amarelo, laranja) e animações divertidas
- **Conteúdo Filtrado**: Acesso apenas a conteúdo apropriado para crianças
- **Proteção por PIN**: Para sair do modo kids, é necessário digitar um PIN de 4 dígitos
- **Perfis Infantis**: Crie perfis personalizados para cada criança com avatar, nome e preferências
- **Recomendações**: Sugestões baseadas em programas infantis populares como Patrulha Canina, Peppa Pig, Bluey, Pokémon, e muito mais

### Ativar o Modo Kids

1. Clique no ícone de criança na navbar ou no botão "Modo Kids" nas configurações
2. Se já tiver um perfil kids configurado, ele será ativado automaticamente
3. Para criar um novo perfil kids, use o assistente de configuração de perfil

### Sair do Modo Kids

1. Clique no botão de sair do modo kids
2. Digite o PIN de 4 dígitos configurado
3. O modo será desativado e você voltará à interface normal

## Tecnologias

- **Frontend**: HTML5, CSS3, JavaScript
- **Backend**: Flask (Python)
- **API**: TMDb (The Movie Database)
- **Autenticação**: Google OAuth 2.0
- **Banco de Dados**: PostgreSQL
