# ADR 0013 — Capturas diárias preservadas e hipótese técnica experimental

Data: 01/11/2026, São Paulo. Estado: implementado, validado e promovido localmente com uma captura pública arquivada; zero nova coleta ou chamada de IA nesta etapa.

## Contexto

O armazenamento anterior deduplicava barras separadas e não preservava o corpo que permitia revalidar uma série inteira. Um calendário, sozinho, não comprovava a finalidade dos preços. O dicionário oficial Brapi revisado define fechamento do período e adjustedClose com proventos/desdobramentos/bonificações; a amostra pública diária permite um contrato de período encerrado sujeito a revisão, sem exigir booleano de finalização ou serviço pago. Não demonstra retorno total do investidor ou ajuste exclusivamente por splits.

## Decisão e justificativa

Adicionar uma tabela append-only `price_vintages`. Cada aquisição preserva corpo público integral, origem canônica sem token, hash, fetched_at local, available_at real, política versionada e calendário conhecido. JSON e projeção financeira são reconstruídos e revalidados. Uma nova coleta igual também permanece registrada; retry exato recupera a disponibilidade e calendário originais. A→B→A produz uma nova ocorrência final. O representante para cache é o primeiro da sequência contígua atual de material igual. A seleção lê metadados históricos e somente os corpos atual/representante; não carrega todos os corpos por GET.

Bundle 1.2 contém uma evidência de série inteira, projeção financeira limitada à janela móvel e corpo bruto com finalidade de auditoria. Apenas campos financeiros revalidados sustentam números; metadata, hashes, datas e corpo fora da janela não ampliam o corpus. Métricas exigem seus registros calculados. Close informado e adjustedClose permanecem campos distintos. Bundle/analysis 1.0 e 1.1 e seus prompts/schemas são preservados. A versão 1.2 tem schema/adendo próprios e chave de cache material; não há nonce.

Criar IDs próprios de variação de adjustedClose em 1/5/21/63 sessões, desvio diário amostral dos últimos vinte logs e anualização por raiz de 252. Cada janela exige n+1 observações positivas finitas do mesmo vintage, continuidade, última sessão esperada e período encerrado. R63 e volume ausentes não bloqueiam janelas menores. Logaritmos usam `log(b)-log(a)` para evitar divisão intermediária zero/infinita. Derivados inválidos são nulos com motivo; sem teto positivo arbitrário.

Política `technical_alignment_v1`: M5 e M21 dividem a variação logarítmica por s20×raiz(n). Ambos estritamente acima de κ=1 elegibilizam buy; ambos abaixo de −1 elegibilizam sell; outros valores finitos elegibilizam wait. Igualdade não qualifica; sem arredondamento na comparação. Ausência, desvio zero ou cálculo não finito resultam em indisponibilidade. κ é convenção experimental, sem calibração preditiva. Janelas 5/21 valem para reavaliação em 30/90 dias, não previsão por esse prazo.

Fundamentos e notícias são contexto/contrapontos, não votos. Direção exige filing tipado do emissor e contexto substantivo datado/vinculado; a release pode cumprir ambos com uma origem conhecida, sem alegar confirmação independente. A IA pode manter ou reduzir a elegibilidade, nunca inverter/promover. Campos estruturados e texto explicitam caráter experimental. O manifesto atual não oferece comparativos temporais: em 1.2, números do filing precisam identificar uma conta e um tipo de período; comparações numéricas de crescimento são recusadas, inclusive em inferência/síntese/justificativa. O guard não prova toda semântica de prosa ou afirmações sem números.

## Alternativas consideradas

- Alterar revisões históricas por barra: não preservava sozinho o corpo e ampliava migração; rejeitado.
- Tabela de membros por barra: duplicação desnecessária para uma série diária pequena; optou-se por projeção pura.
- Reutilizar qualquer ocorrência antiga com hash igual: apagaria a sequência A→B→A e sua disponibilidade; rejeitado.
- Votar com sinal de lucro/caixa: heurística econômica não justificada; rejeitado. F e notícias permanecem contexto.
- Exigir M21/M63 em 90 dias: criaria requisito universal de 64 observações sem fundamento para o horizonte de reavaliação; rejeitado.
- Rotular adjustedClose como split-only ou retorno total: semântica incorreta; rejeitado.
- Parser geral de linguagem financeira: complexidade desproporcional. A gramática tipada conservadora pode recusar redações legítimas; essa limitação é explícita.

## Consequências

Positivas: replay verificável; ausência de retroatividade; métricas parciais honestas; menor bundle; cache útil; transação SQLite/fence preservados; nenhum novo provedor/dependência/serviço. GETs não criam bundle, análise ou reserva. UI distingue captura atual e snapshot histórico.

Negativas: armazenamento cresce a cada aquisição integral; consulta de metadados históricos ainda cresce linearmente; novas políticas/calendários precisarão manter os validadores/pins antigos. O calendário atual termina em 03/11/2026. A política é uma hipótese experimental, não backtest ou calibração. A semântica financeira total não é decidida pelo validador lexical/tipado. Quatro contas não demonstram valuation, solvência ou crescimento. Rejeitar uma análise histórica ao revalidar não prova automaticamente falsidade financeira e não autoriza regravar seu conteúdo.

## Verificação e revisão

Pareceres metodológicos e revisões do núcleo estão arquivados localmente. A revisão identificou e corrigiu a distinção trimestre/YTD; o aceite é limitado, sem comprovar semântica universal ou desempenho preditivo.

Backend integrado final: 690 passaram após a última correção de produto; frontend: 151, TypeScript/Vite e inspeção visual desktop. Dez testes posteriores cobrem somente os verificadores operacionais preparados. A prova em cópia comparou preservação e revalidação semântica ao baseline; os detalhes operacionais ficam locais. Nenhuma coleta nova ou IA operacional nesta etapa. Evidências detalhadas em `docs/qa-20261031/price-vintages-direction/RESULTADOS.md`.

## Fechamento operacional

A primeira execução do helper recusou antes de persistir: o contexto de conexão SQLite não fechava o handle, enquanto Store tentava seu default DELETE contra WAL. A base permaneceu integralmente igual ao backup. A correção usa `contextlib.closing` e `wal=True` nos helpers, sem refatorar o produto. A condição foi reproduzida em temporário; dez testes e o ensaio efetivo before→import→after-import em cópia WAL passaram. A revisão independente dos helpers confirmou fechamento, fences, hashes e recuperação, limitada a esse escopo. O incidente evidencia que uma transação terminada não implica recurso fechado.

O fechamento operacional detalhado permanece no acervo local. Promoção deve preservar snapshots, ledger, configuração e build anterior, com conferência de hashes e verificação do modo efetivamente iniciado. O aceite técnico não autoriza inferência adicional.
