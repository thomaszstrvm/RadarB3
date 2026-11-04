# ADR 0010 — Sinal informativo versionado e números conservadores

Data: 31/10/2026. Status: implementado; validação operacional registrada no relatório desta rodada.

## Contexto

O usuário solicitou sinal informativo de comprar, vender, aguardar ou dados insuficientes, com horizonte, evidências, riscos, contrapontos e invalidação. Os oito resultados históricos usam o contrato 1.0 e não contêm esse campo. A rejeição real anterior conservou código e hash, mas não a resposta; sua causa exata continua desconhecida. Fixtures demonstraram falsos aceites e rejeições na antiga comparação de números soltos.

## Decisão

Criar `analysis-v1.1.schema.json` e adendo de prompt versionado, preservando os contratos e prompts originais. Novas chamadas exigem 1.1; consultas e validações históricas continuam aceitando 1.0 sem inventar sinal. Nenhuma migração reescreve análises. O hash inclui o sistema com adendo e o hash do novo schema, impedindo reutilização do cache antigo como sinal novo.

O objeto `signal` contém ação, horizonte configurado, corte idêntico à análise, justificativa, referências, riscos, contrapontos e condições de invalidação. As referências participam da união exata da raiz. A ação deve ser coerente com status e classificação. `wait` exige evidência substantiva citada; a abstenção explica limites. A interface mostra um aviso explícito em históricos sem sinal.

`buy` e `sell` são valores reconhecidos pelo contrato, mas recusados operacionalmente por `signal_directional_data_unverified`. O pipeline atual não possui contrato verificável de fundamentos, além das limitações de ajustes/calendário. Um documento isolado ou flag arbitrária não substitui essa verificação. Liberar direção exigirá futura decisão, contrato e testes de fundamentos e preços comparáveis, não uma mudança de prompt ou a retirada deste guard. O produto atual entrega aguardar ou dados insuficientes quando sustentados; não promete que os quatro resultados já estão operacionalmente disponíveis.

Substituir dígitos soltos por quantidades completas. Normalizar apenas equivalências decimais e agrupamentos inequívocos; preservar sinal, moeda, percentual/pontos percentuais e escala. Datas são tokens completos e não fornecem anos como preços. Preços estruturados oferecem apenas campos financeiros permitidos; métricas preservam unidade. Não converter fração em percentual nem milhão em bilhão. Literais não suportados são recusados, inclusive dígitos que a regex não consumiu. Números somente no título/metadado continuam sem suporte no conteúdo; a interface já apresenta esses metadados separadamente.

Na versão 1.1, aplicar a guarda numérica também à síntese, às inferências, justificativas, cenários, riscos, lacunas, flags e textos do sinal. Explicações vazias e expressões explícitas de certeza financeira são recusadas. Isso não é interpretação semântica completa: número presente pode referir-se a outra variável, e texto por extenso não é prova causal. A análise continua precisando de inspeção humana das evidências.

O diagnóstico persiste somente código, caminho/índice, tipo de claim e até oito IDs resolvidos no bundle validado, cada um limitado a 128 caracteres. A persistência e a API sanitizam novamente. Não guardar texto rejeitado, número rejeitado, saída bruta, stderr, URLs ou mensagens de exceção.

## Alternativas

- Alterar o schema 1.0 e preencher históricos: descartado porque mudaria a interpretação de resultados já publicados e violaria os originais preservados.
- Inferir sinal pela classificação antiga: descartado porque investigar não significa comprar e uma hipótese incompleta não constitui decisão direcional.
- Aceitar qualquer filing como fundamento: descartado; não comprova conteúdo financeiro, integridade ou atualidade.
- Concatenar título/datas/IDs ao corpus numérico: descartado porque faria anos e identificadores sustentarem valores financeiros.
- Implementar parser financeiro universal/conversor de unidades: desproporcional ao escopo. Preferida recusa explícita de formatos não suportados, sem nova dependência ou LLM.
- Guardar resposta integral para depuração: descartado por aumentar retenção de dados externos; o diagnóstico estrutural é suficiente para localizar a evidência citada, embora não reconstrua a afirmação rejeitada.

## Consequências

Positivas: histórico imutável; cache versionado; ação auditável; unidades e datas não se misturam por simples coincidência de dígitos; diagnóstico reduzido; testes determinísticos sem IA. O custo continua local, sem nova infraestrutura.

Negativas: comprar/vender permanecem indisponíveis; algumas representações corretas exigem abstenção conservadora; títulos/metadados não sustentam números de claims; unidades desconhecidas e algumas expressões de cautela podem ser recusadas. A validação lexical não certifica a correção econômica ou a adequação pessoal. A API permanece somente localhost, sem ordem, corretora ou dimensionamento de posição.

## Compatibilidade do schema e diagnóstico de execução

