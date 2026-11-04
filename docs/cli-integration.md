# Integração da análise por assinatura — preparada, desabilitada por padrão

Esta alteração foi preparada em `cli-integration` a partir dos arquivos finais de `<caminho-local>`. Não altera automaticamente a instalação final. Não executa login, probes, leitura de arquivos de credenciais ou compra de créditos. A execução real depende da homologação feita pelo responsável pela integração.

## Arquivos que compõem a alteração

- `backend/cli_process.py`: transporte com stdout/stderr limitados, deadline, cancelamento, mutex e Windows Job Object.
- `backend/cli_runner.py`: gate de homologação, comando fixo, validação, cache, quota e auditoria.
- `backend/analysis_schedule.py`: seleção determinística ampla, sem chamadas de IA durante o agendamento.
- `backend/worker.py`: integra apenas jobs de análise solicitados; continua separado da API.
- `config/ai-runtime.json`: começa com `enabled=false`.
- `tests/test_cli_runner.py`: testes isolados, sem rede nem execução do Codex CLI.

Os demais arquivos copiados em `cli-integration` são somente contexto para testes. Não devem sobrescrever alterações mais recentes do aplicativo final. O patch `worker.patch` identifica exatamente a modificação do worker.

## Condições para habilitar

O runner exige `enabled=true` tanto na configuração quanto no relatório `docs/cli-validation.json`. O relatório deve conter `isolation_verified=true`, `model_verified=true`, `tools_disabled_verified=true`, `authentication="ChatGPT"`, `model="gpt-6-astra"`, `policy_version="radar_mxc_minimal_v1"`, caminho absoluto `executable`, `executable_sha256` e `version`. O SHA-256 do executável é conferido antes de cada tentativa; substituição da CLI bloqueia o caminho até nova homologação. A API pode consultar `cli_runner.status()` sem iniciar subprocessos.

`live_analysis_tested` é informativo: somente deve virar `true` depois de uma execução real validada. Não é exigido para o primeiro smoke test, evitando uma dependência circular. A configuração não habilita outras plataformas nem outros modelos automaticamente.

## Controles efetivamente implementados

- Um modelo fixo: `gpt-6-astra`, esforço `low`, provider `openai`, login forçado `chatgpt`. Nenhuma chave de API paga é herdada; não existe fallback.
- `--no-daemon`, `-a never`, `exec`, `--ignore-user-config`, `--ephemeral`, `--skip-git-repo-check`, saída JSON/schema. Não usa `shell=True`, bypass de sandbox ou alteração de política de execução.
- Perfil MXC `radar_analysis`: apenas `:minimal=read` e leitura do diretório temporário da tentativa; rede arbitrária desabilitada. Não concede acesso ao projeto, banco, Python ou PowerShell no perfil do modelo.
- Shell, apps, plugins, hooks, memórias, browsers, computer use, imagens e multiagentes desabilitados. Web search desabilitada; MCP vazio; documentos de projeto desativados. `skip_host_skill_discovery` permanece experimental, explicitamente registrado.
- Ambiente por allowlist de variáveis operacionais Windows, incluindo `SYSTEMROOT` e `SYSTEMDRIVE`; remove `OPENAI_API_KEY`, `CODEX_API_KEY`, `CODEX_HOME`, tokens de dados e proxies herdados. Autenticação é reutilizada pela própria CLI, sem leitura de arquivos pela aplicação.
- Prompt fixo e envelope JSON passam somente pelo stdin. O bundle já precisa ser válido e idêntico ao snapshot persistido. Apenas o schema, com IDs e horários fixados como constantes, é colocado na pasta temporária.
- Windows Job Object recebe o processo antes do envio do prompt. Fechar o handle encerra sua árvore, sem pesquisar ou matar processos por nome. Mutex de sessão e lock de thread impedem duas análises simultâneas; o lease do worker continua protegendo transações.
- Timeout máximo de 300 segundos, stdout de até 2 MB, stderr de até 64 KB. Saídas ficam em memória; banco recebe apenas hash, estado, metadados e uso numérico, nunca transcrição bruta ou stderr integral.
- Eventos de ferramentas ou eventos desconhecidos são rejeitados, mesmo com JSON final válido. Erros/avisos de runtime no stderr são rejeitados. Só a advertência conhecida de `skip_host_skill_discovery` e a mensagem de leitura de stdin são aceitas. Saída precisa conter conclusão de turno e contagem de input/output tokens.
- Schema e semântica são validados antes da publicação. Uma única reparação é permitida, com o mesmo bundle/modelo; apenas códigos de erro entram no novo pedido, sem reaproveitar instruções do texto rejeitado.

## Persistência, quota e retomada

O módulo acrescenta `analysis_runs` por migração aditiva versão 2. Cada chamada reserva quota durável antes do envio. O teto local `max_analyses_per_day`, de 0 a 10, usa o dia de São Paulo e **inclui reparações**; não é a franquia oficial da assinatura. Uso de tokens é salvo somente quando informado pela CLI.

Cache usa bundle/modelo/prompt/schema. Snapshots reutilizados conservam o `as_of` original. Uma saída aprovada entra na tabela imutável `analyses`; inválidas deixam apenas um registro de quarentena por hash/código. Atualizações de ferramentas/política do wrapper entram no hash do prompt.

Timeout, cancelamento, falha de execução ou interrupção após o envio deixam o consumo incerto contabilizado. O mesmo cache key não é reenviado automaticamente, nem por retry cego do job, enquanto houver tentativa anterior sem resultado aprovado. O aplicativo informa `cli_previous_attempt_requires_review`. Uma revisão explícita desse estado exige procedimento operacional; este patch não cria um botão que apague o histórico ou libere a quota.

`automatic_analysis=false` é o padrão. Quando habilitada explicitamente após o smoke test, a agenda seleciona ativos elegíveis com menção explícita em notícias com publicação conhecida dentro da janela atual. Favoritos não limitam o universo. A ordem é: ainda não analisados, análise mais antiga, notícia mais recente e ticker. Uma chave por ativo/dia coalesce execuções; a quantidade enfileirada não pode exceder quota diária restante menos jobs de análise ativos no dia. Reservas e reparações entram na quota; a contagem é conservadora quando um job em execução já tem reserva. Fontes expiradas, desconhecidas ou futuras não geram candidatos. A agenda não chama a CLI; apenas o worker serial consome a fila.

## Validação executada nesta preparação

Comando: `../.venv/Scripts/python.exe -m pytest tests/test_cli_runner.py tests/test_core.py -q`, executado em `cli-integration`. Testes de CLI usam subprocessos falsos e não consomem assinatura. O conjunto cobre gate e hash alterado, ambiente sem chaves, política/args, cache, quota antes da chamada, reparação única, rejeição de ferramentas, erro em stderr, uso ausente, timeout, cancelamento e limite de saída. A homologação nativa do Job Object e os probes reais do modelo pertencem ao processo de revisão do responsável; não são reivindicados por esses testes simulados.

Limitações: a CLI e `skip_host_skill_discovery` são versões em desenvolvimento; o comportamento é validado para o binário registrado. O validador estrutural/numérico não prova automaticamente toda a sustentação semântica de uma alegação financeira. A ausência de dados continua exigindo abstenção.
