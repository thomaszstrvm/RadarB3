# Frontend Radar B3

React, TypeScript e Vite. A aplicação usa `/api/v1` na mesma origem; o build é servido pelo FastAPI. Recursos, fontes e estilos são locais. `src/tokens.css` preserva o arquivo original fornecido.

```powershell
npm ci
npm test
npm run build
```

Desenvolvimento: `npm run dev` em `127.0.0.1:5173`, com proxy para a API em `127.0.0.1:8000`. Não publica em interfaces externas. O proxy mantém Host e Origin do cliente para a proteção local da API.

Filtros, ordenação, página e seleção são preservados na URL. Abrir páginas só consulta dados. Atualizar cria um job e as execuções mostram o progresso persistido. Operações mutáveis usam token de sessão em memória; não há cópia de credenciais em armazenamento local. Tema é a única preferência salva no localStorage.

Exportar página inclui filtros, origem, corte e declaração do escopo (apenas a página carregada). CSV/JSON de análises são fornecidos pela API. Links de fontes só aceitam HTTP/HTTPS e conteúdo remoto aparece como texto React.

Verificado: build de produção; testes de ausência versus zero, percentual fracionário, fuso de data de sessão, URLs seguras, filtros, gráfico sem interpolação e identificação de revisão. Validação visual e fluxo completo com banco real pertencem à revisão integrada, registrados no relatório principal. Nenhuma alegação de acessibilidade certificada.
