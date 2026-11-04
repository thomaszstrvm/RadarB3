# Implementação, validação e aceite

O objetivo deste roteiro é entregar software real, executável localmente, com dados rastreáveis e todas as telas funcionais. O pacote atual é o planejamento. O Codex que o receber deve implementar, testar e documentar o aplicativo; não encerrar depois de gerar scaffolding, telas com números fixos ou um plano adicional.

As decisões canônicas estão na arquitetura, nos schemas e nas configurações do pacote. Quando houver conflito entre um exemplo e um schema, corrigir o exemplo e seus testes; não aceitar silenciosamente dois contratos. Não instalar skills remotas ou executar scripts de terceiros sem revisar origem, conteúdo e necessidade. Skills de design não são dependências do aplicativo em produção.

## 1. Ordem de execução e decisões permitidas

Trabalhar em marcos verticais, mantendo algo executável depois de cada um. Resolver escolhas reversíveis de implementação sem interromper o usuário. Registrar decisões e limitações com motivo e efeito. Solicitar informação apenas quando houver dependência indispensável que o ambiente e o pacote não resolvam, por exemplo login inicial da assinatura; continuar em paralelo o trabalho independente dessa etapa.

Não pedir cartão, chave paga ou autorização para contratar serviço. Não substituir fonte bloqueada por scraping proibido. Não transformar a indisponibilidade de um provedor em dados inventados. Um adaptador indisponível deve existir com diagnóstico concreto e estado visível; os caminhos disponíveis continuam funcionando.

Premissas a manter: um usuário; localhost; B3 com todas as ações e units elegíveis; janela móvel de três meses de calendário; cenários de 30/90 dias; oito recortes editoriais do catálogo; preços diários/atrasados; uma IA pelo Codex CLI da assinatura existente; gastos adicionais desabilitados.

## 2. Marco 0 — provar viabilidade das dependências

Antes de construir toda a interface, validar os pontos capazes de inviabilizar o fluxo. Criar um diagnóstico repetível e registrar versão, data, resultado e evidência, sem guardar segredos.

| Verificação | Prova exigida | Comportamento se falhar |
|---|---|---|
| Python/Node/dependências | Instalação reproduzível com lockfiles; imports/build mínimos | Corrigir versões compatíveis e documentar pré-requisitos |
| SQLite WAL | Versão real carregada pelo Python, correção WAL-reset, escrita/leitura e backup | Atualizar runtime/biblioteca por caminho gratuito; bloquear WAL inseguro até resolver |
| Codex CLI | Versão, login da assinatura, modelo acessível, opções reais de execução e schema | Indicar login/incompatibilidade; nunca migrar para API paga |
| Isolamento da análise | Insumos temporários; teste de escrita e de rede arbitrária impedidas no sandbox quando suportado | Bloquear automação de IA se controles essenciais não puderem ser aplicados |
| Universo B3 | Cadastro real importável, tipagem ações/units e contagem observada | Mostrar cadastro indisponível e permitir importação oficial documentada |
| Preços gratuitos | Ao menos uma consulta real, campos, atraso e ajuste conhecidos | Explicar limitação e avaliar outro adaptador gratuito permitido/importação oficial |
| Notícias | Uma tentativa controlada em cada fonte e capacidade de descoberta | Guardar erro, capacidade parcial e alternativa permitida |
| Bootstrap GDELT | Consulta real delimitada, datas, teto e comportamento de truncamento | Mostrar backfill parcial; não prometer três meses completos |

O teste de preços deve incluir exemplos de ON, PN, unit e ativo menos líquido quando disponíveis, sem transformar a amostra em prova de cobertura total. A cobertura do cadastro inteiro é medida no marco de ingestão. Para fonte jornalística, “HTTP 200” não prova que o artigo e a data foram extraídos corretamente: validar conteúdo, URL, timestamps e nível de texto acessível.

Executar uma chamada real pequena do Codex com evidências inofensivas e schema, usando o mesmo caminho que o aplicativo usará. Confirmar que não depende de chave API paga. Não pressupor que disponibilidade em outro produto ou modelo significa disponibilidade nessa CLI. Se a assinatura ainda precisar de login do usuário, terminar o restante do aplicativo e registrar esse bloqueio exato; não chamar a integração de testada.

Entregável do marco: diagnóstico de capacidades por provedor, README de pré-requisitos e decisões de fallback gratuito. Não é necessário obter notícias de todas as fontes para iniciar os outros marcos; é necessário tornar cada limitação mensurável.

## 3. Marco 1 — aplicação local, banco e fila

Implementar backend FastAPI, frontend React/TypeScript/Vite, migrações, configuração tipada e comando de inicialização. Em produção local, o frontend compilado é servido junto da API. Criar rotas de saúde e página de estado com dados reais do processo/banco.

