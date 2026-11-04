import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { JSDOM } from 'jsdom';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import type { Asset, AssetDetail, List } from './types';

type PendingRead = {url: URL; signal?: AbortSignal | null; resolve: (response: Response) => void};
let dom: JSDOM, root: Root, container: HTMLDivElement;
let App: typeof import('./App').default;
let delayed: (url: URL) => boolean;
let pending: PendingRead[], forbidden: string[];
const instant = '2026-11-03T18:00:00Z';
const ok = (data: unknown) => new Response(JSON.stringify(data), {status: 200});
const asset = (ticker: string): Asset => ({
  id: 'qa:' + ticker, ticker, name: 'Empresa sintética ' + ticker, type: 'stock',
  price: null, return_21: null, watchlisted: false, event_count: 0,
});
const detail = (ticker: string): AssetDetail => ({
  ...asset(ticker), prices: [], events: [], articles: [], analysis: null, metrics: [], evidence: [],
});
const pageOne = [asset('ALFA3'), asset('BETA4'),
  ...Array.from({length: 18}, (_, i) => asset('QA' + String(i + 1).padStart(2, '0') + '3'))];
const pageTwo = [asset('GAMA3'), asset('DELT4'),
  ...Array.from({length: 18}, (_, i) => asset('QB' + String(i + 1).padStart(2, '0') + '3'))];
const list = (items: Asset[], total = items.length): List<Asset> => ({items, total, as_of: instant});
const params = () => new URLSearchParams(window.location.search);
const rows = () => [...container.querySelectorAll('.asset-link')].map(node => node.textContent);
const heading = () => container.querySelector('#asset-detail-heading')?.textContent;

function button(text: string, scope: ParentNode = container): HTMLButtonElement {
  const matches = [...scope.querySelectorAll('button')].filter(node => node.textContent === text);
  expect(matches, 'Controle único: ' + text).toHaveLength(1);
  return matches[0];
}
async function click(text: string, scope: ParentNode = container) {
  await act(async () => button(text, scope).click());
}
async function open(ticker: string) {
  const control = container.querySelector<HTMLButtonElement>('[aria-label="Investigar ' + ticker + '"]');
  expect(control).not.toBeNull();
  await act(async () => control!.click());
}
async function close() {
  await act(async () => {
    container.querySelector<HTMLButtonElement>('[aria-label="Fechar detalhe do ativo"]')!.click();
    await vi.advanceTimersByTimeAsync(20);
  });
}
async function back() {
  await act(async () => {window.history.back(); await vi.advanceTimersByTimeAsync(20);});
}
async function settle(read: PendingRead, payload: unknown) {
  // Deliberately ignore abort: even an uncooperative transport must not revive old UI.
  await act(async () => read.resolve(ok(payload)));
}
function onlyPending(): PendingRead {
  expect(pending).toHaveLength(1);
  return pending.shift()!;
}

