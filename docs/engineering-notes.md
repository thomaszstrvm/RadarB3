# Como o Radar B3 funciona

O worker busca dados permitidos, normaliza e grava revisões. A API lê o banco e enfileira comandos; abrir uma tela não busca notícia nem chama IA. O React permite comparar ativos e abrir as evidências. Essa separação mantém a navegação disponível durante uma coleta lenta.

SQLite é o armazenamento local. WAL permite que o leitor continue enquanto uma transação curta grava. O lease é uma concessão temporária de execução: o worker precisa renová-la para continuar dono da fila. Após interrupção, outro processo só recupera trabalho quando a concessão vence. Jobs são repetíveis, e chaves únicas evitam duplicar os efeitos já confirmados.

Um bundle é um retrato fechado de evidências: cada registro tem ID, origem, hash e relógios separados. A análise deve apontar para o bundle que recebeu, não para o mais novo. Isso evita explicar uma conclusão antiga com dados que só chegaram depois.

## Uma função central: três meses de calendário

Em `backend/timeutil.py`, `news_window` recebe um instante com fuso e devolve início/fim em UTC:

```python
def news_window(as_of):
    end = instant(as_of)
    local = end.astimezone(SAO_PAULO)
    month_index = local.year * 12 + local.month - 1 - 3
    year, month = divmod(month_index, 12)
    month += 1
    day = min(local.day, calendar.monthrange(year, month)[1])
    start = datetime.combine(date(year, month, day), time.min, SAO_PAULO)
    return iso(start), iso(end)
```

1. `instant` rejeita horário sem fuso e normaliza a entrada. Sem isso, duas máquinas poderiam interpretar a mesma string como instantes diferentes.
2. `astimezone` encontra a data local de São Paulo; perto da meia-noite, ela pode diferir da data UTC.
3. O índice linear transforma ano/mês em uma sequência de meses e subtrai três. Não há aproximação por 90 dias.
4. `divmod` separa novamente ano e mês, inclusive atravessando dezembro/janeiro.
5. `month += 1` volta da numeração interna 0–11 para os meses 1–12 de `datetime`.
6. `min` limita o dia ao último existente no destino. De 31/05, três meses antes é 28/02 ou 29/02, não uma data inválida.
7. `datetime.combine` fixa o início em meia-noite local, com seu fuso explícito.
8. `iso` serializa ambos em UTC para persistência e comparação. O retorno é uma tupla `(início, fim)`.

A função pode lançar `ValueError` para horário inválido ou sem fuso. Seu chamador deve congelar o mesmo `as_of` por execução; recalcular o relógio em cada página de um backfill poderia misturar janelas.

## Limites do cálculo

Subtrair meses não determina pregões. Um calendário de negociação precisa de feriados e horários oficiais verificados. Por isso a agenda automática de preços aguarda esse cadastro, e os testes de calendário civil não são apresentados como homologação do calendário B3.

## Exercício

Adicione um teste para `as_of` em 31 de maio de um ano bissexto, próximo à meia-noite UTC. Antes de executar, escreva qual deve ser a data inicial em São Paulo e explique por que subtrair 90 dias produziria outro resultado. Não altere a regra para fazer o teste passar.
