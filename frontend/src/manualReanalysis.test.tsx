import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { JSDOM } from 'jsdom';
import { act, useEffect, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import type { AnalysisJob } from './types';

let dom: JSDOM, root: Root, container: HTMLDivElement;
let AnalysisTracking: typeof import('./AnalysisTracking').AnalysisTracking;
let useData: typeof import('./api').useData;
let snapshot: AnalysisJob, detailReads: number, hostMounts: number;
let settlePost: (response: Response) => void, settleDetail: (response: Response) => void;
let writes: {url: string; body: unknown; key: string | null}[], reads: string[];
const assetId = 'qa:DEMO3';
const path = '/assets/qa%3ADEMO3';
const cutoff = '2026-11-01T04:06:00Z';
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), {status});
const button = (text: string) => {
  const found = [...container.querySelectorAll('button')].find(item => item.textContent === text);
  expect(found, text).toBeDefined();
  return found!;
};

beforeEach(async () => {
  vi.resetModules();
  dom = new JSDOM('<!doctype html><html><body></body></html>', {url: 'https://qa.invalid/', pretendToBeVisual: true});
  for (const name of ['window', 'document', 'HTMLElement', 'Node', 'navigator']) vi.stubGlobal(name, (dom.window as any)[name]);
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  vi.useFakeTimers();
  dom.window.setTimeout = globalThis.setTimeout as any;
  dom.window.clearTimeout = globalThis.clearTimeout as any;
  detailReads = 0; hostMounts = 0; writes = []; reads = [];
  snapshot = {asset_id: assetId, as_of: '2026-11-01T16:00:00Z', job: null, manual_scope: 'outside',
    queue_position: null, queue_position_reason: 'no_job', result_analysis: null};
  // Every response is local and synthetic. No request can reach a real service.
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method && init.method !== 'GET') {
      if (init.method !== 'POST' || url !== '/api/v1' + path + '/analyse') throw Error('Unexpected mutation blocked: ' + url);
      writes.push({url, body: JSON.parse(String(init.body)), key: new Headers(init.headers).get('Idempotency-Key')});
      snapshot = {...snapshot, manual_scope: 'current', queue_position: 1, queue_position_reason: null,
        job: {id: 'synthetic-reanalysis-job', kind: 'analyse', status: 'queued', created_at: '2026-11-01T16:01:00Z'}};
      return new Promise<Response>(resolve => {settlePost = resolve;});
    }
    reads.push(url);
    if (url === '/api/v1/session') return response({token: 'SYNTHETIC_REANALYSIS_TOKEN'});
    if (url === '/api/v1' + path + '/analysis-job') return response(snapshot);
    if (url === '/api/v1' + path) {
      detailReads++;
      return detailReads === 1
        ? response({analysis_id: 'previous-validated', as_of: cutoff, synthesis: 'Hipótese anterior inconclusiva'})
        : new Promise<Response>(resolve => {settleDetail = resolve;});
    }
    throw Error('Unexpected read blocked: ' + url);
  }));
  ({AnalysisTracking} = await import('./AnalysisTracking'));
  ({useData} = await import('./api'));
  container = document.createElement('div'); document.body.append(container); root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  await vi.advanceTimersByTimeAsync(0);
  expect(vi.getTimerCount()).toBe(0);
  vi.useRealTimers(); dom.window.close(); vi.unstubAllGlobals();
});

function DetailHost() {
  const [revision, setRevision] = useState(0);
  const detail = useData<{analysis_id: string; as_of: string; synthesis: string}>(path, revision);
  useEffect(() => {hostMounts++;}, []);
  return <><output data-testid="detail">{detail.data?.synthesis}</output>
    <output data-testid="error">{detail.error}</output>
    <AnalysisTracking assetId={assetId} ai={{enabled: true, execution_mode: 'manual-analysis'}} busy={false}
      revision={0} onResult={() => setRevision(value => value + 1)} openJobs={() => {}}
      currentAnalysisId={detail.data?.analysis_id} currentAnalysisAt={detail.data?.as_of}
      resultLoading={detail.loading} resultError={!!detail.error}/></>;
}

it.each(['succeeded', 'failed'] as const)('explicit manual click reaches %s through polling with one POST and no reload', async terminal => {
  await act(async () => root.render(<DetailHost/>));
  const originalOutput = container.querySelector('[data-testid="detail"]');
  expect(originalOutput?.textContent).toBe('Hipótese anterior inconclusiva');
  await act(async () => {button('Reanalisar ativo').click(); button('Reanalisar ativo').click();});
  expect(writes).toEqual([{url: '/api/v1' + path + '/analyse', body: {reanalyze: true}, key: expect.stringMatching(/^radar-[a-zA-Z0-9_-]+$/)}]);
  expect(button('Solicitando análise…').getAttribute('aria-disabled')).toBe('true');
  await act(async () => settlePost(response(snapshot.job, 202)));
  expect(button('Análise solicitada').getAttribute('aria-disabled')).toBe('true');
  await act(async () => {button('Análise solicitada').click(); button('Análise solicitada').click();});
  expect(writes).toHaveLength(1);
  snapshot.job = {...snapshot.job!, status: 'running', updated_at: '2026-11-01T16:01:01Z'};
  await act(async () => {await vi.advanceTimersByTimeAsync(5000);});
  expect(container.textContent).toContain('Análise em andamento.');
  expect(container.textContent).toContain('previous-validated');
  snapshot.job = {...snapshot.job!, status: terminal, updated_at: '2026-11-01T16:02:00Z',
    ...(terminal === 'succeeded' ? {result: {analysis_id: 'new-validated', cache_reused: false, calls: 1}} : {error: 'synthetic_failure'})};
  snapshot.result_analysis = terminal === 'succeeded' ? {analysis_id: 'new-validated', status: 'inconclusive', as_of: cutoff} : null;
  await act(async () => {await vi.advanceTimersByTimeAsync(5000);});
  expect(detailReads).toBe(2);
  expect(container.textContent).toContain('previous-validated');
  if (terminal === 'succeeded') {
    expect(container.textContent).toContain('Atualizando o resultado exibido');
    expect(container.textContent).not.toContain('Nenhuma nova chamada de IA');
    await act(async () => settleDetail(response({analysis_id: 'new-validated', as_of: cutoff, synthesis: 'Nova hipótese inconclusiva'})));
    expect(originalOutput?.textContent).toBe('Nova hipótese inconclusiva');
    expect(container.textContent).toContain('Resultado exibido: new-validated');
    expect(container.textContent).not.toContain('previous-validated');
  } else {
    expect(container.textContent).toContain('Esta solicitação falhou.');
    await act(async () => settleDetail(response({detail: 'Falha sintética na consulta do detalhe'}, 503)));
    expect(originalOutput?.textContent).toBe('Hipótese anterior inconclusiva');
    expect(container.textContent).toContain('Resultado exibido: previous-validated');
    expect(container.textContent).not.toContain('Resultado exibido: new-validated');
    expect(container.querySelector('[data-testid="error"]')?.textContent).toBe('Falha sintética na consulta do detalhe');
  }
  const readsAfterTerminal = reads.length;
  await act(async () => {await vi.advanceTimersByTimeAsync(60_000);});
  expect(reads).toHaveLength(readsAfterTerminal);
  expect(writes).toHaveLength(1);
  expect(detailReads).toBe(2);
  expect(hostMounts).toBe(1);
  expect(container.querySelector('[data-testid="detail"]')).toBe(originalOutput);
  expect(window.location.href).toBe('https://qa.invalid/');
});
