# ADR 0007 — calendário explícito e continuidade dos pregões

Data: 2026-10-29. Estado: implementação e validação em ambiente isolado; importação operacional não autorizada nesta etapa.

## Contexto

Contar barras não prova que elas representam pregões consecutivos. Uma série pode ter a quantidade necessária de observações e omitir um pregão intermediário. Da mesma forma, ausência de preço num dia civil pode ser feriado, fechamento excepcional ou falha de coleta. O calendário operacional ainda não foi verificado; não se deve preencher esse vazio supondo segunda a sexta.

Calendário, ajuste por eventos corporativos e cobertura de preços são dependências separadas. O calendário não transforma uma série com ajuste desconhecido em série adequada para retorno.

## Decisão

Importar um documento JSON local que declare origem HTTPS, versão, momento de verificação, início, fim e todos os dias civis do intervalo. Cada dia declara se houve sessão e, quando aberto, seu encerramento com fuso horário. Dias fechados são explícitos, com encerramento nulo. Intervalos esparsos, duplicatas, datas inválidas e horários incompatíveis são recusados antes de gravar.

A importação desta entrega aceita somente uma base demo existente, identificada por seu perfil persistido, sem lease ativa. Não inicializa o Store real, não cria banco implicitamente, não coleta dados, não agenda tarefas e não modifica o worker. A gravação utiliza uma transação: conflito em qualquer data recusa o lote inteiro. Repetição idêntica é idempotente; datas existentes não são sobrescritas para esconder uma revisão.

Reutilizar `market_sessions`, que já contém origem, versão e verificação. Carregar o calendário necessário à série e registrar a proveniência e o pregão aberto anterior na evidência de cada barra. A validação exige que barras consecutivas se encadeiem por esses pregões; feriados explicitamente fechados não criam lacunas. Um calendário incompleto ou desconhecido mantém a métrica indisponível, com motivo. O contexto acompanha o bundle para que a validação não dependa do estado futuro da base.

O importador valida a estrutura e a consistência temporal, mas não autentica o conteúdo de uma URL: não faz requisição nem converte texto externo em instrução. `source` e `verified_at` registram a proveniência declarada por quem preparou o arquivo. A homologação de um calendário B3 real exige conferir a fonte permitida e continua fora desta entrega.

## Alternativas consideradas

- Inferir dias úteis: simples, mas confunde feriados e fechamentos excepcionais com falhas de preço; descartado.
- Aceitar apenas datas abertas: não permite distinguir um dia fechado de um dia omitido; descartado.
- Confiar somente em `complete_session`: informa qualidade de uma barra, sem provar continuidade entre barras; insuficiente.
- Consultar calendário externo durante o cálculo: introduz rede, variação temporal e dificuldade de reproduzir bundles históricos; não utilizado.
- Criar infraestrutura nova de agendamento: desnecessário para validação determinística local; a agenda atual permanece intacta.

## Consequências positivas

Ausência de calendário e ausência de preço tornam-se distinguíveis. O lote é atômico e pode ser repetido sem duplicação. A evidência preserva contexto para recálculo. Não há nova dependência, serviço, credencial ou chamada de IA. O custo de validar o calendário é limitado pelo intervalo aceito pelo importador.

## Consequências negativas e limites

O arquivo é mais extenso por incluir dias fechados. É necessário preparar e verificar a fonte. Correções de datas já importadas exigirão uma política explícita de revisão e preservação de proveniência; não se resolve conflito sobrescrevendo. Metadados adicionais aumentam o bundle. Preços sem ajuste verificado, barras incompletas e demais restrições continuam bloqueando as métricas, mesmo com calendário válido.

Esta decisão não autoriza importar calendário na base real, promover a versão para o serviço ativo, reiniciar worker nem habilitar coleta de preços. Resultados efetivamente executados pertencem ao relatório da etapa, não a este registro de arquitetura.

## Conceito para estudo

Continuidade é uma propriedade da relação entre observações. Uma barra individual pode ser válida e ainda assim fazer parte de uma série incompleta. A referência ao pregão anterior funciona como uma ligação verificável: a barra seguinte precisa apontar para a anterior da amostra, respeitando os fechamentos declarados.

Exercício: num calendário sintético que inclua um feriado, remova a barra de um dia aberto e explique por que esse caso deve falhar, enquanto a ausência de barra no feriado deve passar. Não use produção ou fontes externas.

## Adendo — pacote operacional limitado, 31/10/2026 (São Paulo)

O texto acima registra a primeira entrega demo. A etapa `vale3-gates` acrescenta um único pacote documental revisado, `b3-cash-20260731-20261103-v1`, limitado a 31/07–03/11/2026. São 96 dias civis: 65 sessões programadas abertas, 28 dias de fim de semana e três feriados. Quatro PDFs públicos da B3, suas páginas, hashes, papéis e vigências compõem um documento canônico de 6.000 caracteres. OC005 rege o período anterior; OC055 entra em vigor em 03/11; OC050 revogado é contexto histórico, não grade vigente. O instante 19h de São Paulo representa o final programado da janela de correção, não o fechamento regular ou a finalização de uma barra do provedor.

