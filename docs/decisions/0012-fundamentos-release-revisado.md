# ADR 0012 — Um release com observações financeiras revisadas

Data: 2026-10-31. Estado: implementação isolada para revisão; nenhuma promoção/importação operacional nesta etapa.

## Contexto

O bundle VALE3 tinha notícias e preços, mas nenhum dado contábil tipado. O ADR0010 mantém `buy`/`sell` bloqueados por ausência de prontidão direcional verificável. Inserir texto de balanço ou uma flag não comprova identidade, período, moeda, escala nem a conta utilizada.

Uma cópia pública do release 3T26 da Vale foi preservada em `docs/qa-20261031/data-readiness/fundamentals/`. Quatro contas e sete observações, identidade, URLs, páginas, moeda, escala e normalização de períodos receberam aceite limitado do Validador, **agente de desenvolvimento independente**, com parecer arquivado localmente. Não se trata de transcrição/revisão humana, auditoria contábil, validação de completude ou recomendação financeira. O manifesto formal e a implementação permanecem sujeitos à revisão material antes de promoção.

## Decisão

1. Contrato `schemas/fundamentals-v1.schema.json`, limitado a `earnings_release` e ao mapeamento `vale3-earnings-release-3t26-v1`, versão `1.0`. Preservar separado de ITR/DFP. As contas são receita líquida, lucro atribuível aos acionistas da Vale, caixa/equivalentes e caixa operacional líquido. Trimestre/acumulado são observações distintas; caixa é estoque em uma data.
2. Valores são coeficientes `Decimal` serializados como strings, moeda USD e escala numérica `1000000`. Um coeficiente `10599` conserva a escala de milhões; não é multiplicado antes de gerar a quantidade lexical. Ausente exige `null` com motivo, mas qualquer mudança nas sete observações aprovadas, inclusive uma ausência nova, exige outro mapeamento revisado.
3. Fixar no código o hash SHA-256 **canônico** do manifesto, com serialização `core.dumps` (UTF-8, chaves ordenadas, sem espaços e sem NaN). Isso tolera apenas diferenças de apresentação JSON sem alterar o documento lógico. PDF e mapeamento têm hashes diferentes: autenticar os bytes do PDF não autentica a transcrição.
4. Manifesto: `docs/qa-20261031/data-readiness/fundamentals/vale3-3t26-reviewed-v1.json`. Hash canônico: `4dfbbffc6cbf6779c5a95ec1f7ef164599f42114107925794cca38f09da64cc4`. PDF: `d73d497d329a02e7b98c71ff719f0e8499381a6e8e1e7e850a3073b11b5c3612`. URL de entrada Vale e link MZ específico, identidade CVM/RI, relógio de coleta, localizadores e referência de revisão são parte do mapeamento fixado; o importador não escolhe uma flag para autorizar fatos novos.
5. Importador offline `scripts/import_fundamentals.py`: banco **existente e inativo**, `expected_profile="demo"` por padrão; CLI `--profile demo|real`, com real exigindo escolha explícita. A igualdade com `settings.profile` é verificada dentro de `BEGIN IMMEDIATE`; o argumento não altera o perfil do banco nem dispensa pins, revisão, inatividade ou ativo elegível. Não existe `--force`. Usa conexão SQLite `mode=rw`, sem inicializar `Store`. A opção real foi autorizada pelo coordenador após o primeiro checkpoint demo-only, para permitir uma operação futura revisada; nenhuma importação operacional foi executada pelo implementador.
6. Criar explicitamente a tabela dedicada `fundamental_releases` e triggers de imutabilidade dentro da mesma transação do registro e evento de auditoria. Não usar `executescript`, pois seu commit implícito romperia o rollback integral. Falha ou interrupção desfaz DDL, registro e auditoria. Repetição idêntica é no-op e não renova disponibilidade.
7. `available_at` é o instante real da importação; o chamador/manifesto não fornece esse relógio. `first_seen_at` significa primeira disponibilidade **no aplicativo** e é igual à importação. `fetched_at` conserva a coleta do artefato. Data pública declarada tem precisão de dia; `Last-Modified` HTTP não vira horário de divulgação. Não preencher um corte anterior com conhecimento importado depois.
8. Leitor retorna lista vazia se a tabela não existe ou se não há release elegível. Nesse caminho, `build_bundle` mantém inputs, gaps e flags anteriores, preservando cache. Com release elegível, um novo `filing` estruturado entra no novo snapshot, alterando seu hash. Snapshots antigos não são enriquecidos nem regravados.
9. Elegibilidade requer publicação dentro da janela móvel de três meses e disponibilidade até `as_of`. Períodos contábeis são separados: o acumulado de janeiro pode estar em um release publicado dentro da janela. Não mudar a janela noticiosa. O dado expira por publicação; não é usado como fundamento recente indefinidamente.
10. O budget preserva o filing integralmente, assim como o último preço/insumos obrigatórios. Se não couber, falha antes de persistir. Filing não incrementa cobertura editorial. Mostrar limitação de quatro contas de um único release, sem afirmar fundamentos completos.
11. Quantidades do novo filing vêm somente dos valores financeiros tipados do manifesto aprovado, com USD e escala de milhões. Datas, páginas, identificadores, metadados de escala e outros textos não sustentam valores. Não converter moedas/escalas nem ampliar a gramática numérica legada. Uma segunda escala reconhecida em sequência ou ligada por `de` é recusada lexicalmente: `milhões de milhões` não pode aproveitar apenas a primeira escala. Essa recusa vale também para artigos legados; prosa normal após uma escala permanece aceita. A correspondência lexical ainda não prova atribuição econômica da afirmação à conta ou ao período correto.
12. `buy` e `sell` continuam recusados **incondicionalmente**. Este recorte não resolve ajuste, calendário, comparabilidade de preços, cobertura financeira ou política de decisão direcional.

