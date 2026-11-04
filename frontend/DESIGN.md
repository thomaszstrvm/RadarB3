# Direção preservada

Autoridade visual: `radar-b3/design/layout-reference.png` inspecionado antes da implementação e `docs/05-design.md`. A imagem é somente referência e seus dados fictícios não integram a aplicação.

Fonte de tokens: cópia integral `src/tokens.css`, importada por `main.tsx`. Fonte de sistema; teal `--color-action`; texto `--color-text`; superfície `--color-surface`; neutros e linhas discretas. Retorno usa sinal e cores positiva/negativa, prioridade violeta, evidência azul. Claro padrão e escuro via `data-theme`.

Sidebar desktop, cabeçalho com janela exata, quatro resumos compactos, filtros, tabela e detalhe lado a lado a partir de 1251px. Abaixo disso, detalhe segue a tabela como região não modal, sem armadilha de foco. Celular usa navegação horizontal e tabela em região própria rolável, sem ocultar colunas.

Gráfico mostra pontos observados, sem interpolar lacunas; tabela textual, escala em reais e marcadores de eventos acionáveis por teclado. Estado de loading/erro/vazio e ausências preservados. Mercado não atualiza automaticamente a ordem; execuções consultam o status a cada cinco segundos.

Limite da revisão: build, testes e screenshots devem ser registrados pelo fluxo integrado; esta documentação não é certificação de acessibilidade.
