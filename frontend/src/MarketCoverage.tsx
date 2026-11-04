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

export function operationHeadline(operations?: Operations, error = ''): string {
  if (error) return 'Estado operacional sem confirmação · última consulta não renovada';
  if (!operations) return 'Consultando modo operacional…';
  if (operations.mode_mismatch) return 'API e executor em modos diferentes · confira o diagnóstico';
  const worker = operations.worker.status === 'active' ? 'Executor ativo'
    : operations.worker.status === 'stale' ? 'Executor sem confirmação' : 'Executor ausente';
  if (operations.execution_mode === 'collection-only') return `Coleta sem IA · IA desativada neste modo · ${worker}`;
  if (operations.execution_mode === 'manual-analysis') return `Análise manual · ${worker}`;
  return `Modo com IA · ${operations.ai.enabled ? 'Inferência disponível' : 'Inferência indisponível'} · ${worker}`;
}

export function MarketCoverage({overview, overviewError = '', operations, error, children}: {
  overview?: Overview; overviewError?: string; operations?: Operations; error: string; children: ReactNode;
}) {
  const received = operations?.collection.latest_price_data_at;
  const coverage = overview?.coverage;
  const presence = (count: number | null | undefined) => count == null || coverage?.eligible_assets == null
    ? 'Indisponível' : `${number(count)} de ${number(coverage.eligible_assets)}`;
  return <section aria-label="Modo e cobertura do Mercado">
    <div className="market-status">
      <p><strong>{operationHeadline(operations, error)}</strong></p>
      {operations?.execution_mode === 'collection-only' && <p className="meta">Análises existentes são históricas; uma coleta não as recalcula.</p>}
      {overviewError && <p className="notice">Cobertura sem atualização confirmada. Valores da última consulta recebida.</p>}
      <dl className="coverage-presence">
        <div><dt>Com captura preservada de preços</dt><dd>{presence(coverage?.eligible_assets_with_price_vintages)}</dd></div>
        <div><dt>Com preços legados</dt><dd>{presence(coverage?.eligible_assets_with_legacy_prices)}</dd></div>
        <div><dt>Com vínculo de notícia</dt><dd>{presence(coverage?.eligible_assets_with_news)}</dd></div>
        <div><dt>Com registro de fundamentos</dt><dd>{presence(coverage?.eligible_assets_with_fundamentals)}</dd></div>
      </dl>
      <p className="meta">Presença entre ações e units elegíveis; não comprova qualidade ou cobertura do período. Preços legados e capturas podem se sobrepor. Vínculos de notícia podem ser candidatos.</p>
      <p className="meta">{number(coverage?.unclassified_assets)} ativos ainda sem classificação.</p>
      <p className="meta">{received ? 'Último dado de preços recebido em ' + date(received, true) : operations ? 'Sem dado de preços recebido' : 'Consultando atualização…'}</p>
    </div>
    <details className="market-context">
    <summary><strong>Ver cobertura</strong><span className="market-context-clock">Contagens e diagnóstico operacional</span></summary>
    {children}
    <OperationalStatus data={operations} error={error}/>
  </details></section>;
}
