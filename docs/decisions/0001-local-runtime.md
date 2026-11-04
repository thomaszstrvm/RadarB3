# ADR 0001 — runtime local e degradação explícita

Data: 2026-10-26. Estado: aceito para o escopo autorizado.

Contexto: aplicação pessoal precisa continuar consultável se fonte ou assinatura falhar, sem ampliar custos/acessos. Python existente tem SQLite anterior à correção WAL.

Decisão: FastAPI e worker serial independente, SQLite WAL com DLL oficial verificada dentro do projeto, frontend React compilado servido na mesma origem. CLI de análise permanece bloqueado até isolamento e modelo serem comprovados.

Alternativas: Redis/Postgres/serviço externo acrescentariam infraestrutura desnecessária; alterar Python global afetaria outros projetos; desabilitar sandbox para conseguir uma análise violaria o escopo. Journal DELETE é fallback técnico de Store, porém não o modo aprovado pelo launcher.

Consequências positivas: isolamento do projeto, retomada e diagnóstico sem custo de serviço novo, frontend sem credenciais de fornecedor. Consequências negativas: Windows x64 necessita DLL local verificada; uma máquina/processo ativo é necessário; análises automáticas não estão homologadas e métricas dependentes de ajustes ficam ausentes.
