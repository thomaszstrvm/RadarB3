# Radar B3 — pacote para implementação no Codex

Versão 1.0 · Pesquisa encerrada em 26/10/2026 · Idioma do produto: português do Brasil.

Este pacote contém o planejamento, os contratos de dados, os prompts e as instruções para construir o sistema. **Não contém um aplicativo já implementado ou provedores já homologados.** Nenhuma compra, assinatura, publicação ou instalação de skill foi realizada por este pacote.

## Como usar

1. Extraia este ZIP em uma pasta de projeto e abra essa pasta no Codex.
2. Cole o conteúdo integral de `PROMPT_INICIAL_CODEX.txt` na conversa do Codex.
3. O Codex deve ler `AGENTS.md`, os documentos numerados e os contratos antes de implementar. Ele deve continuar até entregar o aplicativo, testes e instruções de execução.
4. Configure somente as credenciais gratuitas realmente necessárias. A proposta usa o login da assinatura ChatGPT/Codex já existente; isso consome sua franquia. Não é uma API gratuita ilimitada.

O padrão é uso pessoal local, ações e units da B3, notícias mundiais e brasileiras dos últimos **três meses de calendário**, um único modelo de IA e nenhum novo serviço pago. As escolhas configuráveis estão em `config/project.example.json` e `docs/09-decisoes-limites.md`.

## O que há aqui

| Arquivo/pasta | Finalidade |
|---|---|
| `PROMPT_INICIAL_CODEX.txt` | Mensagem exata para iniciar o desenvolvimento |
| `AGENTS.md` | Regras permanentes do projeto |
| `docs/01` a `docs/09` | Produto, arquitetura, dados, análise, design, aceite, skills, pesquisa e decisões |
| `config/` e `.env.example` | Parâmetros e fontes, sem segredos |
| `schemas/` | Contratos JSON para evidências, eventos e saída da IA |
| `prompts/` | Instruções da IA de análise durante a operação |
| `workflows/` | Revisões de design, dados e integração de skills |
| `design/` | Tokens e esquema visual original; não é uma tela funcional |
| `fixtures/` | Exemplo sintético de contrato e casos de teste |
| `scripts/verify_package.py` | Validação da integridade deste pacote; não testa o aplicativo futuro |
| `MANIFEST.json` | Inventário e hashes dos arquivos entregues |

## Condições importantes

- A interface deve mostrar separadamente: universo cadastrado, ativos com preços, ativos com notícias, ativos analisados e lacunas. O objetivo de varrer toda a B3 não significa que os fornecedores gratuitos garantem cobertura integral.
- RSS raramente entrega três meses completos. GDELT ajuda a descobrir links, mas não garante um arquivo completo nem libera o texto de matérias pagas.
- A IA formula hipóteses fundamentadas e pode concluir “dados insuficientes”. O sistema não promete retornos nem transforma pontuação em probabilidade de lucro.
- O agendamento funciona enquanto o computador e o processo estiverem ativos. Após interrupções, retoma os trabalhos pendentes sem duplicar registros.
- As referências foram estudadas e selecionadas. Arquivos originais de skills Figma não estão embutidos; os workflows próprios permitem implementar o design sem depender deles. Veja `docs/07-skills-integracoes.md`.

Os documentos são instruções complementares. Em conflito, valem: restrições explícitas do usuário → `AGENTS.md` → contratos em `schemas/` e configurações → documentos → referências externas. Resolva divergências técnicas pequenas, registre a decisão e continue; não transforme parâmetros opcionais em perguntas bloqueantes.
