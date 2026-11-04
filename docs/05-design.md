# Design do Radar B3

Este documento especifica uma interface original para investigar mudanças de mercado com notícias, preços e evidências. O produto é uma aplicação local, de usuário único, com FastAPI, SQLite e React com TypeScript e Vite. O design deve ajudar a responder: **o que mudou, quais ativos podem ser afetados, por qual mecanismo e com que qualidade de informação?**

O projeto deve abrir diretamente na aplicação. Não precisa de landing page, cadastro, demonstração comercial, assinatura ou tela de compra. A interface é em português do Brasil. Os dados reais disponíveis determinam o que aparece; o modo real nunca completa lacunas com números ou notícias fictícios.

## 1. Direção visual e referências

Direção proposta: ferramenta analítica sóbria, com hierarquia editorial para a explicação e densidade controlada para a comparação. Tema claro padrão, tema escuro opcional, preferência salva localmente. Usar fonte de sistema, números tabulares, bordas discretas, contraste forte e uma cor de ação verde-azulada. Os tokens estão em `design/tokens.css`.

Pesquisa realizada em 26/10/2026: coleções Data Visualization, App Style e Minimal do Awwwards, buscas por dashboards e finanças e sete referências selecionadas. Foi uma revisão ampla e direcionada, não uma inspeção exaustiva do catálogo. As observações abaixo vieram das fichas, descrições, tags e elementos publicados. Não representam uma auditoria visual, de interação, acessibilidade ou desempenho dos sites originais.

