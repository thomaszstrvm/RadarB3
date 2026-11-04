import type { MouseEvent, ReactNode } from 'react';
import { date, number, query } from './format';
import { OperationalStatus } from './OperationalStatus';
import type { Operations, Overview } from './types';

type Destination = Record<string, string>;
type Navigate = (destination: Destination) => void;

export function CoverageStats({data, navigate}: {data?: Overview; navigate: Navigate}) {
  const counts = data?.counts;
  const market = data?.market_counts;

  function metric(caption: string, value: number | null | undefined, description: string, destination?: Destination) {
    const content = <><span>{caption}</span><strong className={value == null ? 'unknown-count' : undefined}>{number(value)}</strong><small>{description}</small></>;
    if (!destination || value == null) return <div>{content}</div>;
    const follow = (event: MouseEvent<HTMLAnchorElement>) => {
      if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      const disclosure = event.currentTarget.closest('details');
      if (disclosure) {
        disclosure.open = false;
        disclosure.querySelector('summary')?.focus();
      }
      navigate(destination);
    };
    return <a className="coverage-link" href={'?' + query(destination)} onClick={follow}>{content}</a>;
  }

  const manual = data?.ai.execution_mode === 'manual-analysis';
  return <section className="stats" aria-label="Cobertura do radar">
    {metric('Ativos com preço', market?.priced ?? counts?.priced, 'Cadastro consultável · último preço disponível',
      market ? {view: 'market', type: 'all', state: 'priced'} : undefined)}
    {metric('Eventos identificados', counts?.events, number(counts?.articles) + ' publicações coletadas', {view: 'events'})}
    {metric('Ativos analisados', market?.analysed ?? counts?.analysed, 'Cadastro consultável · resultados validados',
      market ? {view: 'market', type: 'all', state: 'analysed'} : undefined)}
    {metric(manual ? 'Solicitações pendentes no histórico' : 'Pedidos de análise em aberto', counts?.pending,
      manual ? 'Podem incluir pedidos fora da sessão manual' : 'Inclui fila, execução, pausas e bloqueios')}
  </section>;
}

export function MarketCoverage({operations, error, children}: {operations?: Operations; error: string; children: ReactNode}) {
  const received = operations?.collection.latest_price_data_at;
  const status = error ? 'Diagnóstico indisponível'
    : operations?.mode_mismatch ? 'API e executor em modos diferentes'
    : operations?.worker.status === 'stale' ? 'Coleta sem confirmação' : '';
  return <details className="market-context">
    <summary><strong>Ver cobertura</strong><span className="market-context-clock">
      {status && <b>{status} · </b>}
      {received ? 'Último dado de preços recebido em ' + date(received) : operations ? 'Sem dado de preços recebido' : 'Consultando atualização…'}
    </span></summary>
    {children}
    <OperationalStatus data={operations} error={error}/>
  </details>;
}
