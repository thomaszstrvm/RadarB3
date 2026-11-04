# Dados, fontes gratuitas e cobertura

Pesquisa documental em 26/10/2026. **Os endpoints ainda precisam de homologação no computador de destino.** Distinguir sempre: documentação encontrada, probe executado, conteúdo acessível, conteúdo suficiente para análise.

## Seleção dos portais

Critério reproduzível: três primeiros domínios da categoria News & Media Publishers da Similarweb em cada país, tráfego local de setembro de 2026. Isso mede audiência estimada, não qualidade editorial. Seleção fixa nesta versão; revisar mensalmente por decisão explícita, sem substituir fontes silenciosamente.

| ID | Fonte | Recorte de ingestão | Limite principal |
|---|---|---|---|
| br_g1 | Globo, por meio do G1 | Economia, Mundo, Política | Ranking é de globo.com; G1 é o subconjunto escolhido |
| br_uol | UOL | Economia, política e internacional | Algumas matérias restritas; RSS documentado não garante operação atual |
| br_cnnbr | CNN Brasil | Economia/Money, política e internacional | Verificar feeds e acesso por seção |
| us_yahoo | Yahoo News | Mundo, negócios, política econômica | Muita distribuição de agências e outros veículos |
| us_nyt | The New York Times | Mundo, negócios, economia | Paywall; frequentemente apenas metadados acessíveis |
| us_fox | Fox News | Mundo, economia e política | Não ampliar automaticamente a Fox Business, que é outro domínio |
| fin_infomoney | InfoMoney | Mercados, empresas e economia | Conteúdo editorial, publicidade e conteúdo patrocinado devem ser diferenciados |
| fin_yahoo | Yahoo Finance | Mercados, economia e empresas | Conteúdo misto gratuito/premium e sindicação |

Yahoo News e Yahoo Finance pertencem ao mesmo grupo. G1 está no grupo Globo. Fontes republicando a mesma agência não são confirmações independentes. Guardar publisher, grupo, autor/agência quando disponíveis; se não conhecidos, usar `unknown`, não inventar independência.

Fontes de classificação: https://www.similarweb.com/top-websites/brazil/news-and-media/ e https://www.similarweb.com/top-websites/united-states/news-and-media/. Ranking consultado: setembro/2026, página atualizada em outubro/2026.

## Estratégia de descoberta

1. RSS oficial disponível e permitido para incrementos. Descobrir links publicados pelo veículo, validar formato, datas e atualização. Não adivinhar um endpoint e considerá-lo pronto. Usar HTTP condicional com ETag/Last-Modified quando suportado.
2. GDELT DOC 2.0 para pesquisar histórico de três meses nos domínios selecionados. Ele descobre URLs/manchetes/metadados; não entrega automaticamente o texto integral ou a licença da matéria.
3. Sitemap público e páginas editoriais apenas quando uso automatizado for permitido. Respeitar termos e restrições observadas; robots é um sinal técnico, não licença suficiente.
4. Matéria acessível: extrair conteúdo textual necessário, autor e datas. Bloqueada: conservar link, manchete e metadados permitidos; marcar acesso parcial. Não usar proxy, cookie de assinatura ou serviço de desbloqueio para contornar barreira.

GDELT ArticleList tem teto documentado de 250 registros por resposta. Dividir por domínio, tema e intervalo; se retornar o teto, subdividir temporalmente até o mínimo configurado. Se ainda saturado, persistir `truncated=true`, intervalo e contagem conhecida. Não anunciar “100%” porque a API retornou sem erro. Algumas consultas de temas mundiais precisam de termos em inglês, com aliases de empresas em PT/EN. O filtro de domínio deve ser validado contra o hostname real retornado.

Usar temas amplos e versões de consulta: juros/inflação, câmbio, energia, commodities, comércio/sanções/tarifas, conflitos, regulação, clima/desastres, tecnologia, consumo e resultados empresariais. Uma busca por ticker isolado não encontra impactos indiretos nem evita homônimos. Guardar consultas e janelas efetivamente pesquisadas para auditoria. A publicação pode anteceder a primeira descoberta; o `seendate` do índice não deve substituir automaticamente a data original.

Usar intervalo conservador configurável por provedor (6 segundos é um valor inicial nosso, não quota oficial do GDELT), uma requisição simultânea por host, retry exponencial com jitter, `Retry-After`, limite de tentativas e circuito temporário. Não recomeçar três meses de coleta diariamente; sobrepor as últimas 48 horas para capturar atualizações e recuperar lacunas por jobs separados.

Documentação: https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/ ; https://blog.gdeltproject.org/doc-2-0-api-llms-summarizing-headlines-turkish-investment-inflation-the-niger-coup/ ; https://www.gdeltproject.org/about.html . RSS UOL: https://rss.uol.com.br/ .

## Preços e catálogo B3

