# ADR 0004 — Diagnóstico mínimo por execução

Data: 29/10/2026. Status: implementado e testado isoladamente, implantação pendente.

Contexto: a rejeição ITUB4 conservou apenas código genérico; a quota ocultou a falha anterior no estado final do job. Guardar saída integral ou mensagens de exceção ampliaria exposição de conteúdo externo.

Decisão: guardar metadados limitados por allowlist no audit_events existente, atomicamente com a finalização da execução. Correlacionar com job_id explícito. Expor consulta somente leitura e tratar registros antigos como detalhe ausente.

Alternativas: saída integral facilitaria reconstrução, mas aumenta retenção e risco; texto livre da exceção pode conter valores privados; adicionar tabela/infraestrutura própria seria custo desnecessário neste volume local.

Consequências positivas: código/regra/campo consultáveis sem nova chamada; quota e MXC permanecem independentes; nenhuma migração destrutiva. Negativas: diagnóstico antigo não recuperável; listas precisam acompanhar regras novas; audit_events cresce sem expurgo automático. A validação financeira mantém suas limitações e não foi relaxada para aceitar uma resposta desconhecida.
