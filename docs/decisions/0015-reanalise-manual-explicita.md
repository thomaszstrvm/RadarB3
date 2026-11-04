# ADR 0015 — Reanálise manual explícita do mesmo snapshot

Data: 01/11/2026. Estado: implementação local; ativação operacional depende da revisão do ensaio FINAL-E2E-20261101.

## Contexto

O pedido exige uma nova análise real acionada pela interface. O botão comum pode apenas reutilizar o cache. Em auditoria somente leitura, às 23:10:36 UTC de 01/11, a reconstrução de VALE3 em uma cópia SQLite exclusivamente em memória retornou o bundle `bundle_6477b0668fe8a611dcc5149c`, com corte original `2026-11-01T04:06:32.266811Z`, e a análise correspondente já existia. Não foi executado runner ou POST nessa auditoria.

Uma chave HTTP nova cria outra solicitação, mas não altera a identidade do cache de análise. A tabela `analyses` também tem unicidade por bundle/modelo/prompt/schema. Ignorar somente a leitura do cache poderia consumir IA e depois descartar silenciosamente a nova linha em `INSERT OR IGNORE`.

## Decisão e justificativa

Aceitar corpo opcional estrito `{"reanalyze": true}` em `POST /assets/{asset_id}/analyse`, somente no modo manual e com chave de idempotência explícita. Valores não booleanos e campos adicionais são recusados. Corpo ausente, vazio ou false mantém o fingerprint histórico `None`; true participa do fingerprint, impedindo trocar a intenção usando o mesmo recibo.

A API guarda `reanalyze=true` no job durável, mantendo admissão serial, owner escolhido pelo servidor e máximo de uma tentativa. O worker passa exclusivamente o ID desse job como `new_request_key` ao runner. O runner exige modo manual, reparações zero, ausência de `reviewed_retry` e job analyse/running da primeira tentativa, não cancelado, com leases válidas, owner atual, ativo correspondente e marca reanalyze verdadeira. Repete o vínculo depois de adquirir o mutex.

A reanálise acrescenta um bloco determinístico de controle **ao texto realmente enviado**, antes de calcular `prompt_hash`. O bloco contém o ID do job e determina que o mesmo evidence_bundle e seu as_of sejam preservados; o ID é metadado operacional interno, não evidência financeira, e não deve ser repetido na prosa. A metodologia financeira, os dados, o bundle, os schemas, o modelo, o esforço, a política MXC e as configurações não mudam.

O hash diferente identifica honestamente um prompt diferente em seus metadados operacionais. A unicidade existente passa a admitir a nova análise sem migração, exclusão ou substituição. O mesmo job reconstrói o mesmo texto/hash/analysis_id: se sua análise foi salva, recupera seu próprio resultado; se a tentativa anterior ficou incerta ou rejeitada sem resultado válido, bloqueia antes de reservar quota e reenviar. Quota normal e mutex continuam autoritativos. A reanálise não ativa o mecanismo excepcional de QA ou retenção de resposta.

## Alternativas consideradas

- Acrescentar request_key à tabela e migrar a restrição UNIQUE: modela solicitações separadamente, mas exige reconstruir tabela imutável e ampliar a superfície de migração para esta necessidade limitada.
- Alterar somente o hash sem alterar o texto enviado: falsearia a identidade do prompt; descartado.
- Alterar bundle, datas ou evidências para invalidar cache: falsearia o snapshot; descartado.
- Apagar análise/cache ou sobrescrever resultado: destruiria histórico e não resolveria segurança de replay; descartado.
- Usar `reviewed_retry`: esse caminho excepcional trata rejeições previamente revisadas, não reanálise comum de resultado válido; descartado.

## Consequências positivas

- Preserva análises e recibos anteriores e o cache padrão, sem migração.
- Uma solicitação explícita corresponde a no máximo uma chamada, sem reparação ou reenvio automático após interrupção.
- O hash pode ser recalculado a partir do prompt realmente enviado, com execução rastreável ao job.
- Dados e `as_of` permanecem os do snapshot; recência da execução não é apresentada como recência dos dados.

## Consequências negativas e limites

- Cada reanálise é uma variante do prompt de controle. Solicitações diferentes podem consumir novas unidades da quota, mesmo com dados idênticos; isso precisa estar claro na interface.
- O cache padrão continua apontando para sua variante original. Uma solicitação comum posterior pode reutilizar essa análise, enquanto a tela escolhe o resultado salvo mais recente pelo corte e desempate de criação; IDs de acompanhamento e resultado exibido permanecem separados.
- Uma falha depois do commit da análise pode deixar o job sem vínculo terminal confirmado; o resultado salvo continua disponível. O ensaio cobre esse limite sem tentar reenviar automaticamente.
- A recuperação de worker continua bloqueando jobs manuais antigos. Uma nova solicitação explícita tem outra identidade; não é uma retomada automática de consumo incerto.
- Testes falsos não homologam disponibilidade, qualidade ou conclusão de uma nova chamada real. Nenhuma execução operacional é autorizada pelo teste ou por este ADR isoladamente.

## Validação prevista

Testes isolados verificam corpo estrito, recibos históricos, conflito de intenção, clique repetido, duas chaves durante job ativo, vínculo antes/depois do mutex, hash do stdin, preservação de dois resultados, snapshot idêntico, uma chamada sem reparação, replay sem quota, bloqueio de tentativa incerta, falha após salvamento e reinício de worker. O wrapper de QA bloqueia rede externa; o módulo novo bloqueia todos os subprocessos.
