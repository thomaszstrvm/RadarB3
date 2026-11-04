# Implementação Radar B3 — decisões e estado

Atualizado em 26/10/2026. O pacote de planejamento permanece intacto em `original/radar-b3-codex.zip`; o código é uma implementação própria, não uma cópia das skills/projetos de referência.

## Origem e ambiente

- Pacote de planejamento preservado em `original/radar-b3-codex.zip`, com 35 entradas.
- SHA-256 cotejado com original: `edaae6248fe97e0141cc20f3881be2e30f11d978d10491558f5622e39f6c8471`.
- A tentativa Library foi encerrada por incompatibilidade `os.setxattr` no Windows. Não foi contornada; a cópia de Downloads foi usada após confirmação do hash.
- Instalação dedicada ao projeto, preservando trabalhos de outros diretórios.
- Desenvolvimento e testes isolados da instalação operacional; evidências detalhadas permanecem locais.
- Python 3.12.0, Node 24.19.0, npm 11.17.0. Dependências Python isoladas e congeladas em requirements.lock; frontend em package-lock.json.

## Escolhas

1. **SQLite local com WAL corrigido.** O Python disponível carregava SQLite 3.42.0. Baixada DLL x64 3.54.1 somente do site oficial, SHA3 conferido; preload com hash SHA256 obrigatório em backend/__init__.py. Nenhuma DLL do Python global foi alterada. Proveniência em vendor/sqlite/provenance.json. Alternativa DELETE é possível em Store, mas o launcher exige WAL seguro.
2. **API e worker separados.** API consulta e enfileira, um worker mantém lease/heartbeat, checkpoints, cotas e tentativas. A entrega é pelo menos uma vez com efeitos idempotentes; não garante exatamente uma requisição externa após uma queda.
3. **Codex bloqueado até homologação.** CLI embutido 0.164.0-alpha.11.1, login status confirmou ChatGPT e exec --help confirmou schema/read-only/ignore-user-config. Não lemos credenciais. Não foi provado isolamento de leitura/ferramentas/rede, nem modelo acessível para análise; nenhum subprocesso de análise foi executado. Pedidos produzem bundle e ficam bloqueados de modo explícito. Não há API paga, fallback ou controle UI que burle esse bloqueio.
4. **Fontes por capacidade observada.** Três feeds oficiais puderam ser validados. UOL inválido e descobertas pendentes ficam visíveis. GDELT 429 não foi tratado como ausência de notícias. Feed geral passa por filtro de seção. `seendate` do GDELT não vira publicação; links sem data original não sustentam bundle de notícias datadas.
5. **Preços conservadores.** Catálogo inclui todo o conjunto recebido do fornecedor; fundos/BDRs excluídos, desconhecidos visíveis, fracionários/temporários separados por regra documentada. Sem token só as quatro ações públicas podem ser consultadas. Ajustes não homologados ficam unknown e métricas incompatíveis null. Agenda de preços exige calendário de pregões verificado; atualização manual permanece disponível.
6. **Fronteira local.** Host restrito; API recusa origens externas e cross-site. Comandos exigem token de sessão local. Documento inicial permite navegação de um link externo, mas isso não concede acesso de script externo à API. Sem CORS global. Notícias são texto, URLs verificadas e nenhuma instrução de fonte é executada.
7. **Revisões e relógios.** Histórico imutável, publicação/descoberta/coleta separados. UI filtra a janela atual e liga análise ao seu próprio bundle. Arquivo de artigo pode ser consultado por ID para preservar proveniência fora da janela corrente.

## Limites conhecidos

- Não alegar cobertura integral da B3 ou da imprensa. Contagens representam catálogo e registros obtidos, não certificação de completude.
- Calendário B3, ajustes societários, fundamentos CVM e análise real Codex ainda não homologados. CVM tem probe de acesso, não ingestão contábil completa.
- Prioridade numérica/qualidade alta não são inventadas. A UI conserva null/ausência e apresenta a limitação.
- Sem previsão numérica, probabilidades de lucro, ordens ou conexão de corretora. Horizontes 30/90 são dias de calendário.
- A máquina/processos precisam estar ligados para a agenda funcionar; nenhum serviço persistente foi instalado.

## Revisão

Desenvolvimento paralelo e revisão independente foram usados apenas para construir o software. O runtime não contém múltiplos modelos/agentes. Revisão encontrou e motivou correções de janela, bundle errado, teto oculto de 5000 notícias, estados de provedores, ETag prematuro e filtros de seções. Testes e homologação final constam em VALIDACAO-APP.md.

## Continuidade

Os registros operacionais posteriores permanecem no acervo local. Estas decisões são históricas; consulte o código e os ADRs mais recentes antes de aplicar procedimentos. Este documento não concede autorização operacional.