Implementar fila SQLite antes de conectar a IA: criação, claim, lease, heartbeat, sucesso parcial, retry, pausa, cancelamento e recuperação. Um único worker executa tarefas; iniciar um segundo deve falhar de forma compreensível. A API pode ser reiniciada sem perder jobs. O worker não pode nascer repetidamente por hot reload ou por cada processo HTTP.

Aceite do marco:

- Instalação limpa, migração e inicialização seguem comandos documentados.
- UI e API respondem em localhost; não ficam expostas automaticamente em `0.0.0.0`.
- Uma tarefa demonstrativa controlada percorre os estados e persiste resultado.
- Matar e reiniciar o worker recupera a tarefa sem duplicar o efeito.
- API continua atendendo consultas enquanto o worker processa algo demorado.
- Backup e restauração preservam dados e integridade; falha de disco é comunicada.

## 4. Marco 2 — universo, preços e notícias reais

Implementar adaptadores de acordo com as capacidades verificadas. Importar o cadastro B3, preservar aliases e tipos, e coletar preços por lotes dentro das cotas. Normalizar sessões, timestamps, moeda, granularidade e ajustes. Implementar atualização incremental e detectar barras corrigidas.

Integrar as oito fontes do catálogo com descoberta permitida; usar GDELT para bootstrap limitado e complemento, não como certificado de completude. Particionar backfill, detectar teto, persistir cursores, deduplicar URLs e republicações e guardar revisões. Para fontes bloqueadas, a interface deve mostrar a tentativa e o nível de evidência disponível. Não exigir extração de texto integral para que uma notícia com título/resumo apareça, mas impedir que esse resumo sustente alegações que não contém.

Aceite do marco:

- Universo não depende de uma lista manual dos maiores tickers; contagem e data do cadastro ficam visíveis.
- Há separação entre elegíveis, cobertos por preço, associados a notícias e analisados.
- ON/PN/units são incluídas conforme cadastro; ETF/FII/BDR/opção/futuro não entram por erro de sufixo.
- Reexecutar o mesmo lote não duplica artigos, barras ou eventos.
- Artigo revisado e preço corrigido geram versões auditáveis.
- 429, timeout, 403, resposta inválida e quota esgotada produzem estados diferentes e não loops infinitos.
- O intervalo de três meses é calculado a cada execução; não está fixo na data do desenvolvimento.
- A tela de fontes mostra por que uma fonte está parcial ou indisponível, sem mascarar como ausência de notícias.
- O progresso do bootstrap apresenta partições pendentes/truncadas e não uma porcentagem inventada de toda a imprensa.

## 5. Marco 3 — eventos, métricas e bundles

Implementar identificação determinística de novidade e mudanças, normalização de entidades, vínculos diretos/indiretos e agrupamento conservador de republicações. Expor divergências; não combinar automaticamente acontecimentos distintos apenas porque citam a mesma empresa. Vínculos incertos devem ser revisáveis e não equivaler a exposição confirmada.

Implementar retornos e volume com metodologia explícita e regras de qualidade. Gerar bundles imutáveis conforme `schemas/evidence-bundle.schema.json`. Guardar versões da configuração, métricas e seleção de evidências. Limitar entrada por tamanho e informar exclusões. Não usar outro modelo para produzir os bundles.

Aceite do marco:

- A mesma entrada e versão de regras produzem o mesmo bundle/hash semântico.
- Recoleta sem mudança material não força nova análise.
- Correção de notícia, mudança relevante de preço ou expiração da janela invalida o resultado dependente de forma rastreável.
- Vinte republicações não viram vinte confirmações independentes.
- Atores ambíguos ou nomes comuns não geram vínculo financeiro sem justificativa.
- Métricas com pouca amostra, preço stale ou ajuste desconhecido ficam indisponíveis/qualificadas.
- Um evento mundial pode ter mecanismo de exposição setorial explicado; não precisa mencionar literalmente o ticker para ser considerado, mas a inferência deve estar identificada.

## 6. Marco 4 — única IA e análises verificáveis

Integrar o adaptador de subprocesso Codex com prompt fixo, bundle, schema, limites e ambiente mínimo. Uma execução de IA por vez. Implementar cache por bundle/modelo/prompt/schema; registrar chamadas, duração e uso quando disponível. Não habilitar busca própria da IA, agentes adicionais ou fallback para outro modelo.

Aplicar validação estrutural e semântica: as evidências citadas existem no bundle, pertencem à versão correta, não estão após o corte e sustentam o tipo de afirmação. O validador consegue verificar referências e consistência; não deve alegar provar automaticamente toda a causalidade de um texto. Evidência fraca conduz a hipótese inconclusiva ou dados insuficientes.

