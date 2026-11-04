# ADR 0002 — acompanhamento persistente da análise por ativo

Data: 2026-10-26. Estado: aceito no escopo autorizado.

## Contexto

A ação de analisar descartava a resposta 202 e remetia o usuário à lista geral de execuções. O banco já preserva jobs, resultados e tentativas. Recarregar ou trocar de ativo deve recuperar o acompanhamento sem reenviar comandos nem consumir IA. A interface precisa distinguir a execução atual da última análise validada.

## Decisão e justificativa

Adicionar GET `/api/v1/assets/{asset_id}/analysis-job`, exclusivamente de leitura. Selecionar um job ativo (`running`, `queued`, `retry_wait`) do ativo; se não houver, selecionar o mais recente por criação/ID. Calcular a posição apenas na fila global pronta, pela mesma ordenação do claim. Escolha, posição e correlação do resultado usam uma única transação de leitura SQLite. O resultado só é anunciado como disponível se `job.result.analysis_id` apontar para análise persistida daquele ativo.

O frontend retém a resposta do POST existente e consulta o estado persistido. As consultas GET são sequenciais, separadas por cinco segundos, somente com página visível. Cada consulta tem timeout de dez segundos; o acompanhamento termina em dez minutos, após três erros consecutivos ou em estado não ativo. A desmontagem aborta a consulta. Retomar acompanhamento faz GET; não repete POST. A conclusão recarrega apenas o detalhe, mantendo filtros e posição da lista.

O último resultado válido permanece na tela com sua data. Não converter progresso binário do executor em uma estimativa percentual. Aplicar critérios do workflow próprio: estados explícitos, foco preservado, anúncio curto e recuperação compreensível. Nenhuma nova dependência visual ou skill externa.

## Alternativas consideradas

- Consultar os últimos jobs da lista geral: pode omitir uma execução do ativo após 200/500 outros jobs; não fornece uma posição consistente na fila.
- Guardar apenas job_id no localStorage: não recupera trabalhos iniciados em outra aba ou pela agenda e pode conservar identidade obsoleta.
- WebSocket/SSE: acrescenta conexão persistente e reconexão sem necessidade demonstrada neste aplicativo local.
- Polling permanente: desperdiça consultas em abas ocultas e quando já há resultado terminal.

## Consequências

Positivas: nenhuma migração, escrita ou chamada de IA no novo GET; retomada após reload; fila coerente com o worker; dados anteriores preservados; manutenção e testes sem infraestrutura nova. SQLite WAL permite leitura curta junto ao worker. A rota reutiliza a proteção e o serializer já existentes.

Negativas: até cinco segundos para observar mudanças; acompanhamento de dez minutos requer retomada manual; cálculo percorre a fila local e não foi medido sob grande volume; o detalhe mostra a análise mais recente do ativo, que pode diferir da execução selecionada, e informa essa diferença. Leitura não comprova vida do worker nem prevê tempo restante. O contrato preserva a idempotência horária já existente no POST: um clique repetido pode devolver a mesma execução; não promete sempre criar outra.

Nenhuma mudança em quota, modelo, prompt, MXC, transporte, scheduler ou fontes. O estudo Amicro é documental e separado desta decisão.