Após uma tentativa CLI com saída não zero e uso não informado, foi identificado um defeito verificável no contrato enviado: `uniqueItems` não pertence ao subconjunto aceito. A documentação oficial de [geração de schemas](https://developers.openai.com/api/docs/guides/prompt-generation) lista essa restrição; [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs) explica que keywords não suportadas podem gerar erro. Essas páginas foram abertas em 31/10/2026. Isso é compatível com a falha rápida observada, mas não comprova a causa daquela execução sem seu erro bruto.

Decisão: remover somente `uniqueItems` do schema 1.1 enviado, mantendo a mesma regra de unicidade em validação semântica explícita. Os demais limites e o contrato 1.0 não mudam. O novo hash de schema gera uma nova chave legítima; a tentativa anterior continua contabilizada e bloqueada. Não usar um filtro genérico que remova silenciosamente restrições do contrato.

Falhas futuras de processo conservam no evento `analysis_cli_failure` somente código de saída numérico limitado, categoria enumerada e tipos reconhecidos de eventos de erro. Mensagens JSON são examinadas apenas em memória; stdout, stderr, campos de erro e valores não são guardados. JSON excessivamente profundo recebe categoria genérica em vez de impedir a finalização do run. A API sanitiza novamente e expõe `execution_diagnostics` separado de `validation_diagnostics`, permitindo à UI distinguir problema de execução de problema factual. Nenhuma mudança em MXC, transporte, credenciais ou ferramentas foi necessária.

## Delta numérico posterior à terceira tentativa

Depois de uma nova rejeição semântica sem texto retido, acrescentar somente um enum opcional ao diagnóstico: `unsupported_format`, `unsupported_date`, `unit_mismatch` ou `quantity_not_found`. A aceitação continua decidida pelo gate existente; a classificação ocorre depois da rejeição e, se falhar, é omitida sem substituir o erro original. Nenhum valor, texto ou hash por token é persistido. API e UI limitam-se à allowlist; históricos sem enum permanecem sem ele.

Uma fixture demonstrou independentemente a equivalência inequívoca de `60,05 reais`/`60.05 BRL` com preço estruturado na moeda BRL. Suportar somente esses sufixos explícitos preservando valor, sinal e escala. Não interpretar `real` singular, dólares genéricos, prefixos conflitantes, parênteses negativos, denominadores ou volume como moeda. Datas/metadados mantêm o comportamento conservador anterior. Essa correção não identifica a causa da terceira resposta não retida; resultados/hashes da verificação anterior continuam sendo evidência daquele momento, não dos arquivos alterados depois.

O novo sufixo é aceito somente no fim do texto, seguido de espaço ou pontuação terminal (`.`, `!`, `?`), sem unidade adicional. Isso recusa conservadoramente continuações como “ao dia”, “na sessão” ou uma segunda moeda, evitando uma lista interminável de denominadores. Símbolos Unicode de moeda não consumidos são recusados, nunca reinterpretados como BRL. No novo formato com sufixo, um token anterior de três letras é ambíguo independentemente da caixa (`GBP`, `Gbp`, `gbp`) e também é recusado. Esse último guard não altera a gramática antiga de prefixo `R$`: “foi R$ 60,05” continua aceito, enquanto o novo “foi 60,05 reais” é recusado conservadoramente. A prosa usual “de 60,05 reais” permanece aceita. A desvantagem deliberada é recusar algumas frases corretas com continuação ou prefixo ambíguo; essa gramática limitada não promete interpretação financeira geral.

## Diagnóstico de conta e período — 01/11/2026

Após a primeira inferência real 1.2, a revisão independente reproduziu a mesma categoria `quantity_not_found` para valor ausente e valor existente sem período reconhecido. A frase real não foi retida; a reprodução usa exemplos sintéticos e não estabelece a causa daquela rejeição.

Decisão: acrescentar somente os motivos enumerados `filing_account_unrecognized`, `filing_account_ambiguous`, `filing_period_unrecognized`, `filing_period_ambiguous` e `filing_period_incompatible` no diagnóstico posterior à rejeição de um filing moderno. A gramática, o corpus e a decisão de aceitação permanecem iguais; a UI explica os motivos em português. O diagnóstico prioriza formato/data, conta, período, unidade e quantidade. Correspondência em outro período exige quantidade completa normalizada na mesma conta, não apenas coeficiente igual.

Alternativas: relaxar o reconhecimento ou ampliar automaticamente sinônimos mudaria a aceitação sem evidência suficiente; uma captura de texto/valor rejeitado mudaria a política de retenção. A proposta de exceção encaminhada foi rejeitada pela revisão automática por falta de autorização clara para essa mudança. Mantém-se integralmente a proibição de reter texto/número rejeitado, saída bruta e mensagens livres. Não existe persistência alternativa de conteúdo rejeitado no QA.

Consequências: melhora o diagnóstico de novas falhas reconhecidas, preservando histórico, privacidade, custo e complexidade operacional. Em contrapartida, enums não reproduzem uma frase real descartada, não provam correção financeira e não cobrem interpretação universal de prosa. Seletores diagnósticos espelham a gramática existente e precisam de testes diferenciais quando ela mudar. Nenhuma migração, nova dependência, chamada de IA ou mudança em MXC é necessária para esse ajuste.

## Exceção pontual posterior — 01/11/2026

O parágrafo de retenção acima registra a decisão anterior, cuja operação negada não foi executada. Posteriormente, houve assentimento humano específico para guardar temporariamente uma frase limitada de um QA com dados públicos. O [ADR 0014](0014-qa-rejeicao-publica-delimitado.md) define essa exceção, vinculada a uma tentativa revisada e separada dos logs, banco, API e fluxo normal. Ela não autoriza retenção global ou expansão de permissões. A captura da tentativa 2 foi reproduzida em memória e removida por caminho/hash às 05:48:20 UTC; [recibo de descarte](../qa-20261101/bounded-rejection-qa/cleanup-receipt.json). O diagnóstico normal continua enumerado e a frase não foi incorporada aos relatórios/testes persistentes.
