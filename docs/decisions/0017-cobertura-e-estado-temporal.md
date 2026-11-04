# ADR 0017 — Cobertura do universo e estado temporal da coleta

- Data: 2026-11-04
- Estado: aceito

## Contexto

A interface precisa distinguir disponibilidade de registros, completude de uma
janela e validade de cada indicador. Os contadores globais existentes incluem
ativos fora do universo elegível; dividi-los pelo total de ações e units elegíveis
produziria uma proporção com populações diferentes. Além disso, a agenda tratava
espera pelo fechamento, dia fechado e ausência de calendário como a mesma falha.

Uma captura inteira pode não conter a primeira sessão dos três meses e ainda
sustentar indicadores de períodos menores. R63 exige 64 observações comparáveis
para 63 intervalos; não é o retorno de três meses de calendário.

## Decisão

1. Preservar os contadores globais existentes por compatibilidade e adicionar
   contadores de ativos elegíveis com captura de preços, preços legados, vínculo
   de notícia e registro de fundamentos. Todos usam `assets.eligible=1`, assim
   como o denominador `eligible_assets`, e contam cada ativo uma vez.
2. Ler contadores e denominador na mesma transação de leitura. Uma atualização
   concorrente do catálogo não pode aparecer em apenas uma parte da proporção.
3. Separar capturas e preços legados, sem somar populações sobrepostas. Presença
   não certifica recência, ajuste, completude, relação causal ou qualidade.
   Armazenamento opcional ausente produz `null`; tabela existente vazia produz 0.
4. Compartilhar entre agenda e coleta diária a decisão baseada no calendário
   conhecido no corte. A sessão só fica elegível para coleta depois do limite
   de fechamento/correção, com comparação estrita. Espera, dia fechado e bloqueio
   têm motivos distintos. A decisão não fabrica uma coleta bem-sucedida nem
   modifica o relógio de verificação documental.
5. Projetar a cobertura da janela da captura atual separadamente das métricas:
   sessões esperadas, observadas e ausentes, período e motivo. Calendário
   insuficiente não permite afirmar completude. O frontend recebe essa projeção;
   não reconstrói regras de calendário pelo relógio do navegador.
6. Preservar fórmulas, capturas, bundles, análises e hashes históricos. Não combinar
   capturas para preencher lacunas, nem bloquear indicadores curtos válidos por
   uma ausência anterior à amostra que eles exigem.

## Justificativa

Campos aditivos corrigem a apresentação sem alterar o significado dos campos
consumidos por clientes existentes. A separação entre cobertura e cálculo evita
apresentar um histórico parcial como completo e evita descartar informação válida.
Uma transação curta em SQLite fornece consistência com baixo custo operacional.

## Alternativas consideradas

- Reutilizar os totais globais na proporção: menor alteração, mas denominador
  incompatível com o numerador.
- Substituir os campos existentes: simplifica o contrato, mas altera clientes e
  filtros já validados.
- Exigir três meses completos para qualquer indicador: conservador em aparência,
  porém invalida amostras menores que já atendem às respectivas regras.
- Preencher a janela com preços de outras capturas: aumenta cobertura aparente,
  mas rompe a política de captura inteira e a rastreabilidade dos ajustes.
- Calcular horários e cobertura no frontend: duplica regras temporais e depende
  do relógio do dispositivo.

## Consequências positivas

- Proporções coerentes, estados operacionais explicáveis e lacunas visíveis.
- Compatibilidade dos contratos anteriores e preservação do histórico financeiro.
- Sem dependências, serviços, chamadas de IA ou migrações de dados adicionais.
- Testes isolados verificam concorrência, fronteiras temporais e ausência de dados.

## Consequências negativas e limites

- O contrato passa a expor contadores globais e elegíveis; rótulos devem distingui-los.
- Contagens de presença não garantem cobertura financeira suficiente.
- A transação de leitura mantém um snapshot por um intervalo curto; não deve
  incluir rede, coleta ou processamento prolongado.
- Calendário documental e disponibilidade do provedor continuam sendo limitações
  independentes; a projeção não homologa dados nem qualidade preditiva.
