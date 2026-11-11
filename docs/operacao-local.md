# Operação local: diagnóstico, recuperação e ativação revisável

Este guia descreve procedimentos. Observações de uma instalação, autorizações,
identificadores de conversas, consumo e recibos ficam no acervo local. Sua presença
no repositório não comprova serviço ou agenda ativos e não autoriza uma execução.

## Diagnóstico e início seguro

1. Executar diagnose.py somente leitura com paths explícitos. Guardar saída em destino novo se necessário. Confrontar horário local, recibos, tarefa e status da API; não tratar Ready como resultado.
2. Se houver erro do observador, corrigir acesso/configuração do diagnóstico antes de concluir que o produto está saudável ou parado.
3. Para início futuro autorizado: confirmar ausência de processo concorrente, porta, jobs/runs e lease, build exato e estado do banco. A observação ociosa pode envelhecer; repetir imediatamente antes da ação.
4. Iniciar somente o modo escolhido explicitamente. Consulta segura: run.py --mode real --no-worker --execution-mode collection-only. A tarefa diária independente permanece como está.
5. Conferir health/operations após início. Modo e worker devem corresponder ao pedido; não presumir que o launcher supervisiona o Windows após reboot.

## Parada e recuperação sem duplicação

- Bloquear novos pedidos humanos durante a janela. Confirmar zero jobs queued/running/retry_wait e zero analysis_runs prepared/started, além de job_runs inacabados. Não apagar ou liberar reservas para obter zero.
- Identidade: PID atual, CreationDate, caminho, argv, parentesco, perfil/modo, socket pertencente à API e console restrito. Na consulta exigir lease ausente; no manual exigir lease única compatível/owner estável. Não reutilizar PIDs de recibos.
- Ctrl+C somente no terminal próprio depois da conferência; o launcher pode recorrer a terminate/kill após oito segundos. Não é uma garantia de parada graciosa de uma inferência em andamento.
- Sem terminal próprio/identidade inequívoca, não improvisar taskkill. O helper histórico safe_service_stop.py é específico de caminhos e árvore de 5/7 processos. Adaptá-lo a outra árvore requer revisão e teste; não foi promovido aqui.
- Depois: confirmar saída dos handles próprios, porta livre, lease ausente e dados/ledger preservados. Resultado incerto interrompe o procedimento; não repetir sinal/execução às cegas.
- Recuperação não autoriza reenviar IA ou executar coleta perdida. Runs incertos, reservas e histórico devem permanecer auditáveis. Banco antigo nunca substitui o ativo.

## Proposta de backup, ainda não ativada

- Proposta inicial: cópia diária após confirmar encerramento da coleta, destino novo, recibo verificado e alerta se faltar. Não ativado; horário/modalidade dependem de decisão do usuário e do comportamento real da máquina.
- Retenção proposta: sete diárias e quatro semanais. Primeiro inventário/simulação; nenhuma exclusão é realizada automaticamente por este lote.
- Segundo local: cópia offline ou outro disco escolhido pelo usuário. Não enviar banco a nuvem/terceiro sem aprovação de destino e conteúdo.
- Objetivos propostos, ainda não acordados nem comprovados: RPO até 24 horas; RTO até 30 minutos. Cronometragem do ensaio histórico não prova esses objetivos em produção.
- Fazer backup consistente com API SQLite, não cópia do .sqlite3 ignorando WAL. Destino, sidecars e recibo inéditos; verificar integridade/FK/schema/tabelas/ledger/BLOB antes de chamar a cópia utilizável.
- Restore sempre em destino novo, com verificação integral e preservação do original. Seleção operacional do banco restaurado exige janela/decisão separada e reconciliação do consumo mais recente.

Use um backup identificado e verificado no ambiente local. Não publique seus recibos, hashes de dados privados ou contagens operacionais no repositório.

## Aplicação futura e rollback

Para aplicação local: revisão concluída, testes no código exato, hashes antes/depois, ausência de execução concorrente, backup atual e identificação explícita do build. A aplicação não ativa serviço nem rotina de backup automaticamente.

Confira o build correspondente à versão atual. Copie somente a lista revisada de arquivos, sem substituir banco, configuração, atestado ou prompts por artefatos de outro ambiente. Conferir hashes depois; não iniciar serviço como efeito colateral.

Rollback de código: parar somente de modo autorizado/seguro, restaurar arquivos do backup correspondente após verificar ausência de novas alterações. Não rollback de banco/ledger para acompanhar código. Falha parcial exige reconciliação; não usar comando de limpeza recursiva.

## Pendências por prioridade

P0: reconciliar conflitos históricos de worker, comprovar rotina diária continuada (parcial não é cobertura completa), estabelecer rotina verificável de backup atual, definir RPO/RTO/local/retensão e validar procedimento operacional no ambiente atual sem inferência.

P1: revalidar instalação limpa; adequar homologação ao executável disponível somente com autorização específica quando houver chamada real; acompanhar expiração documental; definir cobertura mínima de mercado sem contratar fontes por conta própria.

P2: avaliação de qualidade analítica e calibração, sem prometer poder preditivo. Não redesign, ampliação de fontes ou mudança de sinais neste ciclo.

## Um modelo mental de leitura segura

Bloco central de database_state:

```python
con = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=3)
con.execute('PRAGMA query_only=ON')
con.execute('BEGIN')
```

1. mode=ro exige banco existente e abre a conexão sem autorização de escrita; não cria banco vazio silenciosamente.
2. query_only reforça que consultas desta conexão não mudam registros. Não substitui permissões do sistema operacional.
3. BEGIN mantém uma visão consistente durante o diagnóstico. As consultas seguintes não misturam contagens de instantes diferentes; o SQLite considera WAL comprometido.

Exercício: há dois diagnósticos, um sem BEGIN e outro com BEGIN. Uma coleta insere artigo entre a leitura do total de artigos e do total de revisões. Explique qual inconsistência pode aparecer no primeiro e por que não deve ser resolvida inventando um total zero. Não altere a base real para testar.
