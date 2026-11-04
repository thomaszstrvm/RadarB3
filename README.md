# Radar B3

Aplicativo local de investigação de eventos e ações/units da B3. API FastAPI, React/TypeScript, SQLite WAL e worker serial com fila durável. Nenhuma corretora, ordem ou serviço pago novo. Interface em português.

**Verificação operacional:** consulte `/api/v1/operations` antes de iniciar outra instância. O diário de instalação, coleta e disponibilidade permanece no acervo local; esta versão do README não certifica o estado atual do serviço.

## Iniciar no PC validado

Se a instalação já responder em 8765, use a instância existente. Os comandos abaixo são para uma inicialização deliberada após verificar processos e lease. Abra PowerShell nesta pasta:

```powershell
Set-Location '<pasta-do-projeto>'
.\.venv\Scripts\python.exe run.py --no-worker --execution-mode collection-only
```

Comando acima: consulta sem worker e sem inferência. Abra **http://127.0.0.1:8765** e mantenha o terminal aberto. **Ctrl+C** encerra os processos iniciados pelo launcher. O atalho equivalente é `Start-Radar.ps1 -NoWorker -ExecutionMode collection-only`; não é necessário alterar ExecutionPolicy global. Se a porta estiver ocupada, acrescente `--port 8766`; o programa não interrompe outro aplicativo.

Modos disponíveis (coleta sem IA foi ativada em 29/10; não execute outra instância sobre a mesma base):

```powershell
# Coleta automática, sem IA: mantém análises da fila aguardando, sem tentativas.
.\.venv\Scripts\python.exe run.py --execution-mode collection-only
# Operação com IA: somente após autorização, com quota e homologação vigentes.
.\.venv\Scripts\python.exe run.py --execution-mode analysis-enabled
```

Pare a instância anterior com Ctrl+C antes de mudar o modo. Para retornar à consulta, use o primeiro comando com `--no-worker`. Para voltar da IA à coleta, pare e inicie `collection-only`; os jobs e contadores não são apagados. `run.py` sem opções agora inicia coleta sem IA; nunca use o comando abreviado como consulta sem worker. Ambos os filhos recebem o mesmo RADAR_EXECUTION_MODE. Não misture processos iniciados à parte: o painel avisa divergência, mas não desliga um worker independente.

O painel operacional de coleta já foi instalado em 29/10. As correções de interface de 30/10 ficam em build QA separado, sem promoção automática. Para uma futura promoção autorizada, pare a instância e prepare backup/ensaio e cópia revisáveis:

```powershell
$radarStamp = Get-Date -Format 'yyyyMMdd-HHmmss'
.\.venv\Scripts\python.exe scripts/backup.py backup data/real/radar.sqlite3 "backups/before-collection-$radarStamp.sqlite3"
.\.venv\Scripts\python.exe scripts/backup.py restore "backups/before-collection-$radarStamp.sqlite3" "backups/restore-check-$radarStamp.sqlite3"
# Só prossiga se ambos confirmarem integridade; mantenha esses destinos novos.
Copy-Item -LiteralPath frontend/dist -Destination "backups/frontend-before-$radarStamp" -Recurse
Copy-Item -Path 'docs/qa-20261030/ui-regressions/frontend/dist/*' -Destination frontend/dist -Recurse -Force
```

O procedimento de backup/ensaio foi executado na ativação de 29/10, não para promover as correções de 30/10. A coluna aditiva execution_mode já foi aplicada nessa ativação. Os comandos acima são um procedimento futuro: confira o relatório do build escolhido antes de executá-los. Depois selecione um dos comandos de modo acima. Para retornar à interface anterior, pare os processos e copie o conteúdo de `backups/frontend-before-<stamp>` de volta para frontend/dist, mantendo o backup. Não restaure banco por cima da produção nem apague ledger para trocar o modo. Restauração real de dados exige decisão separada e destino novo.

No modo sem IA, claim ignora analyse/analysis, execução e runner recusam inferência e a API rejeita análise manual/retry com 409. blocked/paused não retomam sozinhos; queued/retry_wait ficam guardados. Coleta pode avançar na fila. `/api/v1/operations` distingue worker ativo/ausente/heartbeat vencido, homologação, modo, última coleta bem-sucedida, último dado recebido e snapshot da análise. O horário de consulta do painel não é atualização financeira.

