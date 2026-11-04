# ADR — Filme high-energy independente em Remotion

Data: 03/11/2026. Status: adotada para este filme.

## Contexto

O usuário solicitou outro vídeo do Radar B3 em Remotion e a skill
`Barty-Bart/motion-graphics/explainer-high-energy`, e autorizou instalar requisitos.
Os filmes anteriores usam outro motor e devem ser preservados. A skill recomenda
um renderer HTML/Playwright; o pedido expresso de Remotion orienta a implementação.

## Decisão e justificativa

Criar `presentation-remotion-high-energy/` com dependências e lockfile próprios,
Remotion 4.0.540, composição de 15 s a 60 fps, cenas React/SVG e áudio local.
Aplicar direção, ritmo e transições da skill sem executar seu renderer alternativo.
Relógio visual derivado do frame; música original em WAV e fontes abertas locais.
Preservar o aplicativo, bases, quotas, renders e fontes dos filmes anteriores.

## Alternativas

- Reutilizar HyperFrames: menor preparação, mas contraria a escolha de Remotion.
- HTML em iframe: preservaria o contrato da skill, mas dificultaria editar cenas.
- Incorporar dependências no frontend operacional: misturaria entregáveis e builds.

## Consequências positivas

Textos/cenas editáveis no Studio; busca de um frame independente da reprodução;
nenhum acesso à base operacional ou IA financeira; composição sem recursos remotos.
As formas de radar são conceituais; o cartão é explicitamente ilustrativo.

## Consequências negativas

Mais uma pasta de dependências. Motion blur aumenta custo de render local.
O Studio 4.0.540 não oferece host de loopback como opção pública: o launcher ajusta
o objeto de configuração interno por processo, sem editar node_modules. A versão
fica fixada e essa adaptação precisa ser revista em eventual atualização.
Auditoria inicial: um advisory moderado de minimatch na cadeia dev de ESLint,
reportado em 11 entradas; sem correção compatível comprovada. Não forçar major.

## Revisão de 04/11/2026

Por solicitação do usuário, a v2 passa a 18,75 s (40 tempos, 1.125 quadros),
troca as frases de apoio por Instrument Sans Medium e acrescenta uma cena de
VALE3. A arquitetura permanece a mesma. O SVG usa 22 fechamentos ajustados da
resposta brapi preservada no bundle histórico, conferidos por script contra a
origem; não há consulta de mercado durante o render. A cena informa período e
fonte. Isso permite reprodução determinística, com a consequência de exigir
atualização e verificação explícita para outro período. A v1 e seus fontes foram
preservados. Não foi necessária biblioteca adicional de gráficos.
