# Revisão de design do Radar B3

Workflow original do projeto, em português. Não é uma skill instalável e não depende de Figma ou Awwwards. Usa como orientação conceitual as práticas selecionadas `better-interface`, `ui-state-expander`, `create-color` e `superfuture-design-review`; seus arquivos originais não foram auditados, copiados, instalados ou incorporados. Este documento não autoriza instalar pacotes externos de skills.

Usar ao terminar uma tela ou um conjunto coerente de alterações de interface. Ler `docs/05-design.md`, `design/tokens.css` e os contratos reais de dados. Corrigir problemas encontrados dentro do escopo já autorizado; não pedir confirmação para ajustes reversíveis de hierarquia, contraste, texto, estados ou navegação.

## 1. Preparar uma revisão reproduzível

Registrar a versão/commit revisado, tela, modo de execução, tema e viewports usados. Confirmar que a aplicação local conecta React/TypeScript ao backend FastAPI e ao armazenamento SQLite conforme o projeto. Não assumir que um mock visual demonstra integração.

Em modo real, usar apenas dados efetivamente disponíveis. Para exercitar estados difíceis, usar fixtures de teste isoladas ou interceptação em ambiente de teste claramente identificado; não inserir conteúdo fictício no banco real. Não coletar dados ou acionar a IA desnecessariamente só para fotografar a interface.

Escolher três tarefas concretas para a revisão:

1. Encontrar uma mudança nova e entender por que entrou na fila de investigação.
2. Abrir um ativo e localizar o evento, o preço, a interpretação e sua evidência original.
3. Reconhecer que uma fonte ou análise está incompleta e saber o que ainda pode ser usado.

## 2. Hierarquia e utilidade — prática inspirada em better-interface

- A tela permite iniciar a tarefa principal sem atravessar conteúdo promocional?
- O ativo, o evento, o período e a data da atualização são identificáveis rapidamente?
- A tabela ajuda a comparar itens e conserva filtros/seleção ao abrir e fechar o detalhe?
- A explicação aparece próxima de sua evidência, com mecanismo, horizonte e cenário contrário?
- Prioridade, qualidade da evidência, oportunidade e retorno observado têm rótulos e significados separados?
- Uma hipótese não aparece como ordem, garantia ou probabilidade de lucro sem calibração?
- O usuário identifica o que é fonte, cálculo do sistema e interpretação da IA?
- As ações têm verbos claros, alvo adequado e resultado observável?

Eliminar cartões repetidos, rótulos vagos, precisão numérica sem suporte, texto técnico desnecessário e componentes que só ocupam espaço. Preservar a informação relevante ao julgamento da análise.

## 3. Cobertura dos estados — prática inspirada em ui-state-expander

Para cada componente que consulta dados, revisar pelo menos os estados aplicáveis abaixo. A lista completa não exige implementar recursos fora do escopo; exige que o componente existente não confunda estados diferentes.

| Situação | Verificar |
| --- | --- |
| Primeira execução | Ação inicial, explicação breve e nenhuma estatística falsa. |
| Carregamento inicial | Feedback legível, layout estável e sem números simulando resultado. |
| Atualização | Último resultado válido preservado com data e indicação da execução atual. |
| Filtro sem resultados | Filtros ativos visíveis e ação de limpar; diferente de falha na coleta. |
| Dados ausentes/parciais | Valor ausente não convertido para zero e limitação junto do dado afetado. |
| Fonte fora do ar | Motivo disponível, último sucesso e repetição controlada quando suportada. |
| Dados antigos | Data real e regra de defasagem vinda do domínio. |
| IA pendente, limitada ou indisponível | Notícias/preços continuam acessíveis e a análise tem estado próprio. |
| Saída inválida da IA | Nenhuma conclusão inválida tratada como validada; última versão válida identificada quando existir. |
| Análise revisada | Data/versão e preservação de rastreabilidade. |
| Atualização concluída sem mudanças | Mensagem distinta de erro ou ausência de dados. |

Tentar interromper uma atualização, aplicar filtros e retornar do detalhe. Verificar que o produto não salta para outra linha, troca silenciosamente o contexto temporal ou apaga uma análise válida porque uma fonte falhou.

## 4. Cores e gráficos — prática inspirada em create-color

