# Metodologia de análise e detecção de mudanças

## Separar observação, interpretação e hipótese

- **Fato observado:** número/documento/manchete com fonte, data e nível de acesso. O parser registra o que efetivamente obteve.
- **Métrica calculada:** fórmula, série, unidade, intervalo e política de ajuste identificáveis. É produzida por código, não inventada pela IA.
- **Interpretação:** possível mecanismo ligando o evento à empresa, com evidências de exposição e explicações concorrentes.
- **Cenário:** resultado condicional para o horizonte escolhido. Incluir o que o confirmaria e o que invalidaria a hipótese.

Exemplo abstrato: uma commodity sobe → pode aumentar receita de um produtor exposto → o benefício depende de custos, hedge, volume, câmbio e expectativas já incorporadas. Isso não autoriza concluir que a ação subirá. O texto da análise deve deixar esses elos explícitos.

## Pipeline determinístico

1. Normalizar URLs, títulos e textos; manter hashes das versões. Remover parâmetros de rastreamento conhecidos sem destruir parâmetros que identificam a matéria.
2. Deduplicar primeiro por URL canônica e hash; depois por semelhança de título, entidades e janela temporal. Marcar similaridade como candidata, preservando possibilidade de revisão. Uma atualização material do conteúdo deve gerar nova versão, não desaparecer na deduplicação.
3. Agrupar eventos e republicações; contar grupos editoriais e origem de agência quando conhecidos. Dez republicações da mesma agência continuam sendo um relato de origem.
4. Vincular aliases/tickers/CNPJ/entidades. Vínculo indireto exige uma exposição econômica, não só coocorrência textual. Um dicionário setorial inicial pode propor candidatos, mas não comprova receita ou sensibilidade de uma empresa.
5. Medir novidade contra as versões e análises anteriores; novidade local (“primeira vez que o sistema viu”) não prova primeira divulgação pública.
6. Preparar bundle fechado, com IDs, acesso e horários. Validar tamanho e reservar evidências contrárias, não apenas as favoráveis. Uma evidência cortada por limite de texto deve trazer essa indicação.
7. Colocar na fila IA apenas material novo, revisão relevante, vencimento de tese ou atualização significativa dos dados. Reutilizar resultado somente com mesmo hash do bundle, modelo, prompt e schema.

## Métricas do MVP

| Métrica | Definição e restrição |
|---|---|
| Retorno de preço | `P_fim / P_inicio - 1`, mesma moeda/série/ajuste. Incluir datas reais; omitir se falta observação válida. Não chamar de retorno total sem dividendos. |
| Retorno em 1/5/21/63 pregões | Exige `n+1` fechamentos comparáveis. Três meses de histórico podem não fornecer 64 observações; resultado então é indisponível. |
| Volume relativo | Volume do pregão completo / mediana dos 20 pregões completos anteriores. Sem 20 observações ou mediana positiva: indisponível. Não comparar volume intradiário a dia cheio. |
| Volatilidade realizada | Desvio-padrão amostral dos retornos logarítmicos diários × raiz de 252, com pelo menos 20 retornos. É descritiva, não probabilidade de queda futura. |
| Retorno relativo simples | Retorno do ativo menos retorno do índice/setor no mesmo intervalo e tipo de série. Não chamar de alfa causal. Se não há benchmark comparável, omitir. |
| Reação ao evento | Janela de preço alinhada à primeira divulgação conhecida e ao primeiro pregão elegível; manter flags para horário incerto e eventos concorrentes. Não testar reação com dados futuros ao `as_of`. |

Não calcular P/L para lucro negativo como se fosse múltiplo de desconto; indicar inaplicável. Diferenciar fluxo trimestral, acumulado e TTM. Não somar números acumulados repetidamente. Dívida/EBITDA, margem e outros fundamentos só entram com mapeamento e qualidade demonstrados, não como exigência de preenchimento inicial.

## Três conceitos distintos na interface