## Alternativas consideradas

- Parser genérico de PDFs, download de lotes CVM e fundamentos completos: desproporcionais ao recorte e às evidências disponíveis.
- Guardar só o PDF/hash e aceitar valores fornecidos pelo importador: recusado; não autentica o mapeamento das células.
- Criar tabela na inicialização do aplicativo: recusado; a importação explícita e o leitor tolerante evitam migração em bancos reais neste incremento.
- Reutilizar artigos: recusado; alteraria contagem editorial e esconderia o caráter financeiro estruturado.
- Usar revisão manual/humana como rótulo: recusado; a revisão foi feita por agentes de desenvolvimento.

## Consequências e evolução

Benefícios: pequeno recorte rastreável, cálculo decimal sem conversões ocultas, repetição segura, rollback integral e isolamento operacional. Custos: um release não fornece cobertura suficiente; novos mapeamentos exigem revisão de valores/proveniência e novo pin. Não há extração automática ou garantia semântica integral.

**Preservação histórica:** os schemas, mapeamentos e pins desta versão devem continuar disponíveis para revalidar snapshots existentes. Uma próxima versão não pode simplesmente substituir a constante atual e invalidar o filing antigo. A evolução deve ser aditiva, preservando a validação v1; não foi criado framework multiversão antes de existir outra versão aprovada.

Uma futura promoção operacional exige revisão do delta final, teste de cache/UI, backup e parada coordenada de API/worker. O código já permite perfil real explícito, mantendo todas as verificações; o custo da promoção é esse procedimento de execução, não infraestrutura nova nem ampliação do contrato financeiro. A única escrita em tabela anterior é um evento append-only de auditoria; linhas anteriores, quota, análises, bundles, jobs, fontes e perfil permanecem intactos. Nenhuma flag do arquivo deve substituir essa política. Não se cria uma exigência permanente de autorização humana para dados já autorizados.

## Verificação

Testes isolados em `tests/test_fundamentals.py`: contrato/pins, moedas/escalas/períodos, PDF alterado, replay idempotente, imutabilidade, falhas/interrupções após DDL, perfil real recusado em banco temporário, worker/job ativos, relógios e disponibilidade, janela, cache sem documento, novo snapshot, budget, suporte numérico e direção bloqueada. Usar runner que bloqueia rede e guard que proíbe Codex real; resultados ficam no diretório de QA do recorte. Suíte integrada e revisão material precedem promoção.
