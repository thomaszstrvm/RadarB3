# Radar B3

Aplicação pessoal local para investigar eventos e ações/units da B3. FastAPI, React/TypeScript, SQLite WAL e worker único. Sem corretora, ordens ou fallback de API paga. Sinais experimentais, não garantia de retorno.

## Repositório privado e clone

O Git contém código, contratos, testes, documentação e fixtures revisadas. Bancos,
backups, credenciais, atestado operacional de IA, dependências e builds ficam locais.
Os relatórios completos em `docs/qa-20261110` e `docs/qa-20261111` citados abaixo
são evidências locais; não acompanham o clone. As fixtures QA já rastreadas são
mantidas porque os testes dependem delas.

Após clonar, execute com o Python instalado:

```powershell
python -B scripts/setup_git_excludes.py
```

Esse comando acrescenta exclusões a `.git/info/exclude`, preserva regras existentes
e não altera configurações globais. Uma segunda execução não regrava o arquivo.
O `.gitignore` raiz permanece idêntico ao pacote original verificado; `docs/.gitignore`
protege automaticamente atestados e QA gerada. Não use `git add .` como revisão de conteúdo.

Ambiente Python, `node_modules` e `frontend/dist` precisam ser preparados conforme
os lockfiles. Os quatro testes do launcher exigem um build frontend existente.
Clonar não transfere banco real, login, homologação de IA nem tarefas agendadas,
e não inicia a aplicação. O histórico remoto inclui apenas a branch do projeto;
refs internas de checkpoints de ferramentas permanecem locais.

## Estado e continuidade

Este repositório reúne o aplicativo, contratos, testes e decisões técnicas.
Observações de uma instalação, autorizações, consumo de IA e recibos detalhados
permanecem no acervo local. `ENTREGA.md` contém somente o resumo compartilhável.

As correções temporais e os utilitários de operação têm evidências históricas
arquivadas localmente. Elas não comprovam o estado atual do serviço, da tarefa
diária ou do executável homologado. Não reutilize PIDs, builds ou autorizações
de uma descrição antiga; faça o diagnóstico do ambiente antes de atuar.

## Diagnóstico somente leitura

Comando estável disponível nesta instalação:

```powershell
.\.venv\Scripts\python.exe -B scripts/diagnose.py --db data/real/radar.sqlite3 --receipts-dir data/real/daily-receipts
```

Em cópia isolada, use o Python instalado e caminhos explícitos para a raiz e os dados que deseja observar. O comando não instancia Store, não migra banco, não inicia processos do produto e não executa Codex. Usa SQLite ro/query_only e transação, incluindo WAL; lê dois GETs localhost por validate_live.py, sem proxy/redirecionamento. No Windows, consulta somente a tarefa conhecida via PowerShell, oculto. `--skip-windows-task` identifica explicitamente que a agenda não foi observada.

O JSON sai em stdout e separa serviço indisponível, erro do observador, coleta parcial/perdida, clocks de dados, calendário e compatibilidade do atestado. Não verifica login atual nem envia inferência. `database_idle=true` não autoriza parar processo: falta confirmar identidade atual da árvore e do socket.

Para verificar um backup específico, forneça **ambos** `--backup-report <recibo.json>` e `--backup-file <copia.sqlite3>`. Confere hash e conteúdo real contra o recibo. Data de verificação não é data de atualização dos dados. Sem esses argumentos, backup permanece não verificado, não saudável por omissão.

Saídas: 0 = observações requeridas sem ressalvas cobertas pelo comando; 1 = atenção/limitação conhecida (por exemplo serviço parado, coleta parcial, backup/agenda não observados ou atestado indisponível); 2 = falha de observação ou verificação. Nenhum código autoriza operação, prova cobertura financeira ou substitui inspeção do JSON.

## Modos: não são equivalentes

| Modo | Efeito |
|---|---|
| `collection-only --no-worker` | Consulta; sem worker, coleta ou inferência por este processo. A tarefa Windows separada continua independente. |
| `collection-only` | Worker geral; agenda/coleta, sem IA. Pode conflitar com a tarefa diária. Não iniciar simultaneamente sem reconciliação. |
| `manual-analysis` | Worker dedicado a pedidos manuais; sem agenda/coleta. Requer autorização, quota e atestado vigente. |
| `analysis-enabled` | Worker pode executar análises/agenda conforme configuração. Não usar como modo de consulta. |