**Prioridade de investigação** determina a fila, não o potencial de lucro. Proposta inicial auditável de pontuação 0–100: novidade 0–30 + vínculo econômico 0–30 + relevância do tipo de evento 0–20 + qualidade/disponibilidade de evidência 0–20. Pesos são heurísticos versionados, sem calibração financeira. Componentes usam regras determinísticas: URL nova não basta para novidade máxima; vínculo apenas inferido recebe pontuação menor; relevância usa taxonomia documentada; qualidade considera documento primário, independência e acesso. A IA pode explicar a pontuação, não alterá-la silenciosamente. Empates ordenam por evento mais recente e ID estável.

**Qualidade da evidência** é exibida por eixos: acesso, recência, independência, vínculo e cobertura de preços/fundamentos. Não apresentar um “87% de confiança” arbitrário. Escala resumida baixa/média/alta pode existir com regras explícitas: só manchete → baixa; acesso completo sem confirmação independente → no máximo média; alta exige vínculo documentado e confirmação adequada, não apenas volume de links.

**Classificação da hipótese** é produzida pela IA: investigar, acompanhar, evitar por enquanto ou dados insuficientes. Não é ordem nem pontuação de rentabilidade. Qualidade alta não implica oportunidade positiva. Falta de notícia não implica ausência de risco.

## Contrato da IA

Uma única configuração de modelo, em chamadas sequenciais limitadas, usando o mesmo bundle. Saída obedece a `schemas/analysis.schema.json`. O backend associa prompt/modelo/versão, hashes, timestamps, métricas e cobertura; não confiar em metadados gerados pelo modelo.

Toda métrica não nula exposta à IA deve ter um registro próprio em `evidence` com `record_type=computed_metric`, conteúdo que descreva valor, unidade, fórmula e IDs dos insumos, e um ID próprio indicado em `computed_metrics[].evidence_id`. O campo `computed_metrics[].evidence_ids` lista os insumos do cálculo. Claims citam o ID do registro calculado, não `metric_id` nem uma notícia sem relação com o número. Métrica nula tem `evidence_id=null` e `missing_reason` preenchido. Esta invariância deve ser validada no backend além do schema estrutural.

Cada claim tem `kind` fact/inference, texto e IDs de evidência. Cenários usam `conditional_case`, `upside_case` ou `downside_case`; não exigem três cenários vazios, probabilidades ou preço-alvo. Incluir riscos e condições contrárias. Não afirmar “ainda não precificado” como fato: é hipótese, e ausência de movimento isolada não demonstra isso.

Só manchete: descrição curta limitada ao que ela afirma, status no máximo inconclusivo, não criar valuation ou projeção direcional firme. Sem evidência suficiente: `insufficient_data`, com campos vazios quando apropriado e motivos claros. URLs e números ausentes no bundle não podem aparecer como novos fatos. Se um dado útil faltar, indicar a lacuna para futura coleta determinística.

## Verificação e temporalidade

- Não confundir `published_at`, `modified_at`, `first_seen_at` e `fetched_at`. Dados de preço usam `market_timestamp` para o instante do mercado e `fetched_at` para obtenção no contrato canônico; fundamentos têm divulgação e revisão. `ingested_at`, se usado no banco, é apenas o instante interno da persistência. Adaptadores que retornam `retrieved_at` devem normalizá-lo para `fetched_at`, sem criar um terceiro significado de coleta.
- Análises atuais podem usar matéria antiga recuperada hoje se publicada dentro da janela. Mas uma simulação histórica só pode usar evidência disponível ao sistema no instante simulado, salvo arquivo point-in-time confiável. Recoletar hoje e dizer que o modelo “já sabia” é vazamento temporal.
- Armazenar snapshots imutáveis de bundle e saída; correções geram novas versões. Uma falha não deve apagar uma análise anterior válida.
- Validar referências a IDs existentes, ativo correto, data até `as_of`, limites de período, horizonte permitido, ausência de comandos e enumeração válida. JSON inválido/quebra semântica vai para quarentena; no máximo uma reparação usando o mesmo modelo e as mesmas evidências, sob a mesma quota.
- Backtest rentável não é requisito nem promessa do MVP. Avaliação inicial é de qualidade factual, detecção de mudanças, cobertura e capacidade de abstenção. Quando houver histórico prospectivo suficiente, avaliar hipóteses sem selecionar só acertos e comparar com benchmarks antes de alegar desempenho.
