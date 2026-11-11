# Clareza operacional e cobertura — validação de 04/11/2026

## Entrega

Implementação registrada em `89c9527`, após baseline `426b8fc`, suporte revisado
`3d70eef` e isolamento da fixture de atestado `2fc0dac`. Não houve instalação
de dependências, nova fonte, inferência operacional ou operação financeira.

- Mercado apresenta modo, executor e quatro contadores do universo elegível sem
  exigir expansão de detalhes. Ausência de informação permanece diferente de zero.
- Fontes mostra a limitação histórica do RSS mesmo quando a coleta está disponível,
  traduz o motivo da suspensão da CNN e adapta o conteúdo para cartões no celular.
- Menu mobile mantém a seção atual visível, restaura foco e preserva a consulta.
  Preço, sessão e ausência de preço aparecem junto ao ativo na lista compacta.
- Agenda distingue espera, dia sem pregão e calendário não validado. O limite de
  fechamento/correção é estrito: igualdade ainda significa espera.
- Detalhe informa sessões presentes e ausentes da janela sem alterar fórmulas,
  capturas ou análises históricas. R63 exige 64 observações para 63 intervalos.

O [ADR 0017](decisions/0017-cobertura-e-estado-temporal.md) registra as decisões,
alternativas e consequências. A mudança não preenche as lacunas de dados existentes.

## Verificações novas

| Escopo | Resultado | Limite |
|---|---|---|
| Backend | 436 casos distintos aprovados, zero falhas finais ou ignorados | Seleção de regressões e provas independentes; não é nova execução da suíte inteira |
| Frontend | 235 testes em 28 arquivos, TypeScript e Vite aprovados | Dependências completas copiadas de ambiente existente; não é instalação limpa |
| Preview integrado | 12 verificações HTTP aprovadas, comandos recusados com 405 | Cópia de backup de 04/11 às 17:05:51, sem worker, somente leitura |
| Revisão visual | Mercado, Fontes, PETR4 e VALE3 aprovados nos casos exercitados | Desktop e viewport solicitado de 390×844; largura útil medida de 380 px |
| Navegação | Enter/Escape/foco, seção atual e ida/volta com busca PETR4 preservados | Não cobre todas as combinações de filtros, leitor de tela ou navegação completa por Tab |
| Especificação | ZIP e 34 arquivos originais íntegros; blobs Git conferidos | `.gitattributes` evita conversão dos bytes originais |
| Recuperação do baseline | Clone local limpo e 1.320 testes identificados | Apenas coleta de testes, sem executar fixtures; usa dependências locais existentes |
| Detector Impeccable | Saída zero e nenhuma ocorrência | Verificação mecânica complementar à inspeção renderizada |

As contagens não somam execuções repetidas. O único caso pendente da regressão
backend exigia `None` antes da disponibilidade do calendário e confundia motivo
com erro. As expectativas foram alinhadas ao estado explícito, preservando os
asserts de corte temporal e histórico; a revalidação independente passou.

A fixture do operador passou em 67 casos com leitura do atestado operacional
proibida. O atestado continua fora do Git. Esses 67 casos já pertencem aos 436.

Dois ajustes no harness da coleta de testes do clone foram necessários para
direcionar temporários e log para a pasta permitida. Os primeiros bloqueios foram
preservados; a coleta final terminou sem eventos bloqueados. Não eram falhas da aplicação.

## Preservação e evidências

Os helpers e documentos públicos necessários foram incluídos por lista explícita;
bancos, ambientes, builds, credenciais e demais artefatos locais não foram adicionados.
Os 11 PDFs de suporte tiveram texto extraído e proveniência pública inspecionada;
a varredura limitada não detectou marcadores de credenciais, sem alegar auditoria
exaustiva de segredos. Exclusões adicionais em `.git/info/exclude` são locais e não
acompanham clones. Os arquivos originais do projeto permanecem preservados.

Diretórios locais de evidência, mantidos fora do baseline:

- `radarb3-backend-patch-review-20261104-173506`: relatórios, JUnit, hashes e resultados deduplicados.
- `radarb3-ui-clarity-20261104-172831`: relatório incremental, `mobile-junit.xml`, build e manifestos finais.
- `radarb3-integrated-preview-20261104`: cópia congelada, recibos das barreiras e verificações HTTP.
- `RadarB3-parecer-visual-candidato8250-20261104.md`: critérios exercitados pelo Design Critic.
- Pasta do coordenador `runs/reliability-20261104`: commits, recuperação Git, detector e backups de ativação.

O preview preservou hashes de ledger, execuções, análises e jobs. Suas barreiras
são controles da aplicação, não um sandbox do Windows. As capturas visuais foram
inspecionadas no navegador; a ferramenta não forneceu arquivos PNG exportáveis.

## Estado da ativação local

Candidato aceito. Ativação operacional ainda não registrada neste relatório.
Backup SQLite consistente, restauração ensaiada em outro destino e cópias dos
builds anterior/candidato foram preparados. Não restaurar banco sobre produção.

## Limites

Os sinais permanecem experimentais. Aprovação de testes não demonstra capacidade
preditiva, ajuste financeiro completo ou cobertura integral de preços/notícias.
A revisão visual não simulou uma série suficientemente antiga para observar o
aviso de defasagem; o caso é coberto por teste automatizado. Há aviso de depreciação
Starlette/httpx, sem mudança de dependência nesta entrega.
