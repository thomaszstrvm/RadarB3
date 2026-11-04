# Revisão de dados e análise

Execute antes de aceitar um adaptador, uma métrica ou uma saída IA. Registre resultado e exemplos em `docs/provider-validation.md`; nunca inclua tokens.

1. Identifique origem, licença/condição de acesso, endpoint, versão, atraso e quota. Faça uma requisição limitada antes de uma carga ampla.
2. Confira o grão: empresa, classe de ação, ticker, pregão, moeda, período contábil e consolidado/individual. Verifique chaves estáveis e alterações de ticker.
3. Separe relógios: evento, divulgação, publicação, modificação, descoberta, coleta e execução. Campos desconhecidos ficam nulos com precisão/confiança explícitas.
4. Meça cobertura observada. O total do catálogo é conhecido; o total de notícias existentes na web não. Não reutilize o primeiro denominador para o segundo problema.
5. Teste duplicação, sindicação, atualização de matéria, correção de balanço, fonte indisponível, quota esgotada e reinício no meio da tarefa.
6. Confira cálculos em casos pequenos verificáveis: split, dividendo, cotação ausente, volume parcial, lucro negativo e mudança de moeda. Suspender métricas quando a série não permite comparação.
7. Construa bundles sem credenciais e sem conteúdos futuros ao instante de análise. Verifique IDs e o nível de acesso de cada evidência.
8. Valide a saída IA estrutural e semanticamente. Uma fonte existente não basta: ela deve sustentar a afirmação. Verifique amostra manual e casos adversos de `fixtures/scenario_cases.json`.
9. Faça a UI explicar lacunas sem escondê-las em tooltip. “Sem notícias encontradas”, “não pesquisado”, “coleta falhou” e “histórico parcial” devem ser estados distintos.
10. Repita o mesmo job e reinicie o worker: não pode duplicar eventos, análises, alertas nem consumo já confirmado.

Não aprove uma conclusão de oportunidade se ela depender só de manchete, de simulações não calibradas ou da ausência de movimento de preço. Quando a evidência permitir apenas uma hipótese, escreva como hipótese.
