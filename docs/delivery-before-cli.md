# Entrega anterior à integração da CLI — registro técnico

Registro histórico de 26/10/2026, com detalhes particulares de instalação e
operação mantidos somente no acervo local. Não descreve o estado atual do serviço.

A implementação usa API e worker separados, SQLite com WAL e frontend local.
Coleta parcial permanece explícita; ausências não viram zero e a execução de IA
depende de homologação e configuração próprias. Sem execução de ordens financeiras.

Para preparar uma instalação, consulte `../README.md`. Verifique a configuração,
as versões, a porta e os processos atuais antes de iniciar. Não reutilize caminhos,
PIDs, contagens ou autorizações de relatórios antigos como parâmetros operacionais.