Aceite do marco:

- Uma análise real válida percorre UI → job → bundle → CLI → validação → banco → UI.
- É possível abrir cada evidência citada e ver origem, data, revisão e nível de acesso.
- Falta de preço, notícia suficiente ou metadado confiável não produz recomendação forte.
- Conteúdo malicioso em uma notícia não executa comando, acessa URL arbitrária ou altera prompt/schema.
- JSON inválido, ID inventado e ativo trocado são rejeitados; a saída rejeitada não aparece como resultado confiável.
- Timeout, limite da assinatura e login expirado preservam resultados anteriores e exibem o motivo.
- Não há cobrança adicional automática; a aplicação pausa quando os recursos configurados não estiverem disponíveis.
- Cenários de 30/90 dias mostram condições e riscos, não probabilidade fabricada nem valorização garantida.

## 7. Marco 5 — todas as telas e interações

Construir a direção visual do pacote com dados reais via API, estados vazios honestos e modo de demonstração separado. Fixtures podem viabilizar desenvolvimento e testes; só aparecem ao usuário quando um modo explicitamente identificado como demonstração estiver ativo. Nenhum card pode misturar número fictício com feed real sem identificação.

| Tela | Funções que precisam funcionar |
|---|---|
| Visão geral | Janela atual, mudanças, últimos jobs, dados atrasados e navegação para detalhes |
| Radar/oportunidades | Busca, filtros, ordenação, paginação, classificação explicada e comparação com estado anterior |
| Universo de ativos | Todas as ações/units elegíveis, classe, cobertura, busca e adição/remoção da watchlist |
| Detalhe do ativo | Preços, métricas, eventos, evidências, hipóteses e botão de análise com progresso real |
| Notícias/eventos | Feed filtrável, agrupamentos, revisões, fontes e ligação a ativos/setores |
| Análise | Síntese, hipóteses, contrapontos, horizontes, riscos, condições de invalidação e referências |
| Alertas | Regras editáveis, ativar/desativar, deduplicação, lido/arquivado e estados persistentes |
| Fontes e cobertura | Último sucesso, capacidade, lacunas, quotas, atraso e histórico das tentativas |
| Execuções | Fila, progresso, falhas, cancelamento, retry seguro e próxima tentativa |
| Configurações | Agenda, horizontes, limites, preferências e status do modelo/CLI, sem expor segredos |

Prover estado de carregamento, vazio inicial, erro, dados parciais e dados atrasados para cada tela pertinente. Botões decorativos sem efeito não cumprem o requisito. Uma funcionalidade indisponível por falta real de integração deve informar o motivo e o que já está funcional.

Aceite de experiência:

- Navegação, filtros, gráfico e tabela referem-se ao mesmo ativo e intervalo.
- Recarregar a página mantém estado relevante em URL ou preferências persistidas.
- Ações repetidas não duplicam jobs/alertas/watchlist.
- Interface pode ser usada por teclado, com foco visível, nomes acessíveis e contraste adequado.
- Informações não dependem somente de vermelho/verde; gráficos têm legenda, escala e valores acessíveis.
- Layout verificado em desktop e tela estreita; tabela não oculta silenciosamente colunas essenciais.
- Fonte/delay/horário acompanham o dado onde são necessários, não apenas em rodapé genérico.
- Links externos são seguros e apontam para a evidência correta; HTML externo é sanitizado ou mostrado como texto.
- Exportações mantêm filtros, proveniência e proteção contra fórmulas em CSV.

## 8. Marco 6 — operação, avaliação e entrega

Completar agenda, retomada após desligamento, backup/restauração, manutenção de logs/cache e retenção. Testar um ciclo integrado com fontes disponíveis e uma falha deliberada de provedor. A aplicação deve continuar útil com os resultados anteriores e sinalizar degradação.

Implementar armazenamento de hipóteses prospectivas e avaliação quando 30/90 dias tiverem transcorrido; a tela deve conseguir mostrar “ainda não maturou”. Não preencher resultados futuros com simulação disfarçada. Benchmark, preço de entrada/saída, custos assumidos e base de ajuste ficam documentados. A avaliação retrospectiva inicial, se oferecida, tem rótulo exploratório e não usa `first_seen_at` artificial.

Entregar:

- Código completo de backend/frontend/worker, migrações e lockfiles.
- README com instalação, login local da CLI, execução, atualização, backup, restauração e parada.
- Exemplos de configuração sem segredos e relatório de capacidades verificadas.
- Comandos de testes/build e resumo do que foi executado, com resultados.
- Evidências de navegação real nas telas e fluxos principais; capturas podem complementar, mas não substituir o teste funcional.
- Lista curta de limitações reais e dependências pendentes, vinculadas ao comportamento visível do aplicativo.
- Dados de demonstração isolados, com origem sintética explícita, e instrução para iniciar com banco real vazio.