`run.py` sem opções inicia worker de coleta. `Start-Radar.ps1` não aceita manual-analysis. Configuração habilitada não vence restrição do modo nem gate de homologação.

## Início, parada e recuperação

Procedimento completo e condições: [roteiro operacional](docs/operacao-local.md). Nenhuma ação abaixo foi executada neste ciclo.

Após autorização e reconciliação de processos, porta, banco, lease e build aprovado, o início de consulta é:

```powershell
.\.venv\Scripts\python.exe -B run.py --mode real --no-worker --execution-mode collection-only
```

Bind somente `127.0.0.1:8765`. Não iniciar segunda instância para contornar porta ocupada. Não mudar modo nem usar botão de análise para testar disponibilidade. Não iniciar worker para resolver automaticamente uma pendência de diagnóstico.

Antes de Ctrl+C no terminal próprio: exigir zero jobs queued/running/retry_wait, zero runs prepared/started, confirmar a árvore/identidade atual, impedir novo pedido concorrente e revalidar. O launcher tem fallback terminate/kill após oito segundos; **não usar Ctrl+C durante análise** nem prometer parada totalmente graciosa. Sem terminal/identidade confiável, parar o procedimento, não usar kill global ou PID histórico. O helper de parada de QA é específico da árvore antiga e não é comando genérico homologado para este ciclo.

Depois: verificar saída dos processos próprios, porta, lease e preservação. Não reiniciar nem reenviar inferência para resolver resultado incerto. Nunca restaurar ledger antigo sobre produção.

## Backup/restauração verificáveis

O utilitário mantém a interface existente e acrescenta recibo exclusivo:

```powershell
.\.venv\Scripts\python.exe -B scripts/backup.py backup data/real/radar.sqlite3 backups/NOVO.sqlite3 --report backups/NOVO.json
.\.venv\Scripts\python.exe -B scripts/backup.py restore backups/NOVO.sqlite3 backups/ENSAIO-NOVO.sqlite3 --report backups/ENSAIO-NOVO.json
```

**Exemplos de uso, não comandos de ativação automática.** Os destinos devem ser inéditos, inclusive sidecars e recibos. SQLite backup preserva WAL comprometido; confere integridade, FK, schema e hashes tipados de todas as tabelas, incluindo BLOB e quota consumed/uncertain. Não copiar só o arquivo principal de banco aberto. Falha não apaga fonte nem destino parcial; não usar cópia sem verificação positiva.

Restaure ensaios somente em destinos novos. Uma verificação de cópia histórica não torna seus dados atuais nem autoriza restaurá-la sobre a base ativa. Agenda de backup, retenção e exclusão exigem definição própria; consulte o roteiro operacional.

## Validação reproduzível

Usando o Python instalado, rode o runner em **diretório de saída inexistente**:

```powershell
python -B scripts/validate_release.py --mode operational --output-dir CAMINHO-NOVO
python -B scripts/validate_release.py --mode full --output-dir OUTRO-CAMINHO-NOVO
python -B scripts/verify_spec.py
```

Veja [limites e detalhes](docs/release-validation.md). O runner bloqueia rede externa, CLI real e escritas/bancos fora da saída; somente os Python instalados são permitidos para testes revisados. Não é sandbox universal; filhos Python não herdam o audit hook. O teste Windows usa árvore Python benigna própria, não serviço real. Relatórios não são somados entre rodadas repetidas.

Frontend sem mudança neste ciclo. Os 260 testes e build de 10/11 são evidência histórica, não nova execução. Instalação limpa ainda não revalidada. Os lockfiles continuam fixos; instalar dependências depende de decisão separada. A DLL SQLite local é verificada por hash, sem substituição global.

## Limites atuais

Presença de preços para uma amostra não significa cobertura ampla do universo elegível. Notícias, fundamentos e retorno em 63 intervalos dependem de cobertura suficiente. Uma análise conserva seu corte; coleta nova não recalcula hipóteses automaticamente. Calendário documental tem intervalo finito e homologação passada não cobre binário novo. Disponibilidade contínua depende de comprovar rotina diária, backup recorrente e critérios de recuperação.

Diretivas: `AGENTS.md`, contratos em `schemas/`, configurações sem segredos e documentos 01–09. `scripts/verify_spec.py` protege o ZIP e os 34 originais. Não ler credenciais do Codex, alterar MXC, quotas, schema financeiro, prompts ou agenda para contornar bloqueios.
