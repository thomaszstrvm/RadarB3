# Produto e escopo

## Pergunta que o painel responde

“O que mudou no mundo ou no Brasil, quais empresas da B3 podem ser afetadas, que reação de mercado já foi observada e quais hipóteses merecem investigação agora?”

O produto organiza evidências e cenários. Não estima um preço futuro com precisão aparente, não garante encontrar toda notícia e não determina se uma compra é adequada à situação financeira pessoal do usuário.

## Usuário e operação

Uso pessoal, um usuário, computador local. Interface em pt-BR, reais por padrão, dados originais preservados na moeda de origem. Backend em Python. Não exigir hospedagem, Figma, terminal financeiro pago ou banco externo. A análise IA pode depender de internet e da franquia de uma assinatura já existente.

Universo inicial: ações e units negociadas na B3. O catálogo deve registrar ativos ativos/inativos, tipos, empresas, setores e qualidade da classificação. A cobertura é do catálogo do fornecedor consultado, não uma certificação de completude da bolsa. Varredura ampla não significa que cada ativo receberá uma análise IA todos os dias: o código faz triagem geral e envia os eventos materiais ao único modelo.

## Fluxo principal

1. Primeira execução: validar configuração, descobrir o universo, testar fontes e mostrar um diagnóstico. Carregar a janela histórica em tarefas resumíveis, com progresso por fonte e data.
2. Atualizações: coletar incrementos de notícias, documentos e preços; agrupar republicações; detectar fatos novos, revisões e mudanças de indicadores.
3. Relacionar evento a empresa/setor usando aliases, entidades e exposição econômica documentada. Separar vínculo direto de possível efeito indireto.
4. Selecionar bundles de evidência por novidade/materialidade/cobertura, respeitando limites. A IA gera síntese, mecanismos possíveis, cenários contrários e lacunas.
5. Mostrar resultados ordenáveis com preço observado, data, atualização e qualidade. Abrir uma linha deve revelar gráfico e fontes, sem perder filtros.
6. Mudanças materiais desde a última análise geram alertas internos deduplicados. Favoritos destacam ativos, mas não limitam a coleta geral.

## Tempo

- Janela de notícia: do início da data local de três meses antes até o instante `as_of`. Para dias inexistentes, ajustar ao último dia válido do mês; 31/05 menos três meses → 28/02 ou 29/02.
- Data sem horário: registrar precisão “date”, assumir incerteza de ordem intradiária; não inferir que o mercado reagiu antes/depois no mesmo dia.
- Fato antigo recontado em matéria recente não vira evento novo automaticamente. Registrar `event_date`, `first_disclosed_at`, `published_at` e confiança temporal.
- Históricos de preço anteriores à janela podem ser usados apenas para contextualização se gratuitos e disponíveis, identificados separadamente. Não alargar o escopo de notícias sem uma configuração explícita.
- Horizontes prospectivos propostos: 30 e 90 dias de calendário. Cada hipótese informa quando deve ser reavaliada; não confundir o prazo com validade estatística.

## Telas

| Tela | Trabalho do usuário |
|---|---|
| Visão geral | Saber o que mudou e o grau de atualização/cobertura |
| Mercado | Varrer todo o universo, filtrar setor/tipo/evento/qualidade e ordenar |
| Ativo | Comparar preço, eventos, dados financeiros, cenários e evidências |
| Eventos | Agrupar matérias sobre o mesmo acontecimento e ver empresas afetadas |
| Favoritos | Acompanhar ativos sem alterar o universo geral |
| Fontes e cobertura | Ver falhas, janelas disponíveis, quotas, recência e atrasos |
| Configurações | Definir agenda, orçamento de processamento e modo de IA |

## Definição de resultado útil

Uma oportunidade é uma **hipótese para investigação**, com mecanismo econômico, evidências, condições de confirmação/invalidação e reação já observada. Saídas possíveis: investigar, acompanhar, evitar por enquanto, dados insuficientes. “Evitar” exige explicação objetiva; não é recomendação personalizada de venda. Não forçar uma oportunidade por ativo nem uma quantidade mínima diária.

## Fora da primeira versão

Execução de ordens, intraday/tempo real garantido, alavancagem, opções, portfólio otimizado, rede social, notícias de toda a internet, multiusuário, aplicativo móvel nativo, múltiplos modelos, treinamento de previsão, backtest que reivindique causalidade ou desempenho de estratégia. Exportar CSV/JSON/Markdown com proveniência é permitido; compartilhar publicamente conteúdo de terceiros não é parte do MVP.