| Referência | Evidência consultada | Decisão para este produto |
| --- | --- | --- |
| [Algorithmic Trading Dashboard](https://www.awwwards.com/sites/algorithmic-trading-dashboard) | Elementos publicados de layout, telas e esquema de fontes/cores; tags App Style e UI Design. | Usar hierarquia de aplicação financeira; construir tabela, visão geral e detalhe próprios. Não importar algoritmos ou promessas comerciais. |
| [Coronaffect](https://www.awwwards.com/sites/coronaffect) | Descrição de ferramenta que compara séries da pandemia com outros temas e datasets. | Relacionar eventos e séries temporais visualmente, com datas e evidências; não tratar associação visual como causalidade. |
| [Data Viz Project](https://www.awwwards.com/sites/data-viz-project) | Arquivo de formas de visualização descrito na ficha oficial. | Escolher cada gráfico pela pergunta: linha para evolução, barras para comparação e matriz para cobertura. Esta aplicação é uma proposta nossa. |
| [Xiloteca Trevigiana](https://www.awwwards.com/sites/xiloteca-trevigiana) | Elementos de busca, dados técnicos, glossário, notícias, navegação lateral, filtros e progresso. | Tornar busca, filtros, glossário e progresso acessíveis durante a investigação. Não incorporar 3D, jogos ou navegação imersiva. |
| [Europace — Finance with ease](https://www.awwwards.com/sites/europace-finance-with-ease) | Ficha de produto financeiro com elementos desktop/mobile/blog/404; tags Clean, Typography e Responsive. | Tipografia clara, leitura de análises e estados de erro; não reproduzir uma página comercial. |
| [Viture Dashboard](https://www.awwwards.com/sites/viture-dashboard) | Ficha com screenshots desktop/mobile e paleta declarada; também lista recursos 3D e áudio. | Considerar a adaptação entre telas e a disciplina de cores; evitar os efeitos imersivos. |
| [Bequant](https://www.awwwards.com/sites/bequant-2) | Produto de tecnologia de redes com navegação interna, ilustrações técnicas, grade modular e mapa. Não é a corretora homônima. | Usar seções navegáveis no relatório de um ativo. Um mapa só entra futuramente se explicar uma exposição geográfica relevante. |

Coleções: [Data Visualization](https://www.awwwards.com/websites/data-visualization/), [App Style](https://www.awwwards.com/websites/app-style/) e [Minimal](https://www.awwwards.com/websites/minimal/).

O [artigo de skills do Figma](https://www.figma.com/pt-br/blog/try-these-10-skills-and-show-off-your-own/) orientou a seleção conceitual de quatro práticas: `better-interface`, `ui-state-expander`, `create-color` e `superfuture-design-review`. Os originais não foram auditados, copiados, instalados nem incorporados a este pacote. Os nomes não são comandos executáveis do projeto. As práticas estão traduzidas neste documento e no workflow próprio `workflows/design-review.md`:

- melhorar hierarquia, texto e controles com base na tarefa;
- projetar estados completos antes de considerar uma tela pronta;
- definir cores por significado e verificar contraste;
- revisar clareza, coerência, acessibilidade e utilidade com critérios verificáveis.

Não copiar logos, screenshots, textos, ilustrações, fontes proprietárias ou código das referências. Não incluir dependência de Figma, Awwwards ou qualquer serviço de design para a aplicação funcionar.

## 2. Estrutura da aplicação

Navegação principal: **Visão geral, Mercado, Eventos, Favoritos, Fontes e cobertura, Configurações**. A busca global aceita ticker, nome de empresa, tema e entidade. Favoritar um ativo significa acompanhar; não representa uma posição de carteira ou ordem.

No desktop, usar navegação lateral compacta, barra superior e área principal flexível. A barra mostra período exato, estado da atualização e ação de atualizar. A regra de cálculo dos três meses pertence à especificação de dados; a interface deve apresentar as datas realmente consultadas, sem substituir automaticamente meses por um número arbitrário de dias. Datas e horários devem usar a zona definida pelo projeto e mostrar a zona quando relevante à ordenação de eventos.

Evitar uma sequência de grandes cartões com pouca informação. A parte superior da visão geral pode conter até quatro resumos compactos, derivados de consultas reais: última varredura concluída, ativos com dados disponíveis, eventos novos e análises pendentes. Uma contagem desconhecida aparece como “Indisponível”, nunca zero.

### Visão geral

Priorizar “Mudanças desde a última varredura concluída”, seguido de uma lista/tabela de itens para investigar. Explicar o motivo de cada mudança: evento novo, atualização de fonte, aumento de volume, movimento de preço ou revisão da análise. Mostrar o identificador/data da execução usada como comparação. Na primeira execução, informar que ainda não existe uma varredura anterior.

Mostrar avisos de cobertura parcial junto dos resultados afetados. Um aviso distante no rodapé não é suficiente para interpretar uma lista aparentemente completa.

### Mercado: superfície principal de comparação

Usar tabela semântica com cabeçalho fixo e primeira coluna identificando o ativo. Colunas iniciais:

1. Ativo e empresa.
2. Mudança ou evento principal, com data.
3. Variação no período selecionado, com indicação da base de preço.
4. Indicador de volume disponível, com definição acessível.
5. Prioridade de investigação.
6. Qualidade da evidência.
7. Cobertura de dados.
8. Atualização mais recente.

Deixar colunas adicionais configuráveis, sem ocultar a identidade do ativo ou o significado das métricas. Não mostrar métricas que ainda não existem no backend. Ordenação e nomes devem refletir os valores reais; um valor ausente deve ter posição consistente e nunca virar zero. Mostrar a unidade e o período no cabeçalho ou ajuda associada.

Filtros: busca, setor, tipo de evento, período, dados disponíveis, qualidade da evidência e estado da análise. Usar apenas filtros suportados pelos dados. Exibir filtros ativos em texto, contagem de resultados e “Limpar filtros”. Preservar filtros, ordenação e seleção ao voltar do detalhe; preferir parâmetros de URL para estado compartilhável dentro do ambiente local e armazenamento local para preferências visuais.

Não reordenar a tabela enquanto o usuário estiver lendo, navegando com teclado ou selecionando uma linha. Novos dados devem gerar um aviso discreto “Há uma atualização disponível” com ação para aplicar. A atualização conserva filtros e tenta manter o ativo selecionado.

Começar com paginação acessível. Virtualização só se a medição demonstrar necessidade e a navegação de teclado/leitor de tela continuar funcional. Não implementar rolagem infinita como única forma de acessar linhas.

### Detalhe do ativo

Em telas largas, manter a tabela e abrir uma região lateral complementar. Em telas menores, usar página dedicada ou painel modal acessível. O detalhe inclui:

- identidade do ativo, setor, moeda, mercado e data da cotação;
- gráfico histórico e eventos vinculados;
- resumo da mudança observada;
- hipótese da IA, mecanismo possível e horizonte descrito;
- evidências favoráveis, contraditórias e ausentes;
- contexto quantitativo calculado pelo sistema;
- fontes e cobertura, incluindo limitações de acesso;
- histórico de revisões da análise quando disponível.

Uma região lateral permanente usa `aside`/região nomeada e não aprisiona foco. Um painel sobreposto modal usa semântica de diálogo, foco inicial adequado, contenção de foco, fechamento por Escape e devolução do foco ao controle que o abriu. A página dedicada oferece uma ação clara de retorno e mantém o contexto da lista.

### Eventos

Agrupar matérias que descrevem o mesmo acontecimento. Mostrar título factual, primeira publicação conhecida, última atualização, temas, entidades, ativos possivelmente afetados e número de fontes distintas. Diferenciar fontes editoriais independentes de republicações quando essa informação estiver disponível. Dez republicações não são dez confirmações independentes.

Um clique abre o evento e as evidências. Separar data do acontecimento, data da publicação e momento da coleta; quando uma data não for conhecida, declarar isso. Não fazer parecer que uma notícia foi conhecida antes de sua publicação/coleta em uma análise retrospectiva.

### Fontes e cobertura

Tabela por fonte: estado, última tentativa, último sucesso, janela solicitada, intervalo efetivamente obtido, quantidade de itens, limitações conhecidas e ação de tentar novamente quando aplicável. Exibir motivos simples: indisponível, acesso limitado, limite de requisições, formato alterado ou sem conteúdo encontrado.

Não afirmar “cobertura total” a partir da quantidade coletada. Quando não existe um total conhecido de matérias publicadas, mostrar contagem observada e intervalo disponível, não uma porcentagem de completude inventada. Separar cobertura de preços, notícias e análise da IA.

### Configurações

Somente opções necessárias ao uso: tema, preferências de exibição, frequência suportada de atualização, configuração permitida da IA e informações de armazenamento local. Os detalhes internos de FastAPI, SQLite, processos e filas ficam em uma área técnica opcional quando úteis para diagnóstico. Não expor segredos em tela, exportações ou mensagens de erro.

## 3. Qualidade da evidência, prioridade e oportunidade

Mapeamento canônico de `analysis.status`: `supported_hypothesis` → “Hipótese sustentada”; `inconclusive` → “Inconclusiva”; `insufficient_data` → “Dados insuficientes”. De `opportunity.classification`: `investigate` → “Investigar”; `watch` → “Acompanhar”; `avoid_for_now` → “Evitar por enquanto”; `insufficient_data` → “Dados insuficientes”. Não acrescentar estados incompatíveis com o schema. A qualidade da evidência baixa/média/alta é derivada pelo backend das regras de `docs/04-metodologia-analise.md`, não um campo de certeza produzido pela IA.

Estes conceitos têm componentes visuais e textos diferentes. Não fundir todos em uma nota verde “comprar”. As escalas e fórmulas pertencem à especificação analítica; a UI não cria uma fórmula alternativa.

| Conceito | Significado na interface | Apresentação |
| --- | --- | --- |
| Prioridade de investigação | Ordem útil para revisar itens, conforme as regras do sistema. | Rótulo explícito e ordenação. Cor violeta moderada. Ajuda “Por que está nesta posição?”. Não significa retorno esperado. |
| Qualidade da evidência | Sustentação da interpretação pelas evidências, qualidade e consistência dos dados disponíveis. | Rótulo textual e justificativa. Azul/neutro, sem semáforo de compra. Se não houver metodologia calibrada, não exibir como porcentagem de probabilidade. |
| Oportunidade | Hipótese de efeito futuro que merece investigação. | Texto explicativo com mecanismo, condições, horizonte e cenário contrário. Estados exatos: “Investigar”, “Acompanhar”, “Evitar por enquanto” ou “Dados insuficientes”, conforme `opportunity.classification`. Não é garantia de valorização. |
| Retorno observado | Movimento histórico calculado a partir de preços. | Valor com sinal, período e base. Verde para positivo, vermelho para negativo, sempre acompanhado do número/sinal. |
| Cobertura | Disponibilidade das informações necessárias. | Estado próprio: disponível, parcial, antiga, ausente. Não é qualidade da evidência nem ausência de risco. |

Evitar mostradores circulares de precisão artificial, estrelas de rentabilidade, frases de urgência e probabilidade de lucro não calibrada. A IA pode expressar incerteza; o layout deve dar à evidência contraditória relevância comparável à evidência favorável.

## 4. Gráficos e evidências conectados

Gráfico principal: linha de preço histórico, com base claramente indicada. Para comparação com benchmark, usar modo normalizado com base 100 e explicar a data inicial; não sobrepor reais e pontos de índice como se fossem a mesma unidade. A especificação de dados determina ajustes, pregões e benchmark válidos.

Marcar eventos por data, agrupando marcadores próximos para evitar colisões. Selecionar um marcador destaca o evento e suas fontes; selecionar uma evidência destaca a posição temporal correspondente. Hover pode complementar a informação, mas não ser a única forma de acesso. Fornecer foco por teclado ou uma lista sincronizada acessível de eventos.

Usar poucos gráficos por tela: preço/eventos, volume quando disponível e comparação setorial quando houver dados comparáveis. Evitar gráfico 3D, pizza com muitas categorias, duplo eixo ambíguo e linhas suavizadas que inventem valores intermediários. Lacunas de observação devem permanecer visíveis, sem interpolação silenciosa.

Toda visualização informa título, período, unidade, fonte e atualização. Disponibilizar resumo textual e tabela dos pontos relevantes ou dados exportáveis. Uma tendência futura não pode ser desenhada como previsão numérica se o produto só gerou uma hipótese textual.

Evidências devem apresentar título, veículo, data, URL, tipo de acesso e trecho/resumo disponível. Se apenas título ou resumo estiver disponível, rotular essa limitação. Separar o texto da fonte da interpretação da IA. Links externos devem ser identificáveis e usar tratamento seguro de nova aba quando adotado.

## 5. Estados obrigatórios

| Estado | Comportamento esperado |
| --- | --- |
| Primeiro uso | Explicar a primeira coleta e mostrar ação para iniciá-la. Sem gráficos falsos ou números de exemplo. |
| Carregando pela primeira vez | Esqueleto com altura estável, texto de atividade e sem números aparentando resultados. |
| Atualizando dados existentes | Manter o último resultado válido, indicar atualização em andamento e conservar sua data. |
| Nenhum resultado do filtro | Explicar que os filtros não encontraram itens e oferecer limpar filtros. |
| Nenhum dado coletado | Distinguir de filtro vazio; mostrar estado das fontes e ação aplicável. |
| Dados parciais | Mostrar o que existe e a limitação junto do componente afetado. |
| Dados antigos | Mostrar data real e estado de defasagem determinado pela regra do domínio; não escolher limite arbitrário só no frontend. |
| Fonte indisponível | Resultado anterior permanece identificado; oferecer detalhe e repetição controlada, sem loop infinito. |
| IA pendente ou ocupada | Preços e notícias continuam acessíveis; análise recebe estado próprio. |
| Limite/erro da IA | Informar motivo disponível, preservar evidências e oferecer tentativa manual permitida. Não fabricar uma análise de substituição. |
| Resultado inválido da IA | Não renderizar como conclusão válida. Exibir estado de falha e conservar a última análise validada, se houver, com sua data. |
| Análise revisada | Mostrar data, versão e motivo da atualização quando conhecido. |
| Atualização sem mudanças | Informar conclusão e ausência de novas mudanças detectadas, distinguindo isso de falha de coleta. |
| Modo demonstração, se existir | Ambiente separado, banner persistente “Demonstração — dados fictícios” e armazenamento separado. Nunca misturar com resultados reais. Não é requisito do MVP. |

Usar notificações breves para confirmação de ações, mas resultados e erros que exigem decisão devem permanecer na página. Progresso percentual só aparece quando há um total conhecido; caso contrário, mostrar etapa atual e contagens reais processadas. Não interpretar “sem notícia encontrada” como “não aconteceu nada”.

## 6. Acessibilidade e responsividade

Meta de implementação: WCAG 2.2 nível AA nos fluxos entregues. Verificar contraste, teclado e semântica; a meta não equivale a uma certificação automática.

- HTML semântico; títulos em ordem, regiões nomeadas, link para pular navegação e nomes acessíveis para botões de ícone.
- Texto normal com contraste de pelo menos 4,5:1; texto grande e elementos gráficos essenciais com pelo menos 3:1. Divisórias decorativas podem ser mais discretas. Validar estados de hover, foco, desabilitado e tema escuro.
- Foco sempre visível, sem conteúdo fixo ocultando o elemento focado. A ordem do teclado acompanha a leitura.
- Ordenação de tabela expõe `aria-sort`; cabeçalhos usam `scope`; seleção e ações são controles reais, não somente handlers em uma linha.
- Cor nunca é o único portador de significado. Retornos têm sinal, estados têm rótulo, eventos têm forma/legenda quando necessário.
- Tooltips funcionam com foco e são dispensáveis para tarefas essenciais. Texto crítico não fica apenas no hover.
- Alvos de toque de aproximadamente 44×44 px onde possível; atender ao mínimo aplicável de 24×24 px ou espaçamento equivalente. Não reduzir as ações da tabela a ícones de difícil seleção.
- Zoom a 200% mantém a tarefa utilizável; reflow a 320 CSS px sem rolagem horizontal da página. A região de uma tabela de dados pode ter rolagem própria claramente indicada.
- Respeitar `prefers-reduced-motion`; não usar flashes, atualizações contínuas de números ou animação obrigatória para compreender um gráfico.
- Anunciar término de operações em região `aria-live="polite"` com mensagens agregadas. Não anunciar cada linha da coleta ou todo movimento do gráfico.

Layout sugerido, ajustável por conteúdo:

| Largura | Comportamento |
| --- | --- |
| A partir de 1280 px | Navegação lateral; tabela e detalhe lado a lado quando restar largura legível para ambos. |
| 768–1279 px | Navegação compacta/recolhível; detalhe em página ou sobreposição acessível. |
| Abaixo de 768 px | Navegação em menu; filtros em painel; lista resumida ou tabela com colunas essenciais e acesso aos demais campos. Gráfico e evidências em sequência vertical. |

No celular, não esconder qualidade da evidência, data e limitações para preservar uma aparência limpa. Permitir expandir cada item. A visualização compacta deve usar o mesmo conjunto de dados e a mesma semântica da tabela.

## 7. Implementação e critérios de entrega

Importar `design/tokens.css` uma vez na entrada do frontend. Componentes devem consumir tokens sem multiplicar valores de cor locais. Evitar fontes remotas e bibliotecas de animação apenas para decoração. Ícones podem ser SVG simples de biblioteca já adotada com licença compatível; sempre fornecer nome acessível nas ações.

Componentes conceituais: `AppShell`, `PeriodControl`, `RunStatus`, `MarketTable`, `ActiveFilters`, `AssetDetail`, `PriceEventChart`, `EventTimeline`, `EvidenceList`, `AnalysisSummary`, `CoverageStatus`, `EmptyState` e `ErrorState`. Os nomes são sugestões; preservar convenções do repositório. A regra analítica e a validação de dados ficam no backend; a UI formata, explica e permite explorar.

Tela concluída significa: dados e estados conectados ao contrato real, navegação funcional, teclado utilizável, contraste verificado, ausência de dados fictícios em modo real e evidências rastreáveis. Screenshot bonito sem esses comportamentos não atende à entrega.

O roteiro de revisão está em `workflows/design-review.md`. Registrar o que foi verificado, ambiente utilizado, limitações restantes e ajustes realizados. Não declarar testes de acessibilidade, viewport ou contraste que não foram executados.
