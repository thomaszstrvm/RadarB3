# ADR 0009 — Orçamento do bundle com dependências preservadas

Data: 31/10/2026. Estado: aceito.

## Contexto

O limite operacional é 60 mil caracteres. Sessenta e quatro barras de preço e seus metadados podiam excedê-lo mesmo após remover todas as notícias. Isso impedia a análise manual antes de chamar a IA. A mudança de `pending` para o identificador final também acrescentava 24 caracteres após a verificação.

## Decisão e justificativa

Reservar o comprimento do identificador final antes de medir. Sob pressão de tamanho, omitir primeiro barras antigas não referenciadas por nenhuma métrica, mantendo sempre o preço mais recente. Preservar todas as entradas das métricas, inclusive referências de métricas nulas. Se ainda necessário, omitir artigos inteiros do mais antigo para o mais recente. Registrar a quantidade de barras omitidas e a flag `bundle_price_evidence_omitted`; manter as contagens de cobertura coerentes. Falhar se o conjunto obrigatório ainda exceder o limite.

Métricas são calculadas com a série original e suas entradas necessárias ficam no bundle, permitindo a revalidação existente. Nenhum texto ou número é cortado, nenhum ajuste é inventado e o histórico completo permanece na base.

## Alternativas

- Aumentar o limite: aumenta consumo e não resolve o crescimento futuro; descartado.
- Remover primeiro todas as notícias: elimina justamente o contexto editorial investigado; substituído.
- Remover qualquer barra e recalcular indicadores: pode trocar o fundamento da análise apenas para acomodar o transporte; descartado.

## Consequências

Positivas: análise parcial explicitamente limitada pode prosseguir; fontes e métricas preservam integridade; nenhum novo serviço ou dependência.

Negativas: o modelo recebe menos histórico bruto quando o limite exige; séries ajustadas com muitos insumos obrigatórios ainda podem ser bloqueadas. A seleção usa recência determinística, sem pretender medir materialidade editorial.
