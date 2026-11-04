# ADR 0014 — tentativa revisada e captura local delimitada de QA

Data: 01/11/2026. Estado: implementado e revisado; tentativa 2 executada e rejeitada. Captura reproduzida em memória e excluída por hash. [Resultado verificável](../qa-20261101/bounded-rejection-qa/RESULTADOS.md); [gates e autorização](../qa-20261101/bounded-rejection-qa/PLANO.md).

Adendo: resultados, consumo e permissões específicas do operador de contrapontos ficam no relatório local. A arquitetura descrita não autoriza nova execução nem comprova a exibição visual de resultados.

## Contexto

A primeira análise real VALE3 sob contrato 1.2 foi rejeitada. O ADR 0010 preservou somente diagnóstico enumerado, IDs e hash da saída. Isso localiza o erro, mas não permite reproduzir a frase descartada. A proposta anterior de ampliar a captura foi negada por falta de autorização específica e não foi executada. Nova evidência humana autoriza expressamente uma captura local limitada para este QA público, sem prompts integrais, segredos ou dados pessoais; sua referência e proveniência estão no plano.

Repetir a análise normal da mesma chave permanece bloqueado pelo runner. Remover esse guard, modificar artificialmente o bundle/prompt ou apagar a tentativa anterior prejudicaria a proteção contra duplicação e a contagem de consumo.

## Decisão

Manter o comportamento padrão e expor somente uma entrada explícita ao helper local de QA, sem endpoint, botão, envflag ou opção global. Uma permissão revisada e de duração curta vincula o bundle público integral, sua chave, modelo/prompt/schema/executável e a tentativa anterior terminal conhecida. Ela autoriza no máximo uma tentativa seguinte, sob o mutex e a quota existentes, com zero reparações. Histórico, identidade, cutoff e reserva anterior não mudam. Reserva órfã, tentativa já criada, divergência de pins, expiração ou marcador de uso impedem novo envio. O marcador é consumido de forma exclusiva; falhas não o reabrem automaticamente.

A rejeição é primeiro finalizada no banco com os metadados normais. Só então a captura de QA pode selecionar uma afirmação numérica, íntegra e curta, com seu diagnóstico seguro e referências conhecidas. Um erro na captura não altera a rejeição nem reabre a chamada. O diagnóstico normal e as exportações/API continuam sem a afirmação ou o valor rejeitado.

A captura permite apenas linguagem financeira de vocabulário fechado e uma quantidade monetária explicitamente identificada, com escala e coeficiente curto, mesmo se não corresponder à fonte: esse caso é necessário para investigar `quantity_not_found`. Toda quantidade capturada é rotulada como literal rejeitado do modelo e não validado. Não inferir origem pública por igualdade de coeficiente, que não garante moeda ou escala correspondentes. Nomes ou conteúdo desconhecido, contatos, identificadores pessoais, URLs, caminhos, chaves e controles impedem a gravação inteira. Não truncar ou substituir termos e depois alegar equivalência lexical. Essa regra não é um detector universal de dados pessoais nem garante capturar qualquer resposta.

Retenção: somente um arquivo local neste QA durante sua reprodução e revisão, com limites de tamanho e escrita exclusiva. No encerramento, conferir seu caminho e hash e remover explicitamente apenas esse arquivo novo; manter recibo sem texto e testes sintéticos suficientes para a regressão. Não copiar a frase para logs, relatórios gerais, banco, snapshots ou testes persistentes. Não há serviço de limpeza; se ocorrer interrupção antes da remoção, o fechamento deve indicar que ela ainda não aconteceu.

## Alternativas consideradas

- Repetir com diagnóstico enumerado apenas: não fornece reprodução da frase e pode gastar outra chamada sem resolver a investigação.
- Guardar stdout, prompt ou análise rejeitada integral: excede a autorização e aumenta a retenção desnecessariamente.
- Uma flag `force` ou remoção do guard: permitiria reenvios pouco rastreáveis; descartada.
- Mudar relógio, nonce ou prompt sem mudança material: alteraria identidade apenas para contornar o guard; descartada.
- Novo endpoint e ciclo de reinício: desnecessários para este teste local delimitado. O worker e o painel permanecem como estão; a execução do helper não homologa novamente toda a fila/UI.
- Regex genérica para apagar segredos: não comprova privacidade nem reprodução. Preferida recusa conservadora quando a frase não pertence ao domínio permitido.

## Consequências

Positivas: permite uma investigação concreta com consumo auditável, preserva histórico, critérios de aceitação, isolamento MXC e arquitetura de modelo único. Não acrescenta dependências, infraestrutura ou coleta. A capacidade fica separada do uso diário normal.

