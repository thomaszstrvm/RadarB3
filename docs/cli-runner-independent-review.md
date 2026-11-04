# Revisão independente do runner CLI

Escopo: staging `cli-integration/backend/cli_runner.py`, `cli_process.py`, integração do worker e cache semântico de bundles. Sem execução de Codex CLI, rede, leitura de autenticação ou mudanças de implementação.

## Achado corrigido

`execute_bounded` capturava `InterruptedError` do callback de cancelamento/encerramento no `except OSError`, convertendo para `cli_process_launch_failed`. O processo era encerrado, mas o worker recebia classificação incorreta em lugar do caminho de encerramento. Reproduzido com transporte falso; autor corrigiu a propagação. O teste independente passou após a correção.

## Resultado

- 12 testes independentes em `test_cli_independent_review.py`: passaram, 0,07 s.
- 50 testes existentes `tests/test_cli_runner.py tests/test_core.py`: passaram, 9,44 s.
- Smoke real de WindowsProcessJob com somente `python.exe -B -c` lendo stdin e imprimindo seu comprimento: exit 0, stdout 10 para `local-test`. Confirma criação/atribuição do Job Object e transporte básico; não executa modelo ou ferramentas.

Verificados por código e testes: gate fechado por padrão e exigência de atestações/hash do executável; ambiente sem chaves de API e sem overrides de usuário; login ChatGPT forçado; modelo único; prompt via stdin; ferramentas desativadas; política MXC de leitura mínima sem rede; rejeição de eventos de ferramentas, erros, ausência de conclusão/uso e JSON não finito; quota antes de subprocesso; reparação única contabilizada; cache sem nova chamada/quota; bloqueio de repetição de tentativa com consumo incerto; mutex de thread/nomeado; contenção antes do envio de dados; limites de saída e timeout; cancelamento e encerramento do processo possuído; validação estrutural/semântica antes de persistir análise; transações do worker protegidas pela lease. Não há fallback para API faturada no caminho revisado.

Sem outro bloqueador concreto encontrado neste escopo. Isso não é prova formal de isolamento: testes do runner usam executores falsos, e o smoke com Python não comprova contenção de todas as atividades do Codex. Homologação MXC/CLI e consumo real da assinatura pertencem à validação do responsável; não foram repetidos. O estado de habilitação final não foi alterado por esta revisão.
