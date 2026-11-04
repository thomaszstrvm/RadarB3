# ADR 0011 — Execução manual pela interface sem agenda

Data: 31/10/2026. Estado: implementado, validado em isolamento e ativado após revisão operacional. O teste servido reutilizou cache sem IA; o estado final manual normal foi reconciliado em01/11 às02:07UTC (31/10 em SãoPaulo). Evidências em `docs/qa-20261031/manual-ui-operable/RESULTADOS.md`.

## Contexto

O POST de análise já persistia job e recibo idempotente, mas `--no-worker` não executava o job. O worker normal agenda ingestão e pode consumir a fila geral. A demonstração precisa de análise acionada pela UI, serial e limitada, sem reativar agenda, coleta ou fila antiga. A arquitetura existente separa API e worker; BackgroundTasks não é sua fila durável.

## Decisão

Adicionar o modo explícito `manual-analysis`, propagado pelo launcher à API e ao único worker. Não alterar defaults dos modos anteriores nem configuração persistente. Esse modo exige worker: o launcher recusa sua combinação com `--no-worker`.

A API recupera primeiro o recibo idempotente. Para um comando novo, na mesma transação curta, exige lease manual ativa, capacidade CLI homologada, saldo local e ausência de outro job manual queued/running/retry_wait do owner atual. Grava `manual_owner` no payload usando a lease do servidor, nunca um campo escolhido pelo cliente. O limite é um job admitido; não uma fila ilimitada. A reserva de quota continua ocorrendo no runner imediatamente antes da chamada e permanece a barreira autoritativa.

O worker manual só seleciona jobs `analyse`, queued, marcados com seu owner atual, e passa o ID explicitamente ao claim. Não chama a agenda. `Store.claim` verifica novamente modo, owner, tipo e estado dentro da transação; execução direta também recusa escopo incompatível. Workers normais excluem jobs marcados como manuais de claim e recuperação automática. Não são necessárias tabelas ou colunas novas.

POST de coleta/execução genérica é recusado nesse modo. Retry genérico de jobs manuais é recusado também fora dele: um novo clique é uma solicitação explícita nova, ainda sujeita ao guard de cache/tentativa anterior. Reparação é limitada a zero por chamada e pela política de processo manual, sem editar `ai-runtime.json`. Modelo, MXC, transportes e limite diário máximo dez permanecem iguais.

O owner delimita apenas o despacho local e não entra no bundle, prompt ou chave de cache. Um resultado existente pode completar o job com `cache_reused=true` e `calls=0`, sem reserva adicional. Com saldo zero, um POST novo é recusado conservadoramente; os resultados salvos continuam acessíveis e recibos aceitos anteriormente continuam recuperáveis. Não reconstruir bundle dentro da transação API para tentar provar um acerto de cache.

## Interrupção e reconciliação

Lease vencida não prova que uma chamada terminou. Antes de assumir uma lease manual vencida e reconciliar jobs, o worker adquire o mesmo `AnalysisMutex` global usado pelo runner. O runner mantém esse mutex até fechar seu Job Object Windows, responsável pela árvore local do subprocesso. Mutex ocupado ou lease ainda válida impede a tomada e preserva os jobs.

Sob o mutex, o worker adquire nova lease com `recover_job_ids=[]`. A nova posse impede qualquer escrita/reserva futura do owner antigo pelo fencing já existente. Só então jobs anteriormente marcados como manuais, com outro owner e ainda não terminais, recebem `blocked/manual_session_interrupted`; seus job_runs abertos ficam interrupted. A recuperação não reenvia trabalho, não modifica análises imutáveis e não libera reservas nem apaga uso incerto. É idempotente.

A checagem de takeover fica também dentro da transação de `Store.acquire_worker`, fechando a corrida entre inspeção e aquisição. Um worker collection-only ou analysis-enabled que encontra lease manual vencida também precisa adquirir AnalysisMutex antes de assumir; ele não reconcilia nem executa os jobs manuais.

Isso garante contenção e ausência de reenvio automático local, não o encerramento de processamento remoto já recebido pelo provedor. Mutex abandonado não permite afirmar que não houve consumo. Quota reserved/uncertain continua contando. Um teste Windows com processos Python benignos verifica a árvore local e a recuperação, sem chamar Codex ou alterar o transporte homologado.

## API e interface

`operations.manual_analysis` informa state ready/busy/unavailable, pending_limit=1, repair_attempts=0 e quota local used/limit/remaining/period de São Paulo. Reservas consumidas, reservadas e incertas contam; apenas released não conta. Configuração inválida fecha a admissão com limite efetivo zero e código explícito. `ai.enabled` representa capacidade atual de admitir um comando novo.

`analysis-job.manual_scope` distingue current/outside sem expor o owner. A fila antiga permanece visível, mas não é apresentada como execução do worker manual atual. Posição é um somente para job queued do escopo atual; fora dele, null com manual_scope_inactive. Resultado aprovado e diagnósticos existentes permanecem no contrato. A UI mostra cache reutilizado sem afirmar nova inferência e bloqueia controles de coleta/retry indisponíveis.

## Alternativas

- Ligar o worker normal ou apenas desabilitar a agenda: ainda permitiria consumir fila geral; descartado.
- Executar no request HTTP: bloquearia resposta longa e complicaria cancelamento/recuperação; descartado.
- BackgroundTasks ou subprocesso após commit sem fila observada: abre janela entre recibo gravado e execução perdida; descartado.
- Nova fila/tabela/infraestrutura distribuída: desnecessária para um slot e um usuário local.
- Reenviar jobs interrompidos ou liberar quota por timeout: poderia duplicar consumo; descartado.

## Consequências

Positivas: execução operável pela UI com estado durável, limite serial, idempotência e diagnóstico existentes; nenhuma agenda ou coleta escondida; histórico preservado; cache não é artificialmente invalidado.

Negativas: segundo comando recebe ocupado; reinício interrompe o escopo anterior em vez de retomá-lo; saldo zero bloqueia comandos novos mesmo se um cache pudesse existir; um mutex global ocupado pode impedir iniciar o worker até a chamada ativa terminar. Esses limites são explícitos para evitar execução e contabilização ambíguas.

Ativação requer backup/reconciliação e plano separado. Código/testes isolados não autorizam promoção, alteração da base real, inferência ou importação de fundamentos.