Não encerrar dizendo apenas que o usuário pode implementar os próximos passos. Se um serviço externo bloquear uma função, concluir os componentes independentes, deixar o fluxo degradado utilizável e especificar exatamente o bloqueio que não foi resolvido.

## 9. Matriz de testes necessários

Os testes devem verificar comportamento e riscos materiais, sem espelhar cada linha de implementação. Usar relógio controlável e provedores falsos para casos de falha; usar também smoke tests reais, limitados e separados, para comprovar integrações. Testes normais não devem consumir assinatura de IA nem cotas externas.

| Grupo | Casos obrigatórios | Resultado esperado |
|---|---|---|
| Calendário | Fim de mês, ano bissexto, mudança de ano, timezone, feriado e fim de semana | Janela de três meses correta e sessão de negociação correta |
| Conhecimento temporal | Backfill hoje de notícia antiga; revisão posterior; timestamp futuro | Sem vazamento de informação em replay; conteúdo futuro fora do bundle |
| Janela móvel | Expiração de evidência e execução atravessando meia-noite | Corte congelado por execução; bundle posterior atualizado |
| Preços | Dividendos, split/grupamento, mudança de ticker, volume ausente | Sem falso salto por ajuste; identidade preservada; nulo não vira zero |
| Cobertura | Provedor sem parte dos tickers, cadastro incompleto e preço stale | Denominadores explícitos, lacunas visíveis e conclusão limitada |
| Notícias | Mesma URL com tracking, republicação, correção, título ambíguo, trecho-only | Dedup correto, revisão preservada e força de evidência adequada |
| Bootstrap | Retorno atinge teto, partição falha, quota termina | Subdivisão limitada e estado parcial/truncado auditável |
| Jobs | Morte após fetch e antes do commit; dois workers; retry; cancelamento | Retomada idempotente e ausência de execução concorrente indevida |
| Cotas | 429 com/sem Retry-After, erros permanentes, reinício perto do limite | Backoff/cap persistentes e nenhuma rota paga automática |
| IA | Schema inválido, ID inventado, número contraditório, saída enorme, timeout | Rejeição controlada sem publicação de análise inválida |
| Injeção | Notícia instrui comando, leitura de segredo, navegação ou alteração de regras | Tratamento como dado; nenhum efeito privilegiado |
| Autenticação | CLI sem login, token expirado, modelo indisponível | Diagnóstico sem exposição de credenciais e sem troca automática |
| API | Filtros, paginação estável, IDs inválidos, idempotência e estados de erro | Contrato consistente; nenhum acesso a caminho/comando arbitrário |
| UI | Cada ação principal, refresh, loading/empty/error/stale, teclado e mobile | Fluxos concluídos e estado coerente com API/banco |
| Avaliação | Horizonte imaturo, notícia após fechamento, preço faltante | Sem futuro fabricado ou entrada retrospectiva favorável |
| Recuperação | Backup com WAL ativo e restauração em banco novo | Integridade e dados preservados |

Bibliotecas de teste podem ser escolhidas pelo implementador, mantendo stack simples: pytest no backend e ferramentas de testes TypeScript/browser no frontend. É desejável gerar um cliente tipado a partir do OpenAPI, ou ao menos validar contratos dos dois lados. Não congelar versões fictícias neste planejamento; resolver versões compatíveis, verificar licenças e registrar lockfiles na implementação.

## 10. Critério de conclusão

O produto está pronto para uso local quando o usuário consegue iniciar, importar/atualizar dados reais disponíveis, entender o que foi ou não coberto, investigar um ativo com evidências, executar a análise única, receber um alerta interno e retomar trabalho após reinício. Todas as telas previstas devem responder a interações reais.

Para declarar o fluxo de IA concluído, pelo menos uma execução real na assinatura precisa ter sido validada. Para declarar uma fonte integrada, é necessário um teste real de seus dados, não só um adaptador escrito. Uma limitação externa documentada pode justificar disponibilidade parcial, mas não autoriza anunciar funcionalidade testada quando não foi.

Cobertura integral da internet, extração gratuita irrestrita de todos os portais, todos os preços em tempo real, disponibilidade ilimitada de IA e lucro futuro não são critérios honestos de aceite. O critério é software funcional, auditável e transparente sobre as lacunas observadas.

As referências de arquitetura e seus limites de leitura estão em `02-arquitetura.md`. O SystemDesign.io inspirou os problemas a resolver; não forneceu código, garantia de escala, testes dos provedores ou validação de investimento.
