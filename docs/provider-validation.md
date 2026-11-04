# Validação operacional de provedores — 26/10/2026

Probes executados no Windows de destino entre 21:07 e 21:10 UTC, Python 3.12/httpx do ambiente `.venv`. Sem tokens, cookies de assinatura, cadastro ou contratação. A primeira execução restrita falhou em DNS (`getaddrinfo failed`); a execução com acesso de rede aprovado produziu os resultados abaixo. Isso não autoriza atribuir a falha inicial aos provedores.

## Método reproduzível

Na raiz de `implementation`, executar `../.venv/Scripts/python.exe scripts/probe_providers.py --phase core`, depois `--phase discovery`, `--phase feeds` e `--phase bcb_bounded`. `--source br_uol` limita uma fase a uma fonte. Cada execução gera JSON datado em `data/probes`. São chamadas GET/HEAD públicas, timeout 25 s, teto de 2 MB descomprimidos por resposta, hosts explícitos, até quatro saltos de redirect permitidos, nenhuma repetição automática após 401/429. O script registra links/datas e amostras de dados financeiros públicos; não persiste corpo HTML, resumos editoriais ou matérias integrais. `outcome=success` significa transporte HTTP, não homologação semântica: conferir `xml_root`, `item_count`, datas e shape.

## Resultados de mercado e fontes primárias

| Probe | Resultado observado | Limite de interpretação |
|---|---|---|
| brapi catálogo páginas 1 e 2, type=stock, limit=5 | HTTP 200; results/facets/pagination; totalItems=782, totalPages=157, hasNextPage=true | Não foi varredura completa. Inclui fracionário AALR3F e ABCB10 TEMP; tipo do provedor não basta para afirmar universo elegível da B3. |
| brapi units, subType=unit, volume asc | HTTP 200; AZUL98/AZUL99/AZUL97 nas amostras | Classificação é do provedor; instrumentos transitórios precisam revisão. Não inferir unit pelo sufixo 11. |
| PETR4 e VALE3, range=3mo, interval=1d | HTTP 200, 63 barras cada; OHLC, volume e adjustedClose | Testes públicos não provam disponibilidade dos demais ativos. Política de ajuste não homologada; manter unknown e não anunciar retorno total. |
| Cotação AZUL98 | HTTP 401 | Requer autenticação nesse endpoint; não houve cadastro ou leitura de credenciais. |
| GDELT ArticleList | HTTP 429, sem Retry-After | Uma consulta domainis:g1.globo.com, economy OR oil, 24 h explicitamente delimitadas e maxrecords=10. Sem retry; não prova ausência de notícias nem indisponibilidade universal. |
| BCB 1178 últimos 3 | HTTP 200; 21–23/10/2026 | Selic efetiva anualizada base 252, percentual ao ano. |
| BCB 432 últimos 3 | HTTP 200; 02–04/11/2026 | Datas futuras em relação ao probe! Nunca inserir como observações realizadas. |
| BCB ambas, 19–26/10 explicitamente | HTTP 200; 5 observações 1178 e 8 observações 432 | Consulta delimitada resolve o risco observado; validar novamente datas <= as_of na ingestão. Selic meta e efetiva são conceitos distintos. |
| CVM diretório DFP | HTTP 200; 3.611 bytes | Prova descoberta pública, não ingestão de demonstrações. |
| CVM DFP 2025 ZIP HEAD | HTTP 200; 12.806.582 bytes; Last-Modified 25/10/2026 | ZIP não baixado/descompactado. Precisa orçamento maior que teto HTTP padrão, limites comprimido/descomprimido e validação de caminhos. |

Evidências: `data/probes/core-20261026T210745Z.json`, `bcb_bounded-20261026T210929Z.json`. Falhas do sandbox preservadas em `core-20261026T210727Z.json`.

## Oito fontes editoriais

Todos os oito pontos de descoberta oficiais foram inspecionados. Não foram inventados feeds para completar uma tabela.