Banco real em `data/real/radar.sqlite3`. Para abrir um perfil separado e vazio, `run.py --mode demo`; o banner identifica demonstração e coleta externa fica desativada. Nenhum dado fictício é inserido automaticamente. Para consulta sem agenda, `run.py --no-worker`.

O worker executa notícias a cada 60 minutos e catálogo semanalmente enquanto estiver ligado. Ao reiniciar, agrupa execuções perdidas e recupera leases/checkpoints. Preços automáticos exigem calendário B3 verificado; **Atualizar dados** permite coleta manual. Não há serviço instalado no Windows, tarefa agendada ou promessa de 24 horas.

## Usar e verificar

1. **Atualizar dados** enfileira catálogo, notícias, preços e macro. Em **Execuções**, veja cada etapa e falha; cancelamento e repetição são persistentes.
2. **Mercado** mostra ações/units elegíveis e cadastro ainda sem classificação. Favoritos não limitam a varredura. Busque `PETR4`, abra o detalhe e confira série, datas e evidências.
3. **Eventos** lista notícias agrupadas conservadoramente, com link da fonte e nível de acesso. Só manchete não vira hipótese forte.
4. **Fontes e cobertura** distingue não pesquisado, bloqueado, parcial e disponível. Três meses são calendário em São Paulo, não 90 dias fixos. RSS não garante arquivo de todo esse período.
5. **Analisar ativo** depende do modo com IA e da homologação; fica indisponível em coleta sem IA. Análises históricas permanecem consultáveis com seu snapshot original. Uma coleta nova não as recalcula. Limite diário é compartilhado entre uso manual e automático.
6. **Configurações** preserva frequência, horizontes 30/90 e limite de análises. Não permite ativar IA não homologada nem qualquer fallback pago.

## Utilitários de observação e coleta pontual

`validate_live.py` agora é somente leitura: não cria alertas, não solicita análises, não inicializa Store e não executa worker. Consulta a base com SQLite ro/query_only e os GETs locais de saúde/estado operacional, sem proxy ou redirecionamento. Exemplo de observação (não executado contra o real nesta correção):

```powershell
.\.venv\Scripts\python.exe scripts/validate_live.py --db data/real/radar.sqlite3
```

O JSON sai em stdout; nenhum relatório é gravado automaticamente. A comparação de ledger/runs informa mudanças no intervalo, sem atribuí-las ao verificador nem afirmar ausência global de IA. Código de saída 2 indica erro de consulta local. Isso não homologa qualidade financeira ou disponibilidade atual da assinatura.

`collect_once.py` exige `--db` explícito e permissão operacional para coleta. Não o execute enquanto o worker estiver ativo: ele recusa a lease antes de enfileirar. Sempre força coleta sem IA, mesmo com ambiente analysis-enabled, e acompanha só seu refresh e filhos. `--request-key` identifica uma retomada do mesmo lote; `--timeout` (300 segundos por padrão) é conferido **entre jobs**, e `--max-iterations` (1000) limita o laço. Um coletor já em execução termina ou atinge seus próprios limites de transporte. Resultado parcial/limite retorna código 2; código 0 exige todos os filhos bem-sucedidos. O refresh inclui catálogo, notícias, preços e macro: não é um comando de mera consulta.

As correções desses utilitários foram exercitadas apenas com bases temporárias e executores falsos. Não houve coleta pontual real nesta rodada. Em Execuções, a interface corrigida oferece cancelamento e retomada separadamente; retry de análise continua desabilitado quando a API não permite IA. A próxima tentativa usa o campo not_before do contrato.

## Dependências e reconstrução

Testado em Windows x64, Python 3.12.0, Node 24.19.0, npm 11.17.0. Não houve teste de outro sistema operacional. Recrie o ambiente apenas dentro deste projeto:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
cd frontend
npm.cmd ci --ignore-scripts
npm.cmd run build
cd ..
```

O SQLite do Python original era 3.42.0. O projeto carrega **3.54.1** de `vendor/sqlite/sqlite3.dll`, obtido do site oficial, hash conferido antes de carregar. Sua proveniência e licença estão ao lado. Não substitua DLL global. Se ausente, `.\.venv\Scripts\python.exe scripts/install_sqlite.py` valida versão/hash oficial antes de instalar somente aqui. O launcher recusa WAL sem versão corrigida.

`requirements.lock` e `frontend/package-lock.json` fixam dependências; instalação requer internet para os registros oficiais quando não há cache. Nenhuma skill de terceiros foi instalada. Os workflows locais do pacote orientam design e revisão.

## Testes

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe scripts/verify_spec.py
cd frontend
npm.cmd test
npm.cmd run build
```

