# Decisões, pressupostos e limites

Este arquivo evita que o implementador transforme toda escolha em uma nova pergunta. Se uma preferência não estiver expressa, use o padrão abaixo e exponha a configuração quando fizer sentido.

| Tema | Decisão inicial | Como revisar |
|---|---|---|
| Mercado | Ações e units B3 | Acrescentar outros mercados em adaptadores futuros |
| Período de notícias | Últimos 3 meses de calendário | Configuração de produto, sem confundir com 90 dias |
| Horizonte dos cenários | 30 e 90 dias de calendário, suposição de projeto | Usuário pode ajustar na interface |
| Atualização | Notícias a cada 60 min; preços diariamente após fechamento verificado; catálogo semanal | Reduzir conforme quotas e máquina ativa |
| Inicialização | Bootstrap em lotes, mostrando parcial até terminar | Retomar checkpoint após interrupção |
| IA | Codex CLI com login de assinatura existente; um modelo selecionado no setup | Verificar suporte/termos/limites atuais; sem fallback faturado |
| Limite de IA | Máximo inicial de 10 novas análises/dia, 1 simultânea | É teto próprio conservador, não quota oficial; configurar |
| Hospedagem | Localhost, um usuário | Nenhum deploy automático |
| Alertas | Caixa interna do aplicativo | Telegram/email fora do MVP |
| Design | Claro padrão, escuro opcional, denso e legível | Tokens centralizados, acessibilidade preservada |
| Plataformas | Priorizar execução local multiplataforma; documentar SO realmente testado | Sem prometer testes em SO não disponível |

## Questões que precisam de prova, não de promessas

1. A brapi gratuita fornece o conjunto de ativos/históricos necessário com o token do usuário? Fazer probe e relatar cobertura e consumo estimado antes de disparar bootstrap amplo.
2. Quais feeds/rotas editoriais funcionam e permitem a coleta? Registrar diagnóstico por fonte. Não fingir que escolher um domínio resolve a integração.
3. O índice GDELT recupera a janela desejada para os oito domínios sem saturação persistente? O teste mede as consultas, não a completude da web.
4. O Codex CLI instalado aceita login e saída estruturada, respeita a quota, não herda ferramentas ou permissões excessivas e permite invocação local apropriada? Testar uma análise sintética limitada. Não copiar a sessão autenticada para um servidor ou ler arquivos de credencial.
5. Há preços comparáveis e ajustes suficientes para as métricas? Se não, desativar a métrica e explicar o motivo. Não misturar séries para ocultar buracos.

## O que a gratuidade permite afirmar

O projeto pode ser construído com bibliotecas abertas, armazenamento local e fontes com acesso gratuito, sem nova assinatura obrigatória. Não se garante capacidade ilimitada, histórico integral de notícias, texto de paywall, cotações em tempo real, dados societários completos ou funcionamento enquanto o computador estiver desligado.

Se o usuário não tiver acesso vigente à IA por assinatura, a análise automática fica indisponível até configurar um provedor compatível sem novo custo. Não instalar outro modelo por conta própria: isso alteraria a escolha operacional. Coleta, navegação e exportação continuam funcionando.

## Registro exigido na entrega do aplicativo

Criar `docs/implementation-decisions.md` e `docs/provider-validation.md` com decisões reais, versões, data de teste, limitações, endpoints verificados sem segredos, quantidade de dados disponível, comandos executados e resultado. Informações deste pacote datadas de 26/10/2026 são uma base de projeto, não uma homologação permanente.
