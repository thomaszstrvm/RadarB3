import { afterEach, beforeEach, expect, it, vi } from 'vitest';
// Declared development dependency; the production bundle never imports this test.
import { JSDOM } from 'jsdom';
import { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import type { AnalysisJob } from './types';

let dom: any, root: Root, container: HTMLDivElement;
let status: string, detailReads: number, resolveDetail: (response: Response) => void;
let cacheReused: boolean, trackingFailures: number;
let calls: string[];
let AnalysisTracking: typeof import('./AnalysisTracking').AnalysisTracking;
let useData: typeof import('./api').useData;
const path = '/assets/qa%3ADEMO3';
const response = (value: unknown) => new Response(JSON.stringify(value), {status: 200});

beforeEach(async () => {
  vi.resetModules();
  dom = new JSDOM('<!doctype html><html><body></body></html>', {url: 'https://qa.invalid/', pretendToBeVisual: true});
  for (const name of ['window', 'document', 'HTMLElement', 'Node', 'navigator']) vi.stubGlobal(name, dom.window[name]);
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  status = 'running'; detailReads = 0; calls = []; cacheReused = false; trackingFailures = 0;
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method && init.method !== 'GET') throw Error('Mutations forbidden');
    calls.push(url);
    if (url === '/api/v1' + path + '/analysis-job') {
      if (trackingFailures > 0) { trackingFailures--; throw Error('Consulta sintética indisponível'); }
      const data: AnalysisJob = {asset_id: 'qa:DEMO3', as_of: '2026-10-29T12:00:00Z', queue_position: null,
        queue_position_reason: null, result_analysis: status === 'succeeded'
          ? {analysis_id: 'new-validated', status: 'inconclusive', as_of: '2026-10-29T11:00:00Z'} : null,
        job: {id: 'qa-job', kind: 'analyse', status, created_at: '2026-10-29T12:00:00Z', updated_at: status,
          ...(status === 'succeeded' ? {result: {analysis_id: 'new-validated', cache_reused: cacheReused, calls: cacheReused ? 0 : 1}} : {})}};
      return response(data);
    }
    if (url === '/api/v1' + path) {
      detailReads++;
      return detailReads === 1 ? response({version: 'anterior validada', analysis_id: 'previous-validated', as_of: '2026-10-28T12:00:00Z'}) : new Promise<Response>(resolve => {resolveDetail = resolve;});
    }
    throw Error('Unexpected request blocked: ' + url);
  }));
  ({AnalysisTracking} = await import('./AnalysisTracking'));
  ({useData} = await import('./api'));
  vi.useFakeTimers();
  container = document.createElement('div'); document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  await vi.advanceTimersByTimeAsync(0);
  expect(vi.getTimerCount()).toBe(0);
  vi.useRealTimers(); dom.window.close(); vi.unstubAllGlobals();
});

// Real React state/effects, production useData/transport/poller/component; only HTTP is synthetic.
function DetailHost() {
  const [detailRevision, setDetailRevision] = useState(0);
  const detail = useData<{version: string; analysis_id?: string; as_of?: string}>(path, detailRevision);
  return <><output data-testid="detail">{detail.data?.version}</output>
    <output data-testid="error">{detail.error}</output>
    <AnalysisTracking assetId="qa:DEMO3" busy={false} revision={0}
      onResult={() => setDetailRevision(value => value + 1)} openJobs={() => {}}
      currentAnalysisId={detail.data?.analysis_id} currentAnalysisAt={detail.data?.as_of}
      resultLoading={detail.loading} resultError={!!detail.error}/></>;
}

const click = (text: string) => {
  const target = [...container.querySelectorAll('button')].find(item => item.textContent === text);
  expect(target, text).toBeDefined();
  target!.click();
};

it.each(['paused', 'failed'])('React DOM refreshes and renders detail after running → %s without resubmission', async terminal => {
  await act(async () => root.render(<DetailHost/>));
  expect(container.textContent).toContain('Análise em andamento.');
  expect(container.querySelector('[data-testid="detail"]')?.textContent).toBe('anterior validada');
  expect(detailReads).toBe(1);
  status = terminal;
  await act(async () => {await vi.advanceTimersByTimeAsync(5000);});
  expect(container.textContent).toContain(terminal === 'paused' ? 'Análise pausada.' : 'Esta solicitação falhou.');
  expect(detailReads).toBe(2);
  expect(container.querySelector('[data-testid="detail"]')?.textContent).toBe('anterior validada');
  await act(async () => {resolveDetail(response({version: 'detalhe atualizado ' + terminal}));});
  expect(container.querySelector('[data-testid="detail"]')?.textContent).toBe('detalhe atualizado ' + terminal);
  const count = calls.length;
  await act(async () => {await vi.advanceTimersByTimeAsync(60_000);});
  expect(detailReads).toBe(2); expect(calls).toHaveLength(count);
  expect(calls.every(url => url === '/api/v1' + path || url === '/api/v1' + path + '/analysis-job')).toBe(true);
});

