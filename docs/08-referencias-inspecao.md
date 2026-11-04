# Referências e alcance da inspeção

Data: 26/10/2026. Esta é uma revisão ampla e direcionada. **Não é uma auditoria completa de todos os sites do Awwwards, de todos os links externos do SystemDesign.io ou do código inteiro dos projetos citados.** As decisões de arquitetura e de produto são propostas deste pacote, não recomendações atribuídas aos autores das referências.

## Awwwards

Ponto de partida: https://www.awwwards.com/websites/ . A página principal apresentou timeouts em algumas consultas. Foram pesquisadas as coleções [Data Visualization](https://www.awwwards.com/websites/data-visualization/), [App Style](https://www.awwwards.com/websites/app-style/) e [Minimal](https://www.awwwards.com/websites/minimal/), além de termos de dashboard/finanças. Sete referências foram qualificadas:

| Referência | O que foi consultado | O que implementar |
|---|---|---|
| [Algorithmic Trading Dashboard](https://www.awwwards.com/sites/algorithmic-trading-dashboard) | Ficha, descrição e tags; sem teste do aplicativo | Tabela de exploração e separação entre visão geral e detalhe |
| [Coronaffect](https://www.awwwards.com/sites/coronaffect) | Descrição de comparação de datasets temporais | Eventos relacionados a séries; comparação visual sem alegação causal |
| [Data Viz Project](https://www.awwwards.com/sites/data-viz-project) | Ficha do acervo; site externo retornou bloqueio | Escolher gráficos pela pergunta; linhas, barras e pequenos múltiplos |
| [Xiloteca Trevigiana](https://www.awwwards.com/sites/xiloteca-trevigiana) | Conteúdo oficial indexado sobre busca e filtros | Navegação lateral, filtros, glossário e progresso |
| [Europace](https://www.awwwards.com/sites/europace-finance-with-ease) | Ficha e tags de tipografia/responsividade | Legibilidade e estados de erro; inferência de design, não auditoria de uso |
| [Viture Dashboard](https://www.awwwards.com/sites/viture-dashboard) | Ficha e inspeção de uma imagem desktop publicada | Grade e contraste como referência secundária; a imagem é apresentação de produto, não terminal analítico |
| [Bequant](https://www.awwwards.com/sites/bequant-2) | Ficha de produto de tecnologia de redes | Organização modular e navegação interna; não é a corretora homônima |

Nenhum site recebeu auditoria de acessibilidade, desempenho ou interação completa. Não reutilizar imagens, fontes comerciais, marca ou código dessas páginas. A ilustração em `design/layout-reference.svg` é uma proposta original simplificada, com dados sintéticos identificados.

Resultado prático: tabela densa, detalhe conectado, timeline de eventos, fontes visíveis, estados claros, contraste e responsividade. Não adotar 3D, áudio, scroll cinematográfico, grandes apresentações comerciais ou animações que atrapalhem comparação de dados. Esses recursos foram excluídos por adequação à tarefa, não por avaliação negativa dos sites.

## SystemDesign.io

Índice completo consultado: https://systemdesign.io/ — 55 temas. Foram aprofundadas 11 páginas relacionadas ao problema. As páginas consultadas apresentam questões de entrevista e links de soluções; parte do conteúdo próprio de solução está em desenvolvimento. Não afirmar que foi extraída uma arquitetura pronta dessas páginas.

| Tema e URL | Aplicação proposta |
|---|---|
| https://systemdesign.io/question/design-a-job-scheduler | Jobs duráveis, checkpoints, recuperação e exclusão de execução duplicada |
| https://systemdesign.io/question/design-an-api-rate-limiter | Orçamento por provedor, limites, retry e backoff |
| https://systemdesign.io/question/design-web-crawler | Coleta incremental, URLs canônicas, controle de domínio e revisão de conteúdo |
| https://systemdesign.io/question/identify-k-most-shared-articles-in-time-windows | Agrupamento e ranking por janela; usar SQL exato no volume local |
| https://systemdesign.io/question/design-a-price-alert-system-for-amazon-or-stock-prices | Mudanças materiais, deduplicação e alertas internos |
| https://systemdesign.io/question/design-a-system-to-view-latest-stock-prices | Horário da cotação, cache e atraso explícito |
| https://systemdesign.io/question/design-a-notification-service-at-scale | Estado de alertas; sem infraestrutura de envio em massa |
| https://systemdesign.io/question/design-a-metrics-logging-and-aggregation-system | Saúde de fontes, erros e custo de coleta |
| https://systemdesign.io/question/design-facebooks-news-feed | Fila relevante e consulta incremental |
| https://systemdesign.io/question/design-google-analytics-dashboard-and-pipeline | Separação de coleta, transformação e apresentação |
| https://systemdesign.io/question/design-a-system-to-monitor-the-health-of-a-cluster | Diagnóstico simples dos processos locais |

O projeto precisa de uma aplicação local com uma fila, não de arquitetura para milhões de usuários. SQLite, worker único, cache e limites resolvem o escopo inicial. Kafka, Kubernetes, microserviços e banco vetorial ficam sem justificativa nesta fase.

## Demais fontes e verificações

- Rankings e provedores: URLs e ressalvas em `docs/03-dados-fontes.md`.
- Projetos e Figma: URLs, seleção e exclusões em `docs/07-skills-integracoes.md`.
- Codex: https://developers.openai.com/codex/auth e https://developers.openai.com/codex/noninteractive/ . As páginas podem redirecionar para a documentação oficial Learn. O CLI permite execução não interativa e saída com schema; autenticação por assinatura e por API têm condições diferentes.
- Acesso por assinatura não é garantia de uso sem limites. Conferir plano e comportamento no ambiente de destino, sem chave de API faturada ou compra automática como fallback.

## Registro de incerteza

Verificado documentalmente: recursos declarados, ranking de período específico, estrutura das referências e alguns trechos de código. Não verificado operacionalmente aqui: cobertura de cada feed, coleta de três meses completos, ajustes de todos os ativos, login do usuário no Codex CLI e qualidade de suas análises reais. Os probes e testes necessários estão nos critérios de aceite. Nenhuma referência externa é prova de retorno financeiro futuro.
