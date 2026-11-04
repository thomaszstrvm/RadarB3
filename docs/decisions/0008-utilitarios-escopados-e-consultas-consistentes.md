# ADR 0008 — Execuções delimitadas e identidade das consultas

Data: 30/10/2026. Estado: implementado e validado em isolamento; não promovido nesta etapa.

## Contexto

A revisão independente reproduziu utilitários que podiam consumir análise antiga da fila, declaração constante de ausência de IA, liberação de quota após envio parcial e concorrência entre polling/exportação e consultas pendentes. Testes anteriores não exercitavam essas combinações.

## Decisão

- Coleta pontual força `collection-only` no worker e no ambiente; adquire lease antes de enfileirar. Claim e recuperação recebem escopo opcional de IDs. `None` conserva a fila normal; escopo vazio não seleciona nem recupera jobs. O utilitário acompanha exclusivamente o refresh identificado e seus quatro filhos.
- `validate_live` é somente observação: SQLite `mode=ro/query_only` e GETs locais permitidos, sem proxies/redirects, Store, worker ou mutações. Mudança de ledger/runs é observada, não atribuída automaticamente ao verificador. Não há teste real de IA escondido nesse comando.
- A reserva HTTP deixa de ser liberável imediatamente antes de `request()`, pois uma exceção pode ocorrer depois de enviar bytes. Falha anterior de conexão/TLS libera a unidade; falha após início é `uncertain`; resposta já contabilizada permanece `consumed`.
- Respostas da interface carregam identidade da consulta e revisão. Dados anteriores podem permanecer visíveis, mas só a resposta atual concluída e sem erro pode ser exportada. O horário exportado vem dessa resposta.
- Polling de Execuções agenda a próxima consulta depois da conclusão, respeitando o timeout do transporte. Contexto/desmontagem cancelam a consulta; aba oculta suspende novos polls. Cancelamento e retomada são capacidades independentes, com bloqueio de IA mantido.

## Alternativas

Trocar globalmente o padrão da biblioteca de worker alteraria chamadores não relacionados; foi mantido o padrão existente com modo explícito nos utilitários. Aumentar o número fixo de iterações não delimitaria a fila. Apagar toda resposta antiga do hook prejudicaria a preservação do detalhe. Aumentar o intervalo fixo apenas deslocaria o problema de polling. Liberar quota depois de uma tentativa ambígua subestimaria consumo.

## Consequências positivas

Jobs alheios e análises históricas são preservados; observação não produz trabalho. O orçamento permanece conservador; exportação e filtros correspondem; consultas lentas válidas podem terminar. Sem dependências novas, schema adicional ou mudança no MXC.

## Consequências negativas e limites

O orçamento pode contar uma tentativa que não chegou ao provedor, pois não é possível provar o contrário. O timeout do coletor é entre jobs; um handler síncrono depende de seus próprios limites de transporte. Uma execução parcial não é sucesso integral. Observação local não comprova ausência global de inferência. Os parâmetros opcionais de escopo acrescentam um contrato a manter e testar. Ativação posterior exige decisão operacional separada; esta entrega não reinicia o worker nem promove o build.