it.each(['paused', 'failed'])('keeps previous validated detail when the terminal refresh fails (%s)', async terminal => {
  await act(async () => root.render(<DetailHost/>));
  status = terminal;
  await act(async () => {await vi.advanceTimersByTimeAsync(5000);});
  await act(async () => {resolveDetail(new Response(JSON.stringify({detail: 'Falha sintética'}), {status: 503}));});
  expect(container.querySelector('[data-testid="detail"]')?.textContent).toBe('anterior validada');
  expect(container.querySelector('[data-testid="error"]')?.textContent).toBe('Falha sintética');
  await act(async () => {await vi.advanceTimersByTimeAsync(60_000);});
  expect(detailReads).toBe(2);
});

it.each([false, true])('polling success refreshes the visible result once, including cache reuse=%s', async reused => {
  cacheReused = reused;
  await act(async () => root.render(<DetailHost/>));
  status = 'succeeded';
  await act(async () => {await vi.advanceTimersByTimeAsync(5000);});
  expect(detailReads).toBe(2);
  expect(container.textContent).toContain('Atualizando o resultado exibido');
  expect(container.textContent).toContain('previous-validated');
  expect(container.textContent?.includes('Nenhuma nova chamada de IA.')).toBe(reused);
  await act(async () => {resolveDetail(response({version: 'resultado inconclusivo atualizado', analysis_id: 'new-validated', as_of: '2026-10-29T11:00:00Z'}));});
  expect(container.querySelector('[data-testid="detail"]')?.textContent).toBe('resultado inconclusivo atualizado');
  expect(container.textContent).not.toContain('previous-validated');
  expect(container.textContent).not.toContain('Atualizando o resultado exibido');
  await act(async () => {click('Consultar acompanhamento'); click('Consultar acompanhamento');});
  await act(async () => {await vi.advanceTimersByTimeAsync(60_000);});
  expect(detailReads).toBe(2);
  expect(calls.filter(url => url.endsWith('/analysis-job'))).toHaveLength(3);
});

it('failed result refresh preserves the previous analysis and recovers with read-only retry', async () => {
  await act(async () => root.render(<DetailHost/>));
  status = 'succeeded';
  await act(async () => {await vi.advanceTimersByTimeAsync(5000);});
  await act(async () => {resolveDetail(new Response(JSON.stringify({detail: 'Falha sintética'}), {status: 503}));});
  expect(container.textContent).toContain('A versão anterior foi preservada');
  expect(container.textContent).toContain('previous-validated');
  await act(async () => {click('Recarregar resultado');});
  expect(detailReads).toBe(3);
  expect(container.querySelector('[data-testid="detail"]')?.textContent).toBe('anterior validada');
  await act(async () => {resolveDetail(response({version: 'resultado recuperado', analysis_id: 'new-validated', as_of: '2026-10-29T11:00:00Z'}));});
  expect(container.textContent).not.toContain('previous-validated');
  expect(container.textContent).not.toContain('A versão anterior foi preservada');
  expect(container.querySelector('[data-testid="detail"]')?.textContent).toBe('resultado recuperado');
});

it('query failures preserve the prior analysis and repeated resume clicks recover without resubmission', async () => {
  await act(async () => root.render(<DetailHost/>));
  trackingFailures = 3;
  await act(async () => {await vi.advanceTimersByTimeAsync(15_000);});
  expect(container.textContent).toContain('três falhas consecutivas');
  expect(container.textContent).toContain('previous-validated');
  expect(detailReads).toBe(1);
  status = 'succeeded';
  await act(async () => {click('Retomar acompanhamento'); click('Retomar acompanhamento');});
  expect(container.textContent).not.toContain('Consulta indisponível');
  expect(container.textContent).toContain('Atualizando o resultado exibido');
  expect(detailReads).toBe(2);
  expect(calls.filter(url => url.endsWith('/analysis-job'))).toHaveLength(5);
  await act(async () => {resolveDetail(response({version: 'resultado após retomada', analysis_id: 'new-validated'}));});
  expect(container.querySelector('[data-testid="detail"]')?.textContent).toBe('resultado após retomada');
  expect(container.textContent).not.toContain('previous-validated');
});
