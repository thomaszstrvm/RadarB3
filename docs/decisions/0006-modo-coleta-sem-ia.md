# ADR 0006 — modo de processo para coleta sem inferência

Data: 2026-10-29. Estado: implementado e testado em isolamento; não ativado em produção.

## Contexto

`automatic_analysis=false` impede novas seleções, mas não bloqueia jobs manuais, retries ou trabalhos antigos. Alterar `enabled` persistente misturaria homologação com a decisão operacional e modificaria a configuração real. O processo precisa poder coletar preservando a fila de análises sem consumir tentativas/quota.

## Decisão

Introduzir `collection-only` e `analysis-enabled`. `run.py` e o CLI de worker usam collection-only por padrão. O launcher passa o mesmo modo por argumento e ambiente RADAR_EXECUTION_MODE aos filhos; não grava preferências ou atestado. A API de fábrica sem banco explícito também assume collection-only. Chamadores Python com banco explícito/Worker/Store preservam compatibilidade analysis-enabled na ausência de configuração; ferramentas devem informar o modo explicitamente. Valor desconhecido falha na resolução.

Persistir o modo junto de worker_lease por coluna aditiva, para que Store.claim filtre analyse/analysis dentro da transação. Esse filtro evita gastar tentativas e evita bloquear a coleta atrás de análises prioritárias. Recuperação de running abandonado permanece distinta de retry explícito e de paused/blocked; não se apagam jobs ou reservas. A checagem da coluna e ALTER são serializados com BEGIN IMMEDIATE; leases antigos recebem analysis-enabled, correspondente ao comportamento anterior.

Barreiras adicionais no Worker.execute (antes do bundle), no despacho com handlers e no AnalysisRunner.run (antes da configuração/quota) impedem inferência em caminhos diretos. schedule_analyses também respeita o ambiente. API collection-only recusa análise e retry dos dois aliases com 409, mantendo consultas e coleta. Um worker collection-only ativo também restringe uma API configurada com IA. Divergência inversa é comunicada explicitamente: a API não pode desligar um processo independente que já executa IA.

O status separa homologação, permissão do processo, lease ativo/ausente/vencido, relógio da consulta, último sucesso de coleta, última versão recebida e snapshot analítico. 304/duplicatas não criam versões, mas avançam o último sucesso registrado pelo provedor.

## Alternativas

- Somente alterar automatic_analysis: não cobre a fila existente.
- Apagar/pausar repetidamente todos os jobs IA: altera histórico e cria competição desnecessária; não usado.
- Infraestrutura de filas ou processos separados por fornecedor: não há gargalo que justifique dependências adicionais.
- Somente UI: pode ser contornada por HTTP/execução direta; usada apenas como comunicação adicional às barreiras backend.

## Consequências

Positivas: política reversível sem alterar configuração real, coleta progride, zero tentativas/quota IA no modo restrito, uma única fila e um único worker. Configuração de processo e API concordam pelo launcher. Jsdom é declarado/fixado só em desenvolvimento para preservar o teste React/DOM numa instalação limpa.

Negativas/limites: trocar modo exige parar a instância e iniciar outra; a API não muda o modo de um worker já ativo. Chamadores Python legados precisam escolher o modo explicitamente para obter a proteção. Relógio local e lease continuam sendo a base da observabilidade. Homologação técnica não valida qualidade financeira. O build operacional e a base real não foram atualizados nesta etapa.

## Verificação

Testes de fila mista, aliases, prioridades, retomada, retry, exclusão do segundo worker, modo direto/runner, preservação do histórico/ledger, relógio São Paulo, API/UI e propagação do launcher. Coleta e executor são falsos; tests/conftest.py continua proibindo Codex real. Resultados finais no relatório da etapa.