Testes comuns usam bases temporárias e não consomem quotas de dados/IA. No sandbox de execução usado durante desenvolvimento, o socketpair local do asyncio foi bloqueado; testes da API foram executados em terminal local autorizado. A documentação de validação registra resultados reais, sem converter build em prova de qualidade financeira.

`jsdom` 26.1.0 e seus tipos são dependências de desenvolvimento fixadas no lockfile. `terminalDom.test.tsx` importa `jsdom` normalmente; `npm ci` prepara o teste React/DOM sem pasta auxiliar em docs. Não entram no bundle de produção. Nesta entrega, instalação limpa, testes, TypeScript e Vite foram executados em `docs/qa-20261029/collection-only-qa/frontend-clean`, preservando frontend/node_modules e frontend/dist operacionais. Para repetir sem alterar o site, copie src, public (se houver), index.html, package*.json, tsconfig*.json e vite.config.* para um diretório **novo**, então execute ali `npm.cmd ci --ignore-scripts`, `npm.cmd test` e `npm.cmd run build`. Não reutilize dist/node_modules da instalação operacional como prova de instalação limpa.

`scripts/probe_providers.py --phase core|discovery|feeds|bcb_bounded` e `scripts/collect_once.py` fazem rede real e são separados dos testes normais. Probes gravam metadados datados; não usar repetidamente sem necessidade. Coleta reserva orçamento local antes da requisição.

## Configuração sem segredos

Os exemplos originais em `config/` e `.env.example` são preservados. Preferências de uso ficam no SQLite; feeds operacionais homologados em `config/verified-feeds.json`. `BRAPI_TOKEN` opcional deve ser fornecido como variável de ambiente somente se o usuário já possuir um token gratuito. O aplicativo não lê automaticamente `.env`, não cria conta e não compra planos. Sem token, consulta apenas os quatro símbolos de teste documentados; os demais ficam com lacuna visível.

CLI **0.166.0-alpha.5.1** e gpt-6-astra foram homologados em 29/10; consulte o hash e a data em docs/cli-validation.json. Isso é evidência datada, não garantia de binário ainda instalado. O atestado, enabled=true e automatic_analysis=true não foram alterados nesta etapa; o modo de processo pode impedir inferência mesmo com essas flags. Em analysis-enabled, a agenda e os pedidos existentes continuam sujeitos ao teto persistente de 10 chamadas/dia, incluindo reparos e tentativas incertas. Não há fallback pago. O runner verifica o modo antes de quota e mantém MXC/ferramentas desativadas; skip_host_skill_discovery continua experimental. A política de processo foi adicionada sem repetir homologação real.

## Backup e restauração

Com o aplicativo ligado, o backup usa a API SQLite, não cópia simples do `.db` em WAL:

```powershell
.\.venv\Scripts\python.exe scripts/backup.py backup data/real/radar.sqlite3 backups/radar-2026-10-26.sqlite3
```

Destino deve ser novo; o comando recusa sobrescrever. Antes de futura ativação que altere dados, faça um backup novo e ensaie a restauração em outro destino, sem substituir produção:

```powershell
.\.venv\Scripts\python.exe scripts/backup.py restore backups/radar-2026-10-26.sqlite3 backups/restore-check-novo.sqlite3
```

Não copie `.db` aberto ignorando `.wal`. Logs/diagnósticos nunca devem conter tokens. As exportações locais são JSON/CSV com proveniência; CSV neutraliza prefixos de fórmula.

## O que não está homologado

- Homologação de IA não cobre outros sistemas/versões nem garante automaticamente toda alegação futura.
- Calendário oficial de pregões, ajustes societários e retorno total; preços disponíveis não garantem comparabilidade.
- Ingestão contábil CVM e mapeamento de exposição econômica indireta. Há probe CVM, mas fundamentos ausentes ficam null.
- Cobertura histórica integral de notícias, independência perfeita de republicações e identidade histórica de emissores. IDs atuais usam símbolos do fornecedor com a limitação registrada.
- Não há histórico prospectivo maturado de hipóteses nem resultados financeiros inventados.

Os arquivos originais do planejamento são preservados; `scripts/verify_spec.py` confere seus hashes mesmo com código novo no projeto. Decisões e explicação técnica em `docs/implementation-decisions.md` e `docs/engineering-notes.md`.
