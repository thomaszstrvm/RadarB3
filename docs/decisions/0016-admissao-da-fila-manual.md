# Admissão de comandos pela capacidade do consumidor

Data de implementação · 03/11/2026, São Paulo. Identificador da coordenação · FILA-MANUAL-20261104.

## Contexto

A API aceitava coleta e retry enquanto não existia executor da fila. Um coletor diário com lease válida também produzia esse falso positivo, pois chama `run_once(job_ids=ids)` apenas com seus jobs. O heartbeat comprova a atividade do proprietário, mas não informa quais pedidos serão consumidos.

## Decisão

A lease recebe duas colunas aditivas, `consumer_scope` e `consumer_scope_owner`. A migração deixa leases antigas em `unknown/null`. O loop contínuo normal declara `general`, vinculado ao seu owner. Inicialização avulsa, execução única, recuperação delimitada e utilitários declaram `restricted`. Um claim delimitado rebaixa a capacidade antes da seleção, dentro da transação.

A API admite o tipo de job somente se a lease estiver válida, o escopo for geral, o proprietário da declaração coincidir com o proprietário atual e o modo permitir aquele tipo. A declaração de um proprietário antigo não autoriza um sucessor legado que deixou as novas colunas intactas.

O gate roda em `apply(con)`, após a consulta ao recibo idempotente e antes de alterar a fila. Recibos aceitos e recusados mantêm sua resposta original após mudanças de disponibilidade. Uma solicitação nova depois de corrigir a condição recebe uma chave nova. As rejeições geram recibo 409 e conservam status, tentativas, checkpoints e histórico do job.

`/operations.job_control` comunica os tipos admitidos, o motivo e o vencimento, sem expor o owner. A UI bloqueia enquanto a consulta está pendente ou falhou e reconsulta quando a declaração vence. A API revalida a capacidade em toda nova gravação. As restrições existentes de análise manual, `manual_owner` e coleta sem IA continuam compondo a decisão.

## Alternativas

- Inferir atendimento por heartbeat ou por modo é insuficiente para um consumidor delimitado.
- Iniciar um consumidor persistente automaticamente mudaria a política operacional autorizada.
- Uma tabela adicional de capacidades separaria os conceitos, mas acrescentaria sincronização e limpeza sem benefício necessário para o worker único. As duas colunas tornam explícito o vínculo à lease existente.
- Guardar apenas um booleano permitiria que um takeover de versão antiga herdasse a capacidade do proprietário anterior.

## Consequências

A solução conserva fila, recibos, fencing, mutex e calendário. A leitura adicional usa a lease na mesma transação SQLite de escrita, sem espera de rede ou novo processo. O custo é uma consulta curta por comando e a migração aditiva na próxima abertura do banco pelo runtime atualizado.

Processos antigos ficam sem admissão da fila até declararem capacidade pelo código atualizado. A capacidade confirma o atendimento configurado no momento da admissão. Uma interrupção posterior ainda pode adiar o processamento e permanece sujeita à recuperação normal. Alterar o escopo de um worker não cancela pedidos já admitidos.

Os tipos internos e aliases históricos continuam disponíveis para retry quando o executor os suporta. O contrato de novos enqueues permanece limitado a `JobRequest`.

## Verificação

Reprodução anterior e testes isolados cobrem ausência, expiração, consumidor diário, execução única, recuperação delimitada, troca de owner, migração legada, consumidor compatível, rebaixamento, recibos e mudanças após leitura da UI. A base operacional e o serviço não foram utilizados para executar esses testes. [Relatório e evidências](../qa-20261104/manual-queue/RESULTADOS.md)