Adaptador inicial: brapi, sujeito a probe. A documentação gratuita consultada informa 15.000 requisições/mês, um ticker por requisição, atraso aproximado de 30 minutos e histórico de três meses. Quatro tickers de teste públicos não representam o universo completo; os demais exigem cadastro/token gratuito. O plano gratuito não inclui dividendos. A oferta pode mudar, e “gratuito” não comprova todos os endpoints em produção.

- Validar separadamente catálogo, paginação, tipo de ativo, cotação e histórico. A documentação de catálogo usa `/api/v2/tickers`; não presumir que parâmetros e respostas da cotação legada sejam idênticos.
- Guardar identificador estável da companhia e do ativo. Preservar pontos/sufixos específicos por fornecedor; nunca substituir indiscriminadamente `.` por `-`.
- Cobrir ações e units; ativos com tipo incerto aparecem em pendências de classificação. Não incluí-los silenciosamente no denominador de ações nem descartá-los.
- Preço bruto, ajustado por desdobramento e retorno total são séries distintas. Sem política de ajuste confirmada, mostrar `adjustment=unknown`; desativar métricas incompatíveis e alertar sobre eventos societários. Sem dividendos, não apresentar variação do preço como retorno total.
- Preço atual com atraso não é “tempo real”. Mostrar horário de mercado, horário de coleta e fornecedor.
- Planejar consumo: quantidade de ativos elegíveis × dias de coleta × chamadas por ativo + bootstrap/retries. Reservar 20% da quota; 12.000 chamadas planejáveis não garantem varredura diária caso o universo exceda o orçamento. Reduzir frequência ou distribuir em lotes com cobertura e fila visíveis.
- Ler catálogo semanalmente e atualizar preços diariamente após o fechamento confirmado pela agenda B3. Não fixar fechamento sem considerar calendário/horário vigente. Jobs fora do pregão devem reconhecer feriados e fins de semana.

Fontes: https://brapi.dev/faq/api-e-gratis-mesmo ; https://brapi.dev/docs/tickers ; https://brapi.dev/docs .

Fallback: adaptador opcional de Yahoo/yfinance, **desativado por padrão**, somente após verificar termos, suporte aos tickers B3 e qualidade. É acesso não oficial e pode falhar; não serve como promessa de cobertura. A origem de cada série deve permanecer separada; nunca juntar silenciosamente preços de provedores com ajustes diferentes. https://github.com/ranaroussi/yfinance .

A página oficial de cotações históricas da B3 proíbe coleta automatizada; não programar crawler de COTAHIST. Importação manual de arquivo obtido legitimamente pode ser uma função futura: https://www.b3.com.br/pt_br/market-data-e-indices/servicos-de-dados/market-data/historico/mercado-a-vista/cotacoes-historicas/ .

## Fundamentos e documentos primários

CVM DFP/ITR para demonstrações, IPE para documentos e fatos relevantes; validar data de divulgação, versão/reapresentação, CNPJ/CVM, período, moeda, escala e consolidado/individual. Arquivos em lote não equivalem a atualização instantânea. Se faltar dado para um múltiplo, omitir o cálculo. Não inferir capitalização a partir de uma classe de ação quando o total de ações é desconhecido.

Arquivos CVM em lote usam download por streaming com limites próprios em `collection.large_dataset_overrides.cvm`; o teto HTTP padrão de 5 MB se aplica a páginas/APIs, não a esses downloads. Os tetos comprimido/descomprimido são escolhas de proteção locais, ajustáveis após o probe. Validar espaço em disco, tamanho, quantidade de entradas e caminhos de ZIP; não descompactar caminhos absolutos ou com travessia. Se exceder o teto, marcar limite e preservar o último dado válido.

Links: https://dados.cvm.gov.br/dataset/cia_aberta-doc-dfp ; https://dados.cvm.gov.br/dataset/cia_aberta-doc-itr ; https://dados.cvm.gov.br/dataset/cia_aberta-doc-ipe . Sites de RI entram por cadastro validado, com URL e companhia correspondentes.

BCB/SGS para séries macroeconômicas públicas. Registrar código, unidade, frequência e conceito. Não confundir Selic meta com Selic efetiva anualizada; não preencher futuro com o último valor. Fonte inicial: https://dadosabertos.bcb.gov.br/dataset/1178-taxa-de-juros---selic-anualizada-base-252 .

## O painel de cobertura é uma funcionalidade central

Por fonte: primeira/última data conhecida, consultas realizadas, janelas saturadas, acesso integral/parcial, quantidade de eventos únicos, último sucesso, último erro, limites, próximo retry. Por ativo: preços disponíveis, último pregão, fundamentos, eventos relacionados, análise mais recente. “Sem notícia encontrada” é diferente de “fonte indisponível”.

Não há denominador confiável para “porcentagem de todas as notícias existentes”. Mostrar cobertura operacional: janelas consultadas, fontes alcançadas e conteúdos recuperados. Se usar porcentagem, nomear o denominador exato. Retenção de conteúdo protegido deve ser mínima e compatível com a permissão; guardar links e excertos necessários quando não houver permissão para arquivar o texto completo.