Usar os tokens existentes; criar novos apenas se houver um significado que os atuais não cobrem. Não ajustar uma cor isolada para um screenshot sem testar seu contexto.

Medir contraste do texto sobre seus fundos reais em temas claro e escuro, incluindo tags, hover, seleção, links e foco. Alvo: pelo menos 4,5:1 para texto normal; 3:1 para texto grande e elementos gráficos essenciais. Bordas meramente decorativas não precisam receber o mesmo tratamento de controles essenciais.

Confirmar que texto/sinal/ícone preserva o significado sem depender de verde/vermelho. Qualidade da evidência não deve assumir a cor ou o significado de retorno positivo. Prioridade não deve parecer um alerta de compra urgente.

Para gráficos, verificar:

- título, unidade, período, base do preço, fonte e atualização;
- legenda acessível e séries distinguíveis por mais de uma característica quando necessário;
- eventos selecionáveis além de hover e lista textual sincronizada;
- ausência de interpolação silenciosa de dados faltantes;
- comparação normalizada quando as unidades diferirem;
- resumo e alternativa textual/tabular utilizável;
- nenhuma projeção visual numérica inventada a partir de uma hipótese textual.

## 5. Interação, acessibilidade e responsividade

Percorrer as três tarefas usando somente teclado. Revisar ordem e visibilidade do foco, abertura/fechamento de painéis, restauração de foco, atalhos declarados, nomes acessíveis e ausência de armadilhas. Regiões laterais permanentes não devem usar comportamento de modal; overlays modais devem ter tratamento completo de diálogo.

Verificar semântica de tabela (`th`, `scope`, `aria-sort`), nomes de controles, títulos, mensagens de erro e avisos de atualização. Usar leitor de tela quando disponível; caso contrário, registrar que esse teste não foi executado. Uma ferramenta automática de acessibilidade ajuda a localizar falhas, mas não substitui a revisão manual.

Revisar pelo menos:

- desktop amplo, por exemplo 1440×900;
- tela intermediária, por exemplo 1024×768;
- celular, por exemplo 390×844;
- reflow a 320 CSS px e zoom de 200%;
- tema claro, escuro e preferência por movimento reduzido.

Confirmar que a página não ganha rolagem horizontal acidental. Uma tabela pode rolar dentro de sua região, mantendo identificação e navegação claras. Não esconder data, qualidade da evidência ou limitações críticas na versão móvel. Aumentar alvos de toque sem destruir a comparação de dados.

Se ferramentas ou ambiente não permitirem alguma verificação, registrar a limitação específica e não alegar aprovação daquele critério. Não instalar serviços pagos ou dependências de teste desnecessárias para encerrar a revisão.

## 6. Revisão crítica — prática inspirada em superfuture-design-review

Classificar problemas pelo efeito na tarefa:

- **Bloqueador:** dado fictício apresentado como real; fonte não rastreável; ausência tratada como zero; ação inacessível; gráfico enganoso; qualidade da evidência apresentada como probabilidade de lucro sem base; falha que impede concluir a tarefa.
- **Importante:** limitação difícil de encontrar; tabela muda de posição durante leitura; contraste insuficiente; estado de erro ambíguo; filtro perdido; experiência móvel que omite contexto essencial.
- **Ajuste:** espaçamento, consistência de ícones, alinhamento, texto redundante ou acabamento sem impacto material na interpretação.

Corrigir bloqueadores e problemas importantes antes de considerar a tela pronta. Depois, repetir somente os fluxos e verificações afetados pelas alterações; não prolongar a revisão com ciclos sem novas evidências.

## 7. Evidência da entrega

O registro final pode ficar no relatório da implementação ou na descrição da alteração. Deve conter:

- telas e tarefas realmente verificadas;
- estados exercitados e origem dos dados utilizados;
- viewports, temas e verificações de acessibilidade executadas;
- problemas corrigidos e limitações restantes;
- screenshots locais quando úteis para revisar a implementação;
- resultado das verificações técnicas apropriadas ao frontend.

Não incluir chaves, tokens, dados sensíveis ou conteúdo integral indevido das fontes nos registros. Não declarar que o produto é “100% acessível”, “cobre todas as notícias” ou “prevê o mercado”. A entrega deve comunicar com precisão o que foi implementado e verificado.
