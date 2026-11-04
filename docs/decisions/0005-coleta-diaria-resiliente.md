# ADR 0005 — coleta diária independente por fonte

Data: 2026-10-29. Estado: aceito; validado em QA, worker operacional desligado.

## Contexto

Uma falha retryable de RSS encerrava o job coletivo antes das próximas fontes. Retry-After era retornado ao chamador, mas uma nova tarefa/reinício podia consultar o host antecipadamente. UOL enviava Latin-1 declarado somente no HTTP e datas em português; G1 usava -0000 e alternava compressão. O agendamento não atualizava a série macro automaticamente.

## Decisão

Manter o worker único e as oito fontes. Capturar falhas de transporte/quota por fonte, registrar resultado parcial e continuar as demais. Persistir cooldown por host em `http_backoff`, aplicado antes de DNS e reserva HTTP. Respeitar Retry-After inclusive acima de um dia; sem cabeçalho válido, esperar ao menos 60 segundos. A próxima coleta normal pode ocorrer depois do prazo, nunca antes por causa deste controle.

Decodificar apenas charsets explicitamente suportados, com erros estritos. Aceitar gzip/deflate mantendo limites separados de bytes recebidos e expandidos e exigindo término íntegro. Normalizar nomes portugueses de datas e -0000 conforme RFC; continuar rejeitando datas sem fuso. Reutilizar o adaptador BCB na agenda diária deduplicada. Preços automáticos continuam exigindo sessão verificada.

## Alternativas

- Reiniciar todo o lote em cada falha: repete fontes saudáveis e permite que uma indisponibilidade bloqueie as outras.
- Cooldown somente em memória: perde o limite após reinício.
- Introduzir framework RSS/scheduler: custo de dependência e manutenção desnecessário para os formatos observados.
- Presumir calendário segunda–sexta ou fuso brasileiro: produziria certeza indevida sobre feriados, publicação e fechamento.

## Consequências

Positivas: falhas ficam localizadas, limites sobrevivem a reinício, fontes brasileiras passam por ingestão com datas e deduplicação, sem ampliar domínios de artigos nem capacidades IA. A nova tabela é aditiva e criada pelo Store na próxima inicialização; não exige reescrever dados existentes. Sem nova dependência.

Negativas/limites: intervalo normal pode adiar coleta além do Retry-After; fonte indisponível permanece lacuna. Charsets/compressões não suportados continuam bloqueados. Horários declarados pela fonte são preservados, não certificados. O parser não comprova veracidade jornalística. A tabela de cooldown usa relógio do sistema, como os demais controles de tempo do aplicativo. Testes não homologam atualização automática em produção enquanto o worker estiver desligado.

## Evidência

`docs/qa-20261029/validation-diagnostics/RESULTADOS.md`: 179 testes backend aprovados; cinco feeds coletados em QA; G1/UOL com datas reconhecidas; uma página de catálogo, BCB e uma amostra PETR4. Revisão independente levou à cobertura de HTTP 304 com gzip sem corpo. Banco real/MXC preservados e zero IA nesta etapa.
