# Backup único e inventário de retenção

Preparação isolada, não ativação operacional. Nenhuma tarefa agendada, serviço,
coleta, IA, restauração, limpeza ou exclusão faz parte deste comando. Não acessar
credenciais. Aplicação e execução sobre dados reais dependem do procedimento e
das autorizações de `operacao-local.md`.

## Interface para uso futuro autorizado

Na raiz do projeto, com o Python homologado:

```
python scripts/backup_cycle.py once --source CAMINHO_EXPLICITO_DO_BANCO --root DIRETORIO_LOCAL_DE_BACKUPS
python scripts/backup_cycle.py inventory --root DIRETORIO_LOCAL_DE_BACKUPS --keep-latest 7 --min-age-days 30
```

Os valores 7 e 30 são exemplos de simulação, não política aprovada. Nenhum desses
comandos foi executado sobre a base operacional nesta preparação. O inventário
não cria diretório nem abre bancos pelo SQLite. Ele recalcula SHA256 e verifica o
recibo; não constitui novo ensaio de restauração nem prova de autenticidade contra
um invasor que consiga modificar recibo e banco juntos.

## Garantias e limites

- `once` reutiliza `scripts.backup.copy_verified`: SQLite backup consistente,
  integridade, FKs, esquema, tipos, tabelas e BLOBs. Não usa cópia bruta da origem,
  Store, migração, checkpoint, restore, coleta ou IA.
- Um diretório exclusivo `backup-<UTC com microssegundos>-<UUID>` contém apenas
  `database.sqlite3` e `receipt.json`. O recibo é escrito exclusivamente, fsync e
  relido após a verificação. Falha deixa os artefatos para inspeção, sem limpeza.
- Bloqueio não bloqueante do SO em `.backup-cycle.lock`, por raiz de destino.
  Nunca remover/truncar o arquivo para desbloquear. O handle libera o bloqueio ao
  encerrar o processo, inclusive por crash; arquivo persistente não significa
  processo ativo. Uma tentativa simultânea falha. Não há exclusão mútua entre
  raízes diferentes. Agendamento futuro deve usar uma única raiz por origem e
  decidir separadamente a identidade de cada execução prevista. Chamadas
  sequenciais explícitas geram backups novos; não há deduplicação por calendário.
- Origem deve existir fora da raiz de backups. Links simbólicos/junctions em
  componentes de caminhos, inclusive nos sidecars de origem `-wal`, `-shm` e
  `-journal` (mesmo links pendentes), são rejeitados antes de alocar a saída ou
  chamar SQLite. Usar disco local, diretório controlado
  pelo usuário e sem alteração concorrente por terceiros. As verificações de
  caminho não são uma sandbox contra troca maliciosa de arquivos durante a leitura.
- Nenhuma rotina remove sidecars. SQLite mantém a própria coordenação WAL/SHM;
  não é possível prometer identidade byte a byte do SHM de uma base viva. Testes
  verificam conteúdo comprometido, origem principal e WAL sintéticos preservados.
- Inventário usa nomes fixos sob cada subdiretório imediato. Não segue caminhos
  do JSON, não percorre recursivamente, rejeita links, hardlink no banco, recibo
  maior que 4 MiB, sidecars/arquivos extras, hash divergente, timestamp inválido,
  futuro ou sem timezone. Tudo incompleto/desconhecido fica `preserve_for_review`.
- Política explícita: `keep_latest` inteiro 1..100000 e `min_age_days` inteiro
  1..36500. Ordenação pelo início UTC, UUID como desempate. Preserva os N mais
  recentes entre recibos válidos e todos com idade inferior ao mínimo. Idade é
  tempo decorrido em períodos de 24 horas UTC, não dias de calendário de São Paulo.
  Fora desses critérios retorna apenas `review_retention_only`, nunca permissão
  de apagar; `deletion_authorized=false` em todos. Dados inválidos não contam
  como backups válidos para satisfazer N. Retenção semanal não está implementada.
- Nenhum RPO/RTO, segundo disco, horário, supervisão, alerta ou recuperação após
  computador desligado foi acordado ou comprovado por esta implementação.
- Sucesso retorna exit code 0 e JSON. Erro retorna 1, mensagem sanitizada e tipo,
  sem caminhos privados/SQL/dados. Argumentos incorretos retornam 2 pelo argparse.
  Inventário com itens inválidos ainda retorna 0: consultar estados individuais.

## Testes

`tests/test_backup_cycle.py` usa exclusivamente bancos sintéticos em `tmp_path`:
destinos exclusivos, conflito do lock, WAL comprometido e transação não
comprometida, recibos corrompidos, hash, idade, política inválida, path no recibo,
falha parcial e preservação de artefatos. Não executa subprocessos ou rede.
Em 11/11/2026, executado pelo Python existente com `-B`, runner
`resolution-0404/run_targeted.py`, seleção `backup`, saída nova
`resolution-0404/backup-tests-final-01`: **65 passed in 1.63s**, exit 0.
A seleção inclui `test_backup_cycle.py`, `test_backup_recovery_cycle.py` e
`test_release_validation.py`. Evidência: `junit.xml`, `pytest.log` e
`validation.json` nessa saída. Guard registrou zero subprocessos, 110 conexões
SQLite sintéticas e 64 tentativas bloqueadas exercitadas pelos testes do guard;
nenhuma execução real de IA. O guard é hook do processo, não sandbox do SO.

Após revisão independente, acrescentada recusa de links nos sidecars de origem
e oito regressões sintéticas (arquivo principal e três sufixos, simulando symlink
e reparse point sem criar links reais). Nova execução pelo mesmo runner/seleção,
saída `resolution-0404/backup-tests-final-02`: **73 passed in 1.68s**, exit 0,
zero subprocessos, 118 conexões SQLite sintéticas e 72 bloqueios exercitados pelos
testes do guard. Essa é a evidência mais recente para o código corrigido.
