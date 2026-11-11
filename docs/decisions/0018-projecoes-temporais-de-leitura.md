# ADR 0018 — Projeções temporais de leitura

- Data local: 10/11/2026 (Brasília).
- Estado: implementado e validado em candidato local; ativação conforme relatório de entrega.

## Decisão

As telas de ativos, preços, fontes e operação usam um corte temporal coerente. Preços legados selecionam a última revisão conhecida naquele corte, incluindo desempate por ordem de inserção, sem substituir null por valor antigo. O instante exato do encerramento/correção continua em espera, como a agenda.

O diagnóstico de fontes separa captura preservada, tentativa operacional e cobertura documental. Uma falha posterior não desaparece devido a uma captura antiga. provider_state guarda somente a última linha por provedor: sua projeção não constitui replay histórico completo.

Relógios de tentativa e sucesso exigem timezone e instante não posterior ao corte. Relógios inválidos ou futuros são omitidos da projeção, com motivo explícito e sem regravar registros. Tentativa sem relógio mantém conservadoramente a falha existente; tentativa com relógio comprovadamente inválido/futuro não autoriza expor o erro, detalhes ou retry como evento confirmado. Sucesso anterior válido pode permanecer. next_attempt_at é programação e pode estar no futuro.

O calendário documental mantém cobertura e agenda verificadas separadas da inconsistência do estado operacional. /sources e /operations mostram o mesmo erro de relógio sem invalidar silenciosamente o documento conhecido. Captura de preços inconsistente gera diagnóstico próprio e contagem null, sem fallback para captura antiga.

## Preservação e limites

- Sem migrações, revisão de cálculos históricos, chamadas de IA, coleta, alteração de scheduler ou dependências.
- Testes sintéticos e revisão independente cobrem cortes, offsets, null, corrupção, recuperação e acordo entre endpoints.
- Contrato frontend aceita count e limites de intervalo nulos.
- A inspeção local não identificou relógios futuros/inválidos em provider_state em 10/11/2026 às 21h05. Os defeitos foram reproduzidos sinteticamente.
- A leitura de clocks de dados na operação percorre os registros e deve ser reavaliada se a base crescer substancialmente. Não foi feito benchmark de escala.
- JSON estruturalmente inválido em details_json e relógios válidos porém mutuamente contraditórios são limites de integridade ainda não tratados neste ciclo.
- Captura inválida não é recuperação automática nem autorização para recoleta. Sinais permanecem experimentais e cobertura limitada.