beforeEach(async () => {
  vi.resetModules();
  dom = new JSDOM('<!doctype html><html><body></body></html>', {
    url: 'https://qa.invalid/?view=market', pretendToBeVisual: true,
  });
  for (const name of ['window', 'document', 'HTMLElement', 'Node', 'navigator']) {
    vi.stubGlobal(name, (dom.window as unknown as Record<string, unknown>)[name]);
  }
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  vi.useFakeTimers();
  dom.window.setTimeout = globalThis.setTimeout as typeof dom.window.setTimeout;
  dom.window.clearTimeout = globalThis.clearTimeout as typeof dom.window.clearTimeout;
  dom.window.setInterval = globalThis.setInterval as typeof dom.window.setInterval;
  dom.window.clearInterval = globalThis.clearInterval as typeof dom.window.clearInterval;
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => setTimeout(() => callback(0), 0));
  delayed = () => false;
  pending = []; forbidden = [];
  const deny = (message: string): never => {forbidden.push(message); throw new Error(message);};
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const path = String(input);
    if ((init?.method || 'GET') !== 'GET') deny('Mutação proibida no teste: ' + path);
    if (!path.startsWith('/api/v1/')) deny('Rede externa proibida no teste: ' + path);
    const url = new URL(path, 'https://qa.invalid');
    if (url.pathname === '/api/v1/overview') return Promise.resolve(ok({
      as_of: instant, window_start: '2026-08-03T03:00:00Z', mode: 'demo',
      counts: {assets: 42, priced: 0, articles: 0, events: 0, analysed: 0, pending: 0},
      ai: {enabled: false, execution_mode: 'collection-only'}, last_run: null,
    }));
    if (url.pathname === '/api/v1/operations') return Promise.resolve(ok({
      execution_mode: 'collection-only', ai: {enabled: false, homologated: false},
      worker: {status: 'absent', heartbeat_at: null, expires_at: null, execution_mode: null},
      collection: {sources: [], latest_news_fetch: null, latest_price_fetch: null, latest_macro_fetch: null},
      latest_analysis_snapshot: null, queried_at: instant,
    }));
    if (url.pathname === '/api/v1/assets' || /^\/api\/v1\/assets\/qa%3A[A-Z0-9]+$/.test(url.pathname)) {
      if (delayed(url)) return new Promise<Response>(resolve => pending.push({url, signal: init?.signal, resolve}));
      if (url.pathname !== '/api/v1/assets') return Promise.resolve(ok(detail(decodeURIComponent(url.pathname.split('/').at(-1)!).slice(3))));
      const filtered = url.searchParams.get('quality') === 'low';
      return Promise.resolve(ok(filtered ? list([asset('FILT3')]) : list(url.searchParams.get('page') === '2' ? pageTwo : pageOne, 42)));
    }
    if (/^\/api\/v1\/assets\/qa%3A[A-Z0-9]+\/analysis-job$/.test(url.pathname)) return Promise.resolve(ok({
      asset_id: decodeURIComponent(url.pathname.split('/').at(-2)!), job: null, queue_position: null,
      queue_position_reason: null, result_analysis: null, as_of: instant,
    }));
    return deny('Rota sem fixture proibida no teste: ' + path);
  });
  vi.stubGlobal('fetch', fetchMock);
  dom.window.fetch = fetchMock;
  // App only uses fetch; deny alternative browser networking if a regression adds it.
  const blockedNetwork = class {constructor() {deny('Transporte de rede não autorizado no teste');}};
  vi.stubGlobal('XMLHttpRequest', blockedNetwork);
  vi.stubGlobal('WebSocket', blockedNetwork);
  dom.window.XMLHttpRequest = blockedNetwork as unknown as typeof dom.window.XMLHttpRequest;
  dom.window.WebSocket = blockedNetwork as unknown as typeof dom.window.WebSocket;
  ({default: App} = await import('./App'));
  container = document.createElement('div'); document.body.append(container); root = createRoot(container);
});

afterEach(async () => {
  await act(async () => {root.unmount(); await vi.advanceTimersByTimeAsync(0);});
  const remainingTimers = vi.getTimerCount();
  vi.useRealTimers(); vi.restoreAllMocks(); dom.window.close(); vi.unstubAllGlobals();
  expect(forbidden, 'Nenhuma rota real ou mutação deve ser tentada').toEqual([]);
  expect(remainingTimers, 'A navegação não deve deixar timers ativos').toBe(0);
  expect(pending, 'Todas as respostas controladas devem ser verificadas').toEqual([]);
});

async function mount() {await act(async () => root.render(<App/>));}

