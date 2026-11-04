# ADR 0003 — Recuperação de gravações HTTP por recibos duráveis

Data: 26/10/2026. Estado: implementado; validação integrada descrita no relatório HTTP.

## Contexto

Sessão, envio e leitura de respostas HTTP podiam aguardar indefinidamente. Cancelar o fetch não informa se o servidor já confirmou a escrita. Criar uma regra ou reenfileirar uma análise com identificação nova após perder a resposta pode duplicar efeitos. O SQLite local e o worker único continuam sendo a arquitetura existente.

## Decisão e justificativa

Toda gravação da interface recebe uma chave UUID, método, caminho e corpo imutável. Antes de enviar, a aba persiste esse comando em sessionStorage. Há uma única gravação pendente por aba; consultas e navegação continuam disponíveis. Espera de sessão, cabeçalhos e JSON têm prazo de 10 segundos. Não há reenvio automático.

O servidor associa a chave ao hash canônico de método/caminho/payload. O efeito e o recibo público são gravados na mesma transação SQLite. Uma chave repetida retorna a resposta original; a mesma chave com comando diferente é rejeitada. Exceções inesperadas desfazem efeito e recibo; rejeições de domínio desfazem efeitos parciais e guardam a rejeição. Os métodos de fila recebem a conexão da transação, evitando commits intermediários.

Após resposta perdida, a interface consulta uma vez GET /api/v1/mutations/{key}. Um 404 significa resultado desconhecido, pois a solicitação original pode ainda estar em processamento. A interface oferece consultar novamente ou reenviar explicitamente a mesma identificação/payload. A transação impede repetir um efeito já confirmado. Um recibo confirma o comando; conclusão da coleta/análise continua sendo consultada no acompanhamento de jobs.

## Alternativas consideradas

- Apenas AbortController: limita a espera, mas não resolve o resultado incerto.
- Repetir automaticamente com chave nova: duplica efeitos e pode consumir IA indevidamente.
- Inferir sucesso pelo estado atual do recurso: não distingue solicitações concorrentes nem impede um pedido antigo de terminar depois.
- Fila distribuída/serviço externo: custo e complexidade desnecessários para um painel local.

## Consequências positivas

Recuperação sobrevive a reload da aba e reinício da API, reduz a ambiguidade e permite testar perda de resposta sem IA. Nova tabela é aditiva; não altera dados financeiros, credenciais, MXC, agenda, limites nem modelo. Recibos não incluem tokens, corpos de pedido, respostas brutas de IA ou diagnósticos privados. Guardam apenas a resposta pública do comando e seu fingerprint.

## Consequências negativas e limites

Recibos não expiram automaticamente, pois expirar permitiria executar novamente uma chave antiga. Crescem com o uso e exigirão uma política específica caso volume se torne relevante. A transação ocupa o escritor SQLite por tempo curto; não inclui rede ou IA. Clientes legados sem chave permanecem compatíveis, mas não obtêm recuperação correlacionada.

sessionStorage conserva o comando apenas na aba/origem; fechar definitivamente a aba pode perder sua identificação local. Storage inacessível ou corrompido bloqueia novas gravações com aviso em vez de apagar uma pendência. Atores em abas distintas podem emitir intenções distintas, cada uma com sua própria chave. A garantia é executar no máximo uma vez cada comando identificado, não adivinhar que duas intenções diferentes deveriam ser uma só. Não há garantia de disponibilidade quando API/disco permanecem indisponíveis.
