# Arquitetura do Radar B3

Este documento é uma especificação de implementação. As decisões abaixo adaptam problemas de arquitetura à escala de uma aplicação pessoal; não afirmam que os provedores já foram integrados nem que todos os dados estarão disponíveis gratuitamente. O aceite de cada integração depende do teste real descrito em `06-implementacao-aceite.md`.

## 1. Resultado e limites do sistema

Construir um aplicativo local para um usuário, com interface em português, que identifica mudanças em notícias e preços, relaciona eventos a empresas da B3 e organiza hipóteses para investigação. Ações e units da B3 são o universo padrão explícito. A janela de evidências vai do instante de consulta até três meses de calendário antes; os horizontes de cenário de 30 e 90 dias são premissas iniciais configuráveis.

O aplicativo não executa ordens, não mantém conta de corretora e não transforma correlação observada em promessa de retorno. A classificação de oportunidade serve para priorizar investigação. Preços podem ser diários ou atrasados, sempre identificados como tal. Não há requisito de tempo real.

Software, armazenamento e provedores devem operar sem despesas adicionais. A IA usa exclusivamente o modelo configurado no Codex CLI, autenticado pela assinatura existente do usuário. Isso consome os limites dessa assinatura; não significa uso ilimitado. Não habilitar chaves de API pagas, compra de créditos, outro modelo, outro fornecedor de IA ou serviço de nuvem como fallback automático.

## 2. Componentes e execução

| Componente | Responsabilidade | Escolha inicial |
|---|---|---|
| Frontend | Radar, notícias, ativos, hipóteses, alertas, cobertura e configurações | React, TypeScript e Vite; recursos locais |
| API | Consultas, validação de comandos, configuração e criação de jobs | Python + FastAPI; contratos OpenAPI |
| Worker | Agenda persistente, ingestão, cálculo e execução serial de IA | Um processo Python independente da API |
| Banco | Dados normalizados, versões, jobs, resultados e auditoria | SQLite em disco local, WAL, migrações versionadas |
| Adaptadores de dados | Universo B3, cotações, calendário e notícias | Interfaces Python com capacidades e cotas declaradas |
| Adaptador de IA | Bundle validado entra; análise JSON validada sai | Subprocesso Codex CLI, um modelo fixo por configuração |

Em desenvolvimento, Vite pode usar um proxy para a API. No uso normal, servir o build do frontend e `/api/v1` na mesma origem. Escutar somente em `127.0.0.1`, sem publicação externa. Prover um comando documentado que inicia API e worker e encerra ambos corretamente. Não tornar Docker, Redis, Kafka, PostgreSQL, banco vetorial, Kubernetes ou hospedagem requisitos.

O fluxo é `fontes → ingestão → versões normalizadas → eventos e métricas → bundle → IA → validação → painel e alertas`. A tela consulta o banco; abrir uma página não dispara scraping ou análise implicitamente. Mudanças de filtros são consultas. Botões de atualização criam jobs e mostram seu progresso.

