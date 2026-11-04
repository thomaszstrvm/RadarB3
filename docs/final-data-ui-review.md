# Revisão independente final — dados e UI

Fonte de verdade: `<caminho-local>`. Revisão realizada em 26/10/2026. Nenhuma implementação ou base real foi alterada. Sem chamadas de rede, provedores ou IA. Homologação da CLI fora deste escopo.

## Resultado

43 testes Python passaram: 11 API/regressões e 32 core. Os cinco achados anteriores estão corrigidos no escopo dos testes: bundle correspondente à análise; exclusão de evento cuja evidência expirou; detalhe de notícia após 5.000 registros; estados dos provedores visíveis; preferência de horizonte refletida no bundle.

Contagens e observações particulares da base foram omitidas desta edição. A revisão histórica não certifica o estado atual da instalação.

A UI mostra numerador/denominador de cobertura observada, não promete preços para todo o universo. `backend/api.py:166` mantém retorno e volume relativo como null; `frontend/src/format.ts:1-3` apresenta ausência como “Indisponível”, distinguindo zero real. `backend/evidence.py:46-56` rejeita comparação com ajuste desconhecido e sessão incompleta. `backend/worker.py:97-101` condiciona coleta automática de preço ao calendário verificado. Gráfico usa pontos observados, não interpola lacunas, e declara que associação não comprova causalidade (`frontend/src/components.tsx`, PriceChart). Vínculos de eventos são candidatos, não causalidade confirmada.

## Achado residual

**P2, condicionado à existência de análises — referências da análise podem não ser navegáveis na UI.** `frontend/src/App.tsx:39`, função `AssetPanel`, renderiza `AnalysisView(a.analysis)` e `Articles(a.articles)`, mas não renderiza `a.evidence`, que contém o bundle correto. `frontend/src/components.tsx:10`, `EvidenceRefs`, gera links `#evidence-{id}`; apenas `Articles` cria esses alvos para revisões atuais. Uma análise que cite revisão anterior, preço ou métrica não terá alvo correspondente, embora a API retorne o bundle correto. Antes de apresentar análises ao usuário, renderizar evidências imutáveis do bundle ou disponibilizar navegação direta por ID de evidência. Constatação por inspeção de código; não foi criada análise na base real.

## Testes executados

Na pasta final, Python final com `-B`, `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider` e `--basetemp` dentro do workspace:

1. `pytest tests/test_review_data.py tests/test_api.py -q`: 11 passed, 1.47 s. TestClient executado com autorização de sandbox devido ao socketpair local; sem rede externa. Uma advertência de depreciação Starlette/httpx, sem falha.
2. `pytest tests/test_core.py -q`: 32 passed, 6.87 s.

Não foram repetidos testes de browser/frontend nem probes externos nesta revisão. Conclusões visuais são de inspeção do código final; não equivalem a validar cada interação em navegador. A leitura da base é um retrato do momento, não garantia de atualização futura. Calendário e ajustes continuam não homologados; preços de quatro ativos não validam cobertura dos demais.
