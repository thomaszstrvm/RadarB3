# Validação do pacote entregue

Executada em 26/10/2026. Este registro diz respeito aos materiais de planejamento, não ao funcionamento de um aplicativo ainda não implementado.

- Inventário, tamanhos e hashes conferidos pelo script incluído.
- Onze arquivos JSON, incluindo o manifesto, com sintaxe válida.
- Três schemas estritos e seus três exemplos sintéticos compatíveis, usando o subconjunto de JSON Schema explicitamente suportado pelo verificador.
- Referências de evidências, identidade do ativo, datas, política de abstenção, quantidade de fontes e restrições de IA conferidas no exemplo.
- Vinte e quatro casos de aceite definidos; esses casos ainda serão implementados e executados pelo Codex no aplicativo.
- Tokens revisados em 62 pares de contraste, com mínimo observado de 5,13:1 nos pares avaliados. Isso não substitui uma auditoria do aplicativo final.
- SVG sintaticamente válido e PNG original inspecionado visualmente; dados fictícios sinalizados e nenhuma imagem de terceiros incorporada.
- Revisão independente dos documentos resolveu divergências de corte temporal, referência de métricas, estados de interface, limites de download CVM e nomenclatura de timestamps.

Para reproduzir a verificação desta entrega: `python3 scripts/verify_package.py` na raiz da pasta extraída. O manifesto representa esta versão do pacote; alterações posteriores legítimas exigirão regenerá-lo ou arquivá-lo como registro da entrega original.

Não executado aqui: aplicativo, integrações reais dos oito portais, cobertura histórica integral, preços/fundamentos de todo o universo B3, login/CLI do usuário, análise financeira real ou instalações de skills externas. Esses itens são trabalho de implementação, com critérios explícitos em `docs/06-implementacao-aceite.md`.