Não usar `FastAPI BackgroundTasks` como fila durável. Essas tarefas pertencem ao processo da aplicação; este projeto precisa recuperar trabalho depois de reiniciar. O desenho da fila SQLite é uma decisão do projeto, não uma recomendação de plataforma distribuída. Referência de contexto: [FastAPI Background Tasks](https://fastapi.tiangolo.com/tutorial/background-tasks/).

## 3. Configuração como fonte única

- `config/project.example.json`: modo local, timezone, janela, horizontes, agenda, limites operacionais e modelo único. O runtime deve manter configuração efetiva separada do exemplo, validada e sem segredos no Git.
- `config/providers.example.json`: provedores, prioridades, capacidades, limites, timeouts e política de cache. Não presumir que um endpoint tem disponibilidade ou cota ilimitada.
- `config/news-sources.json`: fontes editoriais permitidas, domínios, seções, propriedade editorial e métodos de descoberta.
- `schemas/evidence-bundle.schema.json`, `schemas/event.schema.json` e `schemas/analysis.schema.json`: contratos canônicos. Gerar ou testar os modelos Python/TypeScript contra eles. Não criar formatos paralelos incompatíveis.

As oito entradas editoriais são `br_g1`, `br_uol`, `br_cnnbr`, `us_yahoo`, `us_nyt`, `us_fox`, `fin_infomoney` e `fin_yahoo`. Yahoo geral e Yahoo Finance são recortes do mesmo grupo; isso não produz duas confirmações editoriais independentes. Artigos de agências republicados devem manter atribuição da origem, quando identificável.

As oito fontes são um recorte operacional. Não escrever na interface que representam toda a imprensa, os oito maiores grupos independentes ou cobertura integral dos acontecimentos mundiais. O catálogo e o relatório de pesquisa explicam o critério de seleção.

## 4. Banco e propriedade dos dados

Usar migrações reproduzíveis; `foreign_keys=ON`, `busy_timeout` limitado e transações curtas. Ativar WAL em disco local, não em pasta de rede. Antes disso, verificar a versão efetiva da biblioteca SQLite carregada pelo Python. A documentação oficial registra correção do problema WAL-reset em 3.51.3 e posteriores, com backports 3.44.6/3.50.7. Aceitar versão corrigida comprovada; não assumir correção apenas pelo número da versão do Python. Referência: [SQLite WAL, incluindo limitações e correção](https://www.sqlite.org/wal.html).

API e worker podem fazer transações pequenas; o worker é o único executor dos pipelines. Não manter transação aberta enquanto aguarda rede ou IA. Fazer backup pela API de backup do SQLite ou procedimento equivalente consistente e testado; copiar apenas o arquivo `.db` aberto em WAL não é um backup válido por definição.

Modelo lógico mínimo; nomes podem ser ajustados nas migrações sem perder estas responsabilidades:

| Grupo | Tabelas sugeridas | Conteúdo e invariantes |
|---|---|---|
| Universo | `issuers`, `assets`, `asset_aliases`, `universe_snapshots` | Identidade estável da empresa/ativo, ticker, classe, tipo, validade e fonte do cadastro |
| Calendário e preços | `market_sessions`, `price_bars`, `price_revisions`, `corporate_actions` | Sessão, OHLCV quando disponível, moeda, base de ajuste, provedor, timestamps e revisões |
| Fontes | `news_sources`, `provider_state`, `quota_ledger` | Estado, capacidade verificada, cotas, cache, cursor e última execução |
| Notícias | `articles`, `article_revisions`, `article_discoveries` | URL canônica, fontes de descoberta, texto permitido, timestamps, hashes e versões imutáveis |
| Eventos | `events`, `event_versions`, `event_articles`, `event_asset_links` | Agrupamento, ligações com evidências, entidades e justificativas; vínculos diretos e indiretos distintos |
| Análise | `metric_snapshots`, `evidence_bundles`, `analysis_runs`, `analyses` | Entradas congeladas, versões de regras/modelo/prompt, resposta bruta restrita e análise aprovada |
| Operação | `jobs`, `job_runs`, `worker_lease`, `coverage_partitions` | Fila, tentativas, heartbeat, cursores, cobertura e lacunas |
| Alertas | `alert_rules`, `alerts`, `alert_state` | Condição, estado anterior, cooldown, deduplicação, leitura e arquivamento |
| Preferências | `watchlist`, `settings`, `audit_events` | Acompanhamento pessoal, configuração versionada e alterações importantes |

Usar identificadores internos estáveis; ticker não é chave histórica universal. Chaves únicas devem impedir duplicação lógica. Exemplos: barra por ativo/provedor/sessão/base de ajuste; revisão por artigo/hash; execução lógica por tipo/escopo/período; análise por hash do bundle/modelo/prompt/schema. Para texto pesquisável, FTS5 é opcional: detectar suporte e manter busca funcional alternativa.

Guardar valores numéricos com precisão definida. Em cálculos financeiros, evitar que arredondamento de exibição altere retornos ou limiares. Volume ausente é nulo, não zero. Datas sem timezone e timestamps impossíveis vão para quarentena ou recebem qualidade explicitamente inferior; nunca adivinhar silenciosamente.

## 5. Tempo, janela e universo B3

Persistir instantes em UTC, com serialização ISO 8601 e timezone explícito. A data de sessão e o corte de três meses usam `America/Sao_Paulo`, com calendário de negociação versionado. Não modelar pregões apenas como segunda a sexta.

Para um `as_of` congelado no início de uma execução, calcular `window_start` subtraindo três meses de calendário na zona configurada, limitando o dia ao último dia do mês de destino, e então normalizar para **00:00:00 do início dessa data local**. Exemplo: `as_of=2026-10-26T12:00:00Z` produz início `2026-07-26T03:00:00Z`, meia-noite em São Paulo; 31/05 volta ao último dia de fevereiro. Uma execução inteira usa o mesmo corte, mesmo que termine depois da meia-noite. A janela inclui o início e o `as_of`; notícias com data futura não entram. Horizontes de 30/90 dias significam dias de calendário, declarados na tela.

Timestamps de notícias têm papéis distintos:

- `published_at`: publicação declarada pela fonte, com precisão e confiabilidade registradas.
- `modified_at`: modificação declarada, quando disponível.
- `first_seen_at`: primeira descoberta pelo sistema; imutável.
- `fetched_at`: instante da obtenção de uma versão.
- `available_at`: instante conservador em que aquela versão ficou disponível para esta aplicação; não anterior ao conhecimento efetivo registrado.

Um bootstrap feito hoje pode trazer notícia publicada há dois meses. Ela participa da análise atual se estiver na janela, mas não pode aparecer em uma simulação como se o sistema já a tivesse capturado dois meses atrás. Revisões antigas permanecem recuperáveis; a correção posterior não reescreve o passado. Para replay histórico, selecionar a versão e os metadados conhecidos até o corte; se isso não puder ser comprovado, marcar o replay como retrospectivo e impróprio para medir desempenho preditivo.

O universo cobre todas as ações e units identificadas como elegíveis no cadastro B3 disponível, com data e cobertura do cadastro. Excluir ETFs, FIIs, BDRs, opções e futuros deste padrão. Usar tipo do instrumento, não apenas o sufixo do ticker. Preservar mudanças de ticker, listagens, deslistagens e classes; uma unit tem identidade própria e relação documentada com o emissor. Não somar automaticamente classes da mesma empresa como evidências independentes.

Separar `elegíveis`, `com cadastro válido`, `com preço`, `com notícia relacionada` e `com análise válida`. Varredura do universo não exige uma chamada de IA por ativo. Limites gratuitos podem impor processamento por lotes; fila, progresso e ativos ainda sem cobertura devem permanecer visíveis. Watchlist prioriza trabalho, mas não reduz silenciosamente o universo declarado.

## 6. Notícias, bootstrap e atualizações

O GDELT é um mecanismo de descoberta e bootstrap, não a prova de cobertura integral nem a fonte editorial final. Consultar somente os recortes permitidos, respeitar os limites correntes documentados e registrar consulta, intervalo, paginação ou subdivisão, quantidade e estado. Não transformar o primeiro lote retornado em uma coleta completa de três meses.

Planejar partições por fonte e período, refinando por intervalos menores quando o resultado chega ao teto do endpoint. Se o menor intervalo ainda atingir o teto, o orçamento acabar ou a consulta falhar, registrar `truncated`, `budget_exhausted` ou `failed`, com o intervalo afetado. Mesmo partições concluídas significam consulta concluída no mecanismo utilizado, não descoberta de todas as notícias publicadas pelo portal. Não inventar um denominador de notícias para declarar percentual de completude.

Depois do bootstrap, usar feeds ou endpoints públicos permitidos e GDELT como complemento, conforme capacidade verificada por fonte. Consultas incrementais mantêm cursores e uma pequena sobreposição configurável para atrasos de indexação; deduplicação evita reprocessamento. O agendamento respeita a máquina ligada: ao reiniciar, recuperar intervalos faltantes dentro da janela e das cotas. Não anunciar monitoramento 24 horas se o computador pode ficar desligado.

Cada adaptador declara `discovery`, `history`, `full_text`, limites, datas suportadas, método de autenticação e última verificação. Não inferir que RSS oferece três meses de histórico. Não contornar paywall, login, bloqueios ou restrições de acesso. Quando só houver título e trecho, guardar esse nível de evidência e limitar a conclusão. Se título/trecho não forem suficientes para a hipótese, devolver dados insuficientes.

Normalizar URL e remover apenas parâmetros reconhecidamente de rastreamento, preservando parâmetros semânticos. Usar URL canônica declarada apenas após validação do domínio e registro da URL original. Desambiguar republicações por título/texto/atribuição, não apenas por URL. Uma atualização real gera revisão; não desaparece por deduplicação. Links obtidos em notícias são dados, não permissão para ampliar o crawler.

Restringir saídas HTTP a provedores/domínios configurados. Revalidar redirects, rejeitar endereços locais/privados e esquemas não HTTP(S), impor tamanho de resposta, timeout e limite de redirects. Extrair texto sem executar scripts. Nunca renderizar HTML externo diretamente na interface. Guardar apenas o conteúdo permitido e necessário, com URL e atribuição.

## 7. Preços, cálculos e identificação de mudança

Cada provedor de preços informa cobertura, atraso, granularidade, limites e base de ajuste. Os adaptadores normalizam os símbolos sem alterar a identidade do ativo. Manter `session_date`, `as_of` do dado, `ingested_at`, moeda e provedor. A barra revisada deve ser versionada; não misturar preços brutos e ajustados numa mesma série ou comparação. Em replay, usar a versão conhecida no corte. Uma série ajustada baixada hoje pode incorporar eventos posteriores ao corte histórico: não a tratar automaticamente como dado disponível naquela data.

Usar dados ajustados apenas quando a metodologia do provedor for conhecida e compatível com a métrica. Separar retorno de preço e retorno total; não chamar ambos de retorno total. Dividendos, desdobramentos, grupamentos e outras ações corporativas podem produzir saltos artificiais. Se o tratamento não for confiável, excluir a comparação afetada da detecção de anomalia e explicar a lacuna. Não reconstruir preços ou volumes ausentes com IA.

O cálculo determinístico identifica mudanças: retorno desde a sessão anterior válida, variação acumulada, mudança de volume quando há base suficiente, novas notícias, revisões materiais, novo evento, alteração de fontes independentes e novas ligações com empresas. Parâmetros e número mínimo de observações ficam versionados. Comparar métricas somente em bases compatíveis. Eventos e exposições indiretas devem mostrar o motivo do vínculo; palavra parecida com nome de empresa não basta.

Evitar recalcular uma análise porque apenas o horário de coleta mudou. O hash semântico de entrada considera evidências/revisões selecionadas, período de preços, métricas, configuração e corte temporal relevante. A expiração de evidências da janela também pode mudar o bundle. Coleta e cálculos percorrem o universo; a IA recebe somente ativos/eventos candidatos e reanálises materiais dentro do orçamento.

O ranking usa critérios explícitos de relevância para investigação. Não inventar probabilidades de alta, preço-alvo ou previsão quantitativa apenas com linguagem confiante. Valores calculados são fornecidos à IA pelo bundle; ela não é a calculadora de retornos. Toda hipótese deve distinguir evidência observada, mecanismo sugerido, contraponto, condições de invalidação e incerteza.

## 8. Jobs duráveis, cotas e falhas

Estados mínimos de job: `queued`, `running`, `succeeded`, `partial`, `retry_wait`, `failed`, `cancelled` e `paused`. Persistir tipo, escopo, chave idempotente, `due_at`, cursor, tentativas, lease, heartbeat e erro categorizado. Um job recorrente produz execuções identificáveis, sem sobrescrever seu histórico.

O worker adquire lease exclusivo, faz claim de job em transação curta e confirma o resultado em nova transação. Um segundo worker deve recusar iniciar enquanto o lease estiver válido. Recuperar leases vencidos somente após verificar heartbeat e condição de retomada. Encerramento gracioso não descarta cursor. O processamento é pelo menos uma vez, com efeitos idempotentes; não prometer execução exatamente uma vez em rede externa.

Prioridades iniciais: recuperação e saúde, atualização de cadastro/preços/notícias, normalização e detecção, análise IA, manutenção. Um bootstrap grande usa lotes retomáveis e não impede atualizações recentes. Ao voltar de um período desligado, juntar execuções recorrentes perdidas de modo a cobrir o intervalo, sem disparar centenas de tarefas iguais.

Reservar orçamento antes de chamar um provedor e reconciliar a contabilização depois. Diferenciar requisição, item e token, conforme contrato do serviço. Concorrência por domínio e por IA é limitada; a IA tem uma execução por vez. Em HTTP 429, honrar `Retry-After` quando houver; falhas transitórias usam backoff com jitter e número máximo de tentativas. Erros permanentes de autenticação, formato ou permissão não entram em loop de retry. Esgotamento de cota pausa o escopo e informa a próxima tentativa possível, sem trocar para serviço pago.

Uma falha preserva o último resultado válido, com indicação de atraso. Se não há resultado válido, mostrar indisponibilidade, não zero. `partial` exige lista de lacunas, não uma mensagem genérica de sucesso. Cancelar interrompe entre etapas seguras e conserva o trabalho já confirmado.

## 9. Adaptador de IA e fronteira de confiança

O runtime analítico usa um único modelo Codex configurado. Não usar embeddings remotos, segundo classificador, tradutor por IA ou agentes de revisão como dependências escondidas. O uso de agentes para escrever o software durante desenvolvimento não muda esta restrição do aplicativo final.

O bundle é um snapshot imutável validado pelo schema do pacote, com ativo, janela, horizontes, evidências identificadas, métricas calculadas e cobertura. O recorte deve ser limitado e reproduzível: diversidade de fontes, relevância, recência, contrapontos e orçamento de tamanho. Guardar quantos itens ficaram de fora e o motivo. O modelo não deve preencher lacunas com pesquisa própria ou memória apresentada como fonte.

O processo pai prepara o bundle, resolve o schema e invoca o binário com argumentos separados, sem `shell=True`, sem interpolar notícia ou ticker como código de shell. Preferir prompt por stdin. Usar timeout, limite de tamanho de saída, cancelamento e captura de exit code; encerrar também processos filhos para evitar chamada órfã. Uma falha depois do envio pode ter consumido uso da assinatura sem devolver resultado: registrar a incerteza antes de tentar novamente. Não passar variáveis de chaves de API pagas. Não ler, imprimir, copiar para o repositório ou enviar à interface tokens de autenticação da assinatura.

Na primeira configuração, verificar versão instalada e opções efetivamente disponíveis no `codex exec --help`. A documentação oficial descreve `codex exec`, saída estruturada por `--output-schema` e reuso de autenticação salva; não copiar flags antigas sem verificar. Referência: [Codex — modo não interativo](https://developers.openai.com/codex/noninteractive/).

Executar a análise em diretório temporário isolado dos arquivos do projeto, com apenas os insumos necessários. Aplicar sandbox de somente leitura e rede do shell desabilitada quando suportadas pelo ambiente; desabilitar busca web, MCPs, hooks, skills e ferramentas de execução desnecessárias conforme a CLI instalada permitir. A conexão da própria CLI ao serviço de IA é necessária e é diferente do acesso arbitrário à rede pelo shell do agente.

Não usar opções que ignorem sandbox ou aprovações. Verificar o isolamento no ambiente real, em vez de deduzi-lo pelo nome de uma flag. Somente leitura não garante restrição de leitura de segredos; manter configuração mínima, entradas temporárias e permissões do sistema operacional adequadas. Se os controles essenciais não estiverem disponíveis, bloquear a análise automática e mostrar a incompatibilidade; continuar coleta, métricas e consulta. Documentar a correção necessária, sem substituir a IA ou comprar outro serviço.

Artigos, títulos, URLs e trechos são conteúdo não confiável. Instruções como “ignore regras”, “execute este comando” ou “envie um segredo” dentro de uma notícia nunca alteram a tarefa, ferramentas, schema ou destino. Separar o prompt fixo dos campos de evidência; não concatenar conteúdo em instruções de sistema. Nenhuma resposta da IA autoriza comando, download, navegação ou transação.

Após a chamada, validar JSON/schema, IDs de evidência, ativo, corte temporal e consistência numérica. Rejeitar referência inexistente, recomendação sem suporte ou saída malformada; guardar o motivo e não publicar como análise válida. Uma tentativa limitada de correção pelo mesmo modelo só pode usar o mesmo bundle e erros de validação. Não entrar em ciclo infinito.

Estados de análise e classificações obedecem a `schemas/analysis.schema.json`: `supported_hypothesis`, `inconclusive`, `insufficient_data`; oportunidade `investigate`, `watch`, `avoid_for_now`, `insufficient_data`. A resposta bruta nunca substitui o objeto validado. Registrar modelo, versão da CLI, hash do bundle, prompt/schema e instantes; não prometer reprodução literal de texto do modelo.

## 10. API mínima e comportamento de interface

Prefixo `/api/v1`. Os contratos abaixo são requisitos funcionais; refletir modelos tipados e exemplos no OpenAPI. JSON usa nomes consistentes, timestamps UTC, números nulos quando ausentes e erros estruturados. Listas têm paginação por cursor ou página estável, tamanho máximo, ordenação explícita e filtros validados. Toda resposta dependente de dados informa `as_of` e qualidade/atraso adequados ao recurso.

| Método e rota | Contrato de comportamento |
|---|---|
| `GET /health/live` | Processo HTTP responde; não afirmar que dados ou worker estão saudáveis |
| `GET /health/ready` | Migrações, banco e capacidade de servir consultas; degradação de fontes separada |
| `GET /overview` | Resumo da janela, universo, mudanças recentes, análises e estado da coleta |
| `GET /assets` | Busca, classe, setor quando conhecido, cobertura, watchlist e ordenação |
| `GET /assets/{asset_id}` | Cadastro, aliases, métricas, evidências e última análise válida |
| `GET /assets/{asset_id}/prices` | Série com período, granularidade, origem, ajuste e lacunas |
| `GET /events` e `GET /events/{event_id}` | Feed e detalhe com versões, fontes, vínculos e justificativas |
| `GET /articles/{article_id}` | Metadados, revisões permitidas, link original e nível de acesso ao texto |
| `GET /analyses` e `GET /analyses/{analysis_id}` | Hipóteses filtráveis, análise validada e evidências rastreáveis |
| `POST /assets/{asset_id}/analyse` | Enfileira análise da janela atual; `202` com job; reaproveita resultado equivalente |
| `GET /jobs` e `GET /jobs/{job_id}` | Estado, etapas, progresso, cursor resumido, falhas e próximas tentativas |
| `POST /jobs` | Tipos permitidos e escopo validado; cria execução manual com chave idempotente |
| `POST /jobs/{job_id}/cancel` | Cancelamento cooperativo e resultado persistido |
| `POST /jobs/{job_id}/retry` | Nova tentativa do mesmo escopo quando seguro; não duplica os efeitos |
| `GET /sources` e `GET /coverage` | Estado das fontes, intervalos consultados, lacunas, atraso e universo coberto |
| `GET /alerts` e `PATCH /alerts/{alert_id}` | Alertas persistentes, marcar lido/arquivado sem recalcular análise |
| `GET/POST /alert-rules`, `PATCH/DELETE /alert-rules/{rule_id}` | Regras locais tipadas, habilitação, edição e remoção explícita |
| `GET/POST /watchlist`, `DELETE /watchlist/{asset_id}` | Acompanhamento persistente do usuário |
| `GET/PATCH /settings` | Configuração permitida; validar, versionar e indicar efeitos na próxima execução |
| `GET /exports/analyses` | Exportação JSON/CSV com filtros e proveniência; escapar fórmulas em CSV |

Para mutations, aceitar chave de idempotência onde uma repetição poderia criar trabalho duplicado. Retornar `400/422` para entrada inválida, `404` para recurso ausente, `409` para transição incompatível e `503` para dependência necessária indisponível. Esgotamento local de orçamento não deve aparecer como sucesso vazio. A API não entrega segredos, arquivos arbitrários, stderr integral ou comandos livres ao navegador.

Validar Host/Origin; não usar CORS irrestrito. Proteger mutações contra solicitações de páginas externas, mesmo em localhost, com política de mesma origem e mecanismo local apropriado. Não expor em LAN sem uma nova decisão de autenticação e implantação.

O frontend usa polling moderado apenas com página visível e jobs em andamento, ou mecanismo equivalente simples. Não precisa de WebSocket. Todas as ações visíveis devem funcionar ou exibir motivo real de indisponibilidade. Rotas, filtros, seleção e ordenação devem sobreviver a recarregar a página quando fizer sentido.

## 11. Saúde, cobertura, alertas e avaliação

Exibir por fonte/provedor: última tentativa, último sucesso, atraso, capacidade disponível, quota consumida/conhecida, próxima tentativa e motivo de degradação. Exibir por coleta: intervalo pretendido, consultado, truncado, fracassado e pendente. Separar cobertura do universo de ativos de cobertura de notícias. Não construir “98% da internet analisada”.

Logs estruturados usam `run_id`, `job_id`, `provider_id` e duração. Redigir segredos e limitar retenção; registrar códigos de erro, não conteúdo sensível completo. Medir quantidades coletadas, revisadas, duplicadas, inacessíveis, rejeitadas e usadas em análises. IA sem dado de tokens deve mostrar uso desconhecido, não zero.

Alertas começam no próprio aplicativo. Uma regra compara estado anterior aceito e atual, dispara em mudança/limiar, aplica cooldown e reativação. A chave de deduplicação inclui regra, entidade e mudança relevante. Uma página aberta várias vezes não gera alertas repetidos. Estado stale e dados insuficientes podem gerar aviso de saúde, mas não “nova oportunidade” fundamentada em ausência de dados.

Persistir as hipóteses a partir de sua produção real. Avaliação futura de 30/90 dias só acontece quando o horizonte amadureceu. Para uma política conservadora com dados diários, definir a sessão de entrada posterior ao `as_of` da hipótese e a sessão de saída no ou após o horizonte; documentar preço usado, ajustes, benchmark e custos assumidos. Não escolher retrospectivamente a melhor entrada. Coleta inicial de três meses não fornece automaticamente histórico honesto de previsões. Uma avaliação exploratória retrospectiva deve ter rótulo distinto, sem alegação de desempenho fora da amostra.

## 12. Inspiração de arquitetura e limites da pesquisa

Foi lido o índice de 55 questões do [SystemDesign.io](https://systemdesign.io/) e foram abertas 11 páginas relacionadas ao projeto. O site apresenta problemas de entrevistas de arquitetura; as páginas consultadas apontavam soluções próprias em elaboração e links externos. Não foram lidos todos os artigos e vídeos externos. As escolhas deste documento são adaptações autorais para uma aplicação local.

| Página consultada | Ideia aproveitada | Escolha concreta |
|---|---|---|
| [Job scheduler](https://systemdesign.io/question/design-a-job-scheduler) | Horário, concorrência e prioridade | Jobs duráveis, worker único e retomada |
| [API rate limiter](https://systemdesign.io/question/design-an-api-rate-limiter) | Limites e tolerância a falhas | Orçamentos por provedor, cache e backoff |
| [Web crawler](https://systemdesign.io/question/design-web-crawler) | URLs, recrawl e intervalos | Ingestão delimitada e revisões |
| [Top K em janelas](https://systemdesign.io/question/identify-k-most-shared-articles-in-time-windows) | Contagem por período | SQL exato e deduplicação de republicações |
| [Price alert](https://systemdesign.io/question/design-a-price-alert-system-for-amazon-or-stock-prices) | Regras e mudanças de fonte | Alertas explicáveis, deduplicados e com cooldown |
| [Stock prices](https://systemdesign.io/question/design-a-system-to-view-latest-stock-prices) | Ingestão, histórico e apresentação | Séries com origem, atraso e ajuste explícitos |
| [Notification service](https://systemdesign.io/question/design-a-notification-service-at-scale) | Estados e confiabilidade | Caixa de alertas persistida no SQLite |
| [Metrics logging](https://systemdesign.io/question/design-a-metrics-logging-and-aggregation-system) | Coleta e consulta de estado | Logs locais, métricas e cobertura visível |

Também foram consultados [news feed](https://systemdesign.io/question/design-facebooks-news-feed), [analytics dashboard/pipeline](https://systemdesign.io/question/design-google-analytics-dashboard-and-pipeline) e [health monitoring](https://systemdesign.io/question/design-a-system-to-monitor-the-health-of-a-cluster). Foram aproveitadas separação de etapas, consulta de feed e monitoramento de saúde. Escala distribuída, fanout massivo e streaming não são requisitos deste projeto.
