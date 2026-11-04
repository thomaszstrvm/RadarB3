// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MarketCoverage, operationHeadline } from './MarketCoverage';
import { SourceDiagnostics } from './Sources';
import { CurrentPriceSnapshot } from './CurrentPriceSnapshot';
import type { Operations, Overview, PriceSnapshot } from './types';

let container: HTMLDivElement, root: Root;
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT',true);
  vi.stubGlobal('fetch',vi.fn(() => {throw Error('Network forbidden');}));
  container = document.createElement('div'); document.body.append(container); root = createRoot(container);
});
afterEach(async () => {await act(async () => root.unmount()); container.remove(); expect(fetch).not.toHaveBeenCalled(); vi.unstubAllGlobals();});
const operations: Operations = {execution_mode:'collection-only',worker:{status:'active',heartbeat_at:null,expires_at:null,execution_mode:'collection-only'},ai:{enabled:false},collection:{sources:[],latest_news_fetch:null,latest_price_fetch:null,latest_macro_fetch:null},latest_analysis_snapshot:null,queried_at:'2026-11-04T12:00:00Z'};
const overview: Overview = {as_of:'2026-11-04T12:00:00Z',window_start:'2026-08-04',mode:'demo',ai:{enabled:false},last_run:null,counts:{assets:20,priced:99,articles:0,events:0,analysed:0,pending:0},coverage:{catalog_assets:30,eligible_assets:20,unclassified_assets:10,eligible_assets_with_price_vintages:3,eligible_assets_with_legacy_prices:2,eligible_assets_with_news:0,eligible_assets_with_fundamentals:null}};

it('keeps mode and eligible coverage visible outside closed details, without using legacy global totals', async () => {
  await act(async () => root.render(<MarketCoverage overview={overview} operations={operations} error=""><p>Detalhes</p></MarketCoverage>));
  const permanent = container.querySelector('.market-status')!;
  expect(permanent.closest('details')).toBeNull();
  expect(permanent.textContent).toContain('IA desativada neste modo');
  expect(permanent.textContent).toContain('3 de 20');
  expect(permanent.textContent).toContain('2 de 20');
  expect(permanent.textContent).toContain('0 de 20');
  expect(permanent.textContent).toContain('Indisponível');
  expect(permanent.textContent).not.toContain('99 de 20');
  expect(permanent.textContent).toContain('podem se sobrepor');
  expect(permanent.textContent).toContain('históricas');
  expect(container.querySelector('details')?.open).toBe(false);
});
it('does not substitute zero for absent fields and labels stale overview data', async () => {
  await act(async () => root.render(<MarketCoverage overview={{...overview,coverage:undefined}} overviewError="offline" operations={operations} error=""><span/></MarketCoverage>));
  expect(container.querySelectorAll('.coverage-presence dd')).toHaveLength(4);
  for (const value of container.querySelectorAll('.coverage-presence dd')) expect(value.textContent).toBe('Indisponível');
  expect(container.textContent).toContain('Cobertura sem atualização confirmada');
});
it.each(['absent','stale'] as const)('does not claim active collection with %s worker', status => {
  const headline=operationHeadline({...operations,worker:{...operations.worker,status}});
  expect(headline).not.toContain('Executor ativo');
  expect(headline).not.toContain('Coleta ativa');
});
it('does not assert disabled inference for mismatch or stale API response', () => {
  expect(operationHeadline({...operations,mode_mismatch:true})).toContain('modos diferentes');
  expect(operationHeadline({...operations,mode_mismatch:true})).not.toContain('IA desativada');
  expect(operationHeadline(operations,'offline')).toContain('sem confirmação');
  expect(operationHeadline(undefined)).toContain('Consultando');
  expect(operationHeadline({...operations,execution_mode:'manual-analysis'})).toContain('Análise manual');
});
it('keeps RSS caveat beside available status and human CNN explanation in both layouts', async () => {
  await act(async () => root.render(<SourceDiagnostics items={[
    {id:'fixture-rss',name:'Fonte de teste',status:'available',count:0,details:{historical_coverage:'feed_window_only'}},
    {id:'fixture-cnn',name:'CNN de teste',status:'blocked',reason:'rss_reuse_terms_review_required'},
  ]}/>));
  for (const selector of ['.source-desktop','.source-mobile']) {
    const layout=container.querySelector(selector)!;
    expect(layout.textContent).toContain('RSS recente; cobertura de três meses não garantida.');
    expect(layout.textContent).toContain('Disponível');
    expect(layout.textContent).toContain('Coleta suspensa: termos de reutilização do RSS aguardam revisão.');
    expect(layout.textContent).toContain('Intervalo não informado');
    for(const caveat of layout.querySelectorAll('.source-limitation')) expect(caveat.closest('details')).toBeNull();
    expect(layout.querySelectorAll('details')[1].textContent).toContain('rss_reuse_terms_review_required');
  }
});

const snapshot: PriceSnapshot = {vintage_id:'fixture',fetched_at:'2026-11-04T12:00:00Z',latest_capture_at:'2026-11-04T12:00:00Z',available_at:'2026-11-04T12:00:00Z',response_sha256:'fixture',policy_sha256:'fixture',window_coverage:{requested_start:'2026-08-04',requested_end:'2026-11-04',expected_last_session:'2026-11-03',observed_start:'2026-08-05',observed_end:'2026-11-03',expected_sessions:63,observed_sessions:62,missing_sessions:['2026-08-04'],status:'partial',reason:'missing_market_sessions'}};
it('shows incomplete window while retaining a valid short metric', async () => {
  await act(async () => root.render(<CurrentPriceSnapshot snapshot={snapshot} metrics={[{label:'Variação curta de teste',value:.02,unit:'fraction'}]}/>));
  expect(container.textContent).toContain('Janela incompleta');
  expect(container.textContent).toContain('Sessões ausentes: 04 de ago. de 2026');
  expect(container.textContent).toContain('Variação curta de teste');
  expect(container.textContent).toContain('63 intervalos e 64 observações');
  expect(container.textContent).toContain('Faltam sessões esperadas nesta captura.');
});
it.each(['unknown','complete'] as const)('renders backend window status %s without inferring by current clock', async status => {
  await act(async () => root.render(<CurrentPriceSnapshot snapshot={{...snapshot,window_coverage:{...snapshot.window_coverage!,status,expected_sessions:null,missing_sessions:[],reason:null}}} metrics={[]}/>));
  expect(container.textContent).toContain(status==='unknown'?'Cobertura da janela não confirmada':'Sessões esperadas presentes');
  expect(container.textContent).toContain('Esperadas: Indisponível');
});

it('shows backend waiting state and next attempt in both source layouts without a failure label', async () => {
  await act(async () => root.render(<SourceDiagnostics items={[{id:'market_calendar',name:'Calendário de teste',status:'partial',
    reason:'Aguardando o fechamento da sessão documentada.',error:null,next_attempt_at:'2026-11-04T22:00:01Z',
    details:{automatic_prices:'waiting_for_close',reason_code:'correction_period_not_ended',session_date:'2026-11-04',closes_at:'2026-11-04T22:00:00Z'}}]}/>));
  for (const selector of ['.source-desktop','.source-mobile']) {
    const message=container.querySelector(selector+' .source-limitation')!;
    expect(message.textContent).toContain('Aguardando o fechamento da sessão documentada.');
    expect(message.textContent).toContain('Aguardar até');
    expect(message.textContent).not.toContain('Falha');
    expect(message.closest('details')).toBeNull();
  }
});