| Fonte | Descoberta/probe | Estado configurado |
|---|---|---|
| G1 | Página oficial Economia HTTP 200, nenhum link RSS publicado detectado no HTML inspecionado | Descoberta pendente; não significa que nenhum RSS exista |
| UOL | Página oficial RSS publica feed/economia.xml (HTTP; mesmo host/caminho em HTTPS testado). HTTP 200, mas XML inválido, linha 5 coluna 15, em duas verificações | `http_200_invalid_xml`; não habilitar como RSS operacional sem parser/encoding validado |
| CNN Brasil | Homepage oficial HTTP 200, nenhum link RSS detectado | Descoberta pendente |
| Yahoo News | news.yahoo.com redireciona a www.yahoo.com/news/ e HTTP 429 | Descoberta bloqueada; hostname novo não aprovado para ingestão de artigos |
| NYT | Página oficial /rss publica Business.xml; RSS HTTP 200, 49 itens | Metadados RSS verificados; acesso à matéria não testado |
| Fox News | Documentação oficial publica moxie.foxnews.com/google-publisher/world.xml; RSS HTTP 200, 25 itens | Metadados RSS verificados; host do feed separado de host editorial |
| InfoMoney | Homepage publica /feed/; RSS HTTP 200, 10 itens | Feed geral inclui Esportes: aplicar allowlist de seções antes de ingestão |
| Yahoo Finance | Homepage HTTP 200, nenhum RSS oficial detectado (links CSS com “feedback” não são feeds) | Descoberta pendente |

Configuração revisável: `config/verified-feeds.json`. URLs candidatas provenientes apenas de agregadores terceiros não foram habilitadas. Evidências: `discovery-20261026T210753Z.json`, `feeds-20261026T210910Z.json`, diagnóstico UOL em `feeds-20261026T211014Z.json`. Nenhuma matéria completa foi buscada, e RSS acessível não demonstra licença de armazenamento integral nem arquivo histórico de três meses.

## Documentação oficial consultada

- [brapi tickers](https://brapi.dev/docs/tickers): catálogo sem token, paginação a partir de 1, limite máximo 2000; filtro `type=stock`, campos `assetType` e `subType`.
- [brapi ações](https://brapi.dev/docs/acoes): endpoint legado /api/quote permanece ativo; quatro símbolos públicos PETR4/VALE3/ITUB4/MGLU3 não representam o catálogo. Não foi testado acesso autenticado.
- [GDELT DOC 2](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/): descoberta de artigos; não equivale a texto integral ou data original confirmada.
- [BCB 1178](https://dadosabertos.bcb.gov.br/dataset/1178-taxa-de-juros---selic-anualizada-base-252) e [BCB 432](https://dadosabertos.bcb.gov.br/dataset/432-taxa-de-juros---meta-selic-definida-pelo-copom): conceitos e unidades distintos; consulta diária deve ser delimitada, máximo documentado de dez anos.
- [CVM DFP](https://dados.cvm.gov.br/dataset/cia_aberta-doc-dfp): arquivos periódicos sujeitos a reapresentações; HEAD não valida conteúdo contábil.
- [UOL RSS](https://rss.uol.com.br/), [NYT RSS](https://www.nytimes.com/rss), [Fox RSS](https://www.foxnews.com/story/foxnews-com-rss-feeds), [InfoMoney](https://www.infomoney.com.br/): descoberta de links oficiais. Fox descreve uso pessoal não comercial com link direto ao artigo; não generalizar essa permissão a outras fontes.

## Revisão independente preliminar

Foram sinalizados aos responsáveis: aplicação efetiva da allowlist de seções (feed InfoMoney contém esporte); correspondência entre IDs exibidos pela API e IDs de estado dos adapters BCB/GDELT; confirmação de ETag apenas após parsing/ingestão bem-sucedidos para evitar 304 mascarando ingestão perdida; classificação explícita de fracionários/instrumentos temporários; filtro BCB de datas futuras. Esta lista descreve os achados no momento da revisão, não certifica que a versão final permaneça com os problemas.
