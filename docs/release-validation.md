# Validação protegida de release

Executar com o Python do ambiente existente. Não instala dependências, inicia API, worker operacional, coleta ou IA real.

```powershell
& '<projeto>\.venv\Scripts\python.exe' -B '<candidato>\scripts\validate_release.py' --mode operational --output-dir '<pasta-existente>\resultado-inédito'
& '<projeto>\.venv\Scripts\python.exe' -B '<candidato>\scripts\validate_release.py' --mode full --output-dir '<pasta-existente>\outro-resultado-inédito'
```

O diretório de saída é obrigatório e precisa ser novo, mesmo se uma pasta anterior estiver vazia. O pai precisa existir. O runner nunca remove ou reutiliza resultados anteriores. `full` seleciona a suíte `tests`; `operational` seleciona uma lista fixa de testes de diagnóstico, backup, recuperação Windows, configuração e da própria fronteira, quando presentes. A lista efetiva consta no relatório. Não há argumento para executar scripts/testes arbitrários.

O cwd e imports são derivados do local do runner. `PYTEST_ADDOPTS`, `PYTEST_PLUGINS` e descoberta automática de plugins externos ficam desativados. A variável de execução `analysis-enabled` vale apenas no processo de teste para os cenários simulados; não modifica configurações do produto. Escrita de bytecode fica desativada.

## Proteções e limites

- Audit hook restringe escrita de arquivos e acesso SQLite ao output-dir. Banco em memória é permitido. Temporários, log, JUnit e JSON ficam na saída nova. O runner não importa nem instancia Store.
- Processos externos são limitados aos caminhos resolvidos de `sys.executable` e `sys._base_executable`. Shell, Codex e mecanismos alternativos comuns de lançamento são bloqueados. A fixture existente também proíbe Codex real.
- Conexões, envio UDP e resolução DNS externos são bloqueados; loopback permanece permitido para clientes de teste. Isto não autoriza iniciar serviços reais.
- **Não é sandbox universal de sistema operacional.** Bibliotecas nativas e subprocessos Python podem ultrapassar audit hooks. Os filhos não herdam o hook; rodar somente testes revisados. Os subprocessos existentes revisados fazem importações locais em bancos temporários ou criam a árvore Python benigna do teste Windows. Não usar para código não confiável.
- O teste nativo Windows cria seu próprio holder, filho e neto inofensivos. Encerra somente o handle de processo criado e confirma recuperação; não procura nem mata serviços existentes. A instância Worker usada no teste é uma fixture sobre SQLite temporário, não um worker operacional.
- O contador `blocked` registra tentativas recusadas, inclusive cenários negativos esperados; não significa que a ação ocorreu. Resultado de teste e restrições precisam ser avaliados juntos.

## Evidência inicial de desenvolvimento, 11/11/2026

Na cópia `docs/qa-20261111/operations-cycle/candidate`, `release-operational-02` passou **105 testes**, incluindo a recuperação nativa Windows, em 25,36 s. `validation.json` registrou um lançamento Python permitido e 1.820 conexões SQLite sob a fronteira. Houve um aviso de depreciação Starlette/httpx; nenhuma instalação foi feita.

A primeira saída `release-operational-01` foi preservada: 104 testes passaram e o teste Windows foi recusado pelo guard antes de iniciar o processo. Corrigiu-se a interpretação do evento de subprocesso Windows (`executable=None` e linha de comando com aspas), com regressão nos testes próprios, antes da segunda execução.

Esses resultados são intermediários: diagnóstico e backup estavam em desenvolvimento paralelo. A validação final do candidato integrado exige nova saída inédita. Artefatos de cada execução: `junit.xml`, `pytest.log`, `validation.json` e temporários preservados. Não limpar uma execução para reaproveitar seu nome.

Execução completa intermediária `release-full-01`: **1.447 testes passaram**, um aviso, 221,11 s. Essa execução começou antes de encerrar a integração paralela; não substitui o aceite do conjunto final.