Decisão: uma tabela `calendar_packages` imutável guarda o documento, hash, verificação documental e disponibilidade operacional real. Reutilizamos `market_sessions.version` como referência ao pacote. O importador confere os quatro arquivos locais, perfil explícito demo/real, ausência de qualquer lease e de jobs ativos. Uma transação `BEGIN IMMEDIATE` inclui pacote, sessões, proteção contra alteração e um evento de auditoria; qualquer `BaseException` desfaz tudo. Replay idêntico não renova disponibilidade nem auditoria, e conta inserções e reutilizações efetivas. O uso real ainda depende da janela operacional aprovada; esta decisão não executa essa janela.

O bundle 1.1 contém o pacote uma vez; barras usam `calendar_ref`. A função pura `calendar_from_rows` é compartilhada por montagem e revalidação, incluindo dias fechados posteriores à última barra para determinar a última sessão esperada. O pacote só é elegível quando `available_at <= as_of`; `verified_at` documental não autoriza enriquecimento retroativo. Bundles 1.0 armazenados e seu schema permanecem intactos. A expansão após 03/11 deverá manter o validador e os pins desta versão para replay; não poderá simplesmente substituir as constantes e invalidar históricos.

Alternativas: repetir quatro URLs/hashes em cada barra aumentaria desnecessariamente o envelope; inferir horários/finalização apenas do calendário produziria falsa qualidade; um serviço externo de calendário introduziria acesso e dependência desnecessários. A referência compacta mantém proveniência verificável dentro do bundle e o teto de 60.000 **caracteres**. Se insumos obrigatórios de uma métrica válida não couberem, a montagem recusa o bundle; não os apaga silenciosamente.

Correções relacionadas: a janela local de três meses filtra sessões antes da seleção de família/64 barras; R63 exige 64 observações e não impede métricas menores com dados suficientes. As métricas atuais de preço exigem `split_adjusted`; `total_return`, que pode incorporar proventos, não recebe o rótulo de retorno de preço. O volume relativo continua acoplado à mesma comparabilidade conservadora; isso não prova política de ajuste de volume e não pretende resolver esse contrato. A revalidação confronta ajuste e datas inicial/final com os insumos, além de fórmula, unidade e valor.

Consequência de compatibilidade explícita: registros históricos são preservados. Um caso sintético 1.0 aceito pela implementação anterior com semântica `total_return` errada passa a ser rejeitado ao revalidar; não foi criado bypass para manter o erro. A compatibilidade dos snapshots preservados deve ser verificada em cópia isolada. Reconstruir com a janela corrigida pode produzir novo hash; não é promessa de reproduzir o bug nem autorização para forçar cache.

O adapter Brapi deixa de promover `complete_session` só porque o horário B3 passou. O dicionário público do provedor confirma `close` de período, volume negociado e `adjustedClose` com proventos também para `/api/quote/{tickers}`. A ausência de booleano de finalização não torna os dados impossíveis: uma política versionada de snapshot diário encerrado, revisável, pode combinar identidade, intervalo, sessão, relógios e hash da resposta integral. O harness QA demonstra esse caminho. **Pendência interna:** o adapter ainda não persiste resposta integral/vintage/política nem cada revisão de coleta; por isso não promove dados reais antigos. Implementar essa persistência é viável com o acesso atual, sem conta paga ou flag obrigatória do provedor. Adequação split-only para retorno de preço é uma questão distinta de finalidade.

As consultas `/sources`, `/coverage` e estado operacional derivam a cobertura limitada do pacote, sem reescrever tentativas históricas em `provider_state`. O modo manual continua sem agenda e sem coleta. `Worker.schedule` nos modos anteriores usa o calendário apenas como horário para tentar coleta, não como certificado de finalização. Sinais buy/sell seguem bloqueados; métricas nulas não obrigam globalmente `insufficient_data`, pois hipótese/aguardar ainda dependem das evidências substantivas.

### Extensão cumulativa em 03/11/2026

Cada pacote revisado tem ID, hash e instante de disponibilidade próprios. O pacote até 31/12 copia as datas do predecessor até 03/11. Na importação, o Radar compara data, abertura e limite de correções em todo esse prefixo e insere somente os 58 dias adicionais. As linhas antigas mantêm a proveniência original. Uma repetição do mesmo pacote conserva o instante da primeira importação.

Essa decisão mantém um único calendário em cada conjunto de evidências e permite reproduzir o resultado histórico usando o pacote disponível no corte da análise. Substituir o conjunto anterior perderia essa distinção. Referenciar vários documentos independentes em cada barra exigiria alterar o formato do histórico. O custo da abordagem cumulativa é repetir parte do calendário em cada documento e revisar explicitamente os hashes e as datas a cada extensão. O fechamento de 2026 delimita o alcance desta versão. [Documentos da B3 e verificação em cópia](../qa-20261103/daily/calendar/FONTES.md)