it('preserva página e filtros ao abrir, fechar e voltar pelo histórico, incluindo foco no ticker', async () => {
  await mount();
  expect(rows()).toEqual(pageOne.map(item => item.ticker));
  await click('Próxima');
  expect(params().get('page')).toBe('2');
  expect(rows()).toEqual(pageTwo.map(item => item.ticker));
  expect(container.querySelector('.pagination')?.textContent).toContain('21–40 de 42');
  await open('GAMA3');
  expect(heading()).toBe('GAMA3');
  expect(document.activeElement?.id).toBe('asset-detail-heading');
  expect(params().get('page')).toBe('2');
  await close();
  expect(container.querySelector('.detail-panel')).toBeNull();
  expect(document.activeElement?.id).toBe('asset-row-qa:GAMA3');
  expect(rows()).toEqual(pageTwo.map(item => item.ticker));
  await click('Com evento', container.querySelector('[aria-label="Recortes rápidos"]')!);
  expect(params().get('page')).toBe('1');
  expect(params().get('quality')).toBe('low');
  expect(params().has('asset')).toBe(false);
  expect(rows()).toEqual(['FILT3']);
  expect(container.querySelector('[aria-label="Filtros ativos"]')?.textContent).toContain('Com eventos vinculados');
  await open('FILT3');
  expect(heading()).toBe('FILT3');
  await back();
  expect(params().get('quality')).toBe('low');
  expect(container.querySelector('.detail-panel')).toBeNull();
  expect(rows()).toEqual(['FILT3']);
  await back();
  expect(params().get('page')).toBe('2');
  expect(params().has('quality')).toBe(false);
  expect(rows()).toEqual(pageTwo.map(item => item.ticker));
  await back();
  expect(heading()).toBe('GAMA3');
  expect(params().get('page')).toBe('2');
  await back();
  expect(container.querySelector('.detail-panel')).toBeNull();
  await back();
  expect(params().has('page')).toBe(false);
  expect(rows()).toEqual(pageOne.map(item => item.ticker));
});

it('descarta a página atrasada após aplicar filtro e mantém o recorte correto ao voltar', async () => {
  await mount();
  delayed = url => url.pathname === '/api/v1/assets' && url.searchParams.get('page') === '2';
  await click('Próxima');
  const obsolete = onlyPending();
  expect(rows()).toEqual([]);
  expect(button('Exportar página').disabled).toBe(true);
  await click('Com evento', container.querySelector('[aria-label="Recortes rápidos"]')!);
  expect(obsolete.signal?.aborted).toBe(true);
  expect(rows()).toEqual(['FILT3']);
  await settle(obsolete, list([asset('ANTG3')], 42));
  expect(rows()).toEqual(['FILT3']);
  expect(params().get('quality')).toBe('low');
  expect(params().get('page')).toBe('1');
  expect(container.querySelector('.detail-panel')).toBeNull();
  expect(button('Exportar página').disabled).toBe(false);
  delayed = () => false;
  await back();
  expect(rows()).toEqual(pageTwo.map(item => item.ticker));
  expect(params().get('page')).toBe('2');
  expect(params().has('quality')).toBe(false);
});

it('não restaura detalhe vencido ao trocar de ativo nem reabre o painel após voltar', async () => {
  await mount();
  delayed = url => url.pathname === '/api/v1/assets/qa%3AALFA3';
  await open('ALFA3');
  const obsolete = onlyPending();
  expect(heading()).toBeUndefined();
  await open('BETA4');
  expect(obsolete.signal?.aborted).toBe(true);
  expect(heading()).toBe('BETA4');
  await settle(obsolete, detail('ALFA3'));
  expect(heading()).toBe('BETA4');
  expect(params().get('asset')).toBe('qa:BETA4');
  await back();
  const restored = onlyPending();
  expect(params().get('asset')).toBe('qa:ALFA3');
  expect(heading()).toBeUndefined();
  expect(container.querySelector('.detail-panel')?.textContent).not.toContain('Empresa sintética BETA4');
  await back();
  expect(restored.signal?.aborted).toBe(true);
  expect(params().has('asset')).toBe(false);
  expect(container.querySelector('.detail-panel')).toBeNull();
  await settle(restored, detail('ALFA3'));
  expect(container.querySelector('.detail-panel')).toBeNull();
  expect(heading()).toBeUndefined();
  expect(rows()).toEqual(pageOne.map(item => item.ticker));
});