Negativas: um crash após consumir o marcador pode exigir revisão manual sem ter lançado o processo; essa indisponibilidade conservadora evita duplicação. A captura pode ser recusada para uma frase legítima fora da gramática e isso será uma limitação explícita. A remoção ao final limita a reprodução futura ao recibo e às fixtures sintéticas; não se alegará reter a frase que foi removida. O mutex serializa inferências, mas não impede um clique concorrente de criar um job; por isso a janela de QA exige conferência da fila antes e depois.

Validação necessária: testes isolados de permissão, expiração, pins, concorrência, quota, reserva órfã, crash, privacidade, reprodução e falha de gravação; regressões do runner; revisão independente; backup e preservação. Resultados reais serão registrados no relatório da etapa, sem transformar este ADR em prova de execução.

## Resultado e precisão do prompt

A tentativa 2 revelou uma expressão de conta não reconhecida. A substituição exclusiva pelo rótulo canônico foi aceita offline, preservando as quantidades completas; o restante da aceitação não foi alterado. A correção mínima exige no prompt 1.2 rótulos canônicos literais e períodos compatíveis. Ampliar os sinônimos do parser foi considerado, mas não é necessário para alinhar a geração à gramática existente. O novo hash decorre dessa instrução material; não apaga nem libera as tentativas anteriores. Testes sintéticos verificam a precisão e a preservação do histórico, mas não garantem que uma futura geração obedeça ao prompt.

O descarte verificado da captura e o estado da permissão constam no recibo local. Uma permissão consumida não é reaberta; um pedido normal não reutiliza essa permissão nem ativa captura.

## Continuação delimitada: contrapontos e composição externa

Data: 01/11/2026. O pedido normal posterior terminou rejeitado em `/signal/counterpoints/0`, sem captura habilitada. Isso foi uma decisão da integração normal; a ausência de texto impede atribuir uma causa lexical exata. Testes sintéticos provaram, separadamente, que o próprio rótulo `R63` usado pelo prompt é recusado em prosa. O prompt agora exige nomes/janelas por extenso. Parser, schema e aceitação financeira permanecem intactos.

O assentimento humano original permite uma frase e campos mínimos neste QA público. A restrição à tentativa 2 e o uso único pertencem ao manifesto técnico anterior, que permanece consumido e não será rearmado. A continuidade operacional foi autorizada separadamente. Uma nova permissão exige identidade própria, revisão material, preflight e vínculo ao mesmo bundle público. Não se ampliam retenção global, dados enviados ou cobertura financeira.

Decisão: compor um operador de QA externo sobre o `AnalysisRunner` normal, sem alterar seu contrato ou usar a permissão v1. O operador mantém a aquisição única do mutex, consome um novo marcador exclusivo antes da quota e confere os bytes efetivamente enviados. O runner continua responsável por quotas, histórico, quarentena e aceitação. Após finalizar uma rejeição, o operador pode reter somente o primeiro campo elegível de claim ou contraponto, integral e limitado, com gramática independente da validação financeira. O objeto da resposta vive apenas na memória do processo durante esse fluxo; a referência é liberada em `finally`, sem promessa de apagamento físico da RAM.

O sanitizador só pode executar depois dos controles de captura; lê, confere e compila o mesmo snapshot de bytes. Importar primeiro e conferir o hash depois foi uma falha concreta encontrada pelo Validador e corrigida com regressões de troca durante o executor falso e após a leitura. Não se carrega um `.pyc` nem se reabre o arquivo entre conferência e execução.

Alternativas: ampliar a retenção normal facilitaria diagnósticos, mas mudaria a política global; reutilizar o manifesto anterior apagaria a distinção de uso; exigir que o capturador financeiro aceite a frase impediria reproduzir justamente formatos rejeitados. A composição local evita esses problemas, ao custo de um operador especial que exige revisão e não homologa a fila/UI inteira.

Limites: vocabulário fechado, até três literais financeiros, quatro rótulos fixos, valores públicos exatos extraídos de campos tipados e uma exceção monetária curta. Na matriz de oitenta exemplos, 54 são retidos e 26 bloqueados; algumas frases financeiramente válidas também ficam fora. Não há cobertura universal nem garantia de captura de qualquer geração futura. Ausência de captura deve ser acompanhada pelo código preciso do bloqueio, nunca por reconstrução do texto descartado. Interrupção na escrita pode deixar arquivo parcial; `retention_pending` exige conferência e descarte pelo caminho/hash exatos.

A implementação, revisão e eventual execução são etapas distintas. O estado efetivamente confirmado, consumo e descarte constam em [RESULTADOS.md](../qa-20261101/bounded-rejection-qa/RESULTADOS.md); este ADR não autoriza uma inferência nem concede aceite financeiro.
