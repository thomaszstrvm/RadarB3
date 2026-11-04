import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { JSDOM } from 'jsdom';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import type { Asset, Job } from './types';

let dom: JSDOM, root: Root, container: HTMLDivElement;
let App: typeof import('./App').default;
let jobs: Job[], aiEnabled: boolean, jobDelay: number;
let jobStarted: number, jobCompleted: number, jobAborted: number, active: number, maxActive: number;
let settleAssets: (response: Response) => void;
const ok = (data: unknown) => new Response(JSON.stringify(data), {status: 200});
const asset = (ticker: string): Asset => ({id: 'qa:' + ticker, ticker, name: ticker, price: null,
  return_21: null, watchlisted: false});
const button = (text: string, scope: ParentNode = container) =>
  [...scope.querySelectorAll('button')].find(node => node.textContent === text) as HTMLButtonElement;
const row = (id: string) => [...container.querySelectorAll('tbody tr')].find(node => node.textContent?.includes(id))!;

beforeEach(async () => {
  vi.resetModules();
  dom = new JSDOM('<!doctype html><html><body></body></html>', {url: 'https://qa.invalid/?view=market&search=ALFA3', pretendToBeVisual: true});
  for (const name of ['window', 'document', 'HTMLElement', 'Node', 'navigator']) {
    vi.stubGlobal(name, (dom.window as any)[name]);
  }
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  vi.useFakeTimers();
  // Browser timers share one namespace; JSDOM otherwise wraps Node timers twice.
  dom.window.setTimeout = globalThis.setTimeout as any;
  dom.window.clearTimeout = globalThis.clearTimeout as any;
  dom.window.setInterval = globalThis.setInterval as any;
  dom.window.clearInterval = globalThis.clearInterval as any;
  jobs = []; aiEnabled = false; jobDelay = 0;
  jobStarted = jobCompleted = jobAborted = active = maxActive = 0;
  vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
    if (init?.method && init.method !== 'GET') throw Error('Unexpected mutation forbidden: ' + url);
    if (url === '/api/v1/overview') return Promise.resolve(ok({as_of:'2026-10-30T18:00:00Z', window_start:'2026-07-30T03:00:00Z', mode:'demo',
      counts:{assets:2, priced:0, articles:0, events:0, analysed:0, pending:0}, market_counts:{priced:1,analysed:1}, ai:{enabled:aiEnabled}, last_run:null}));
    if (url === '/api/v1/operations') return Promise.resolve(ok({execution_mode:aiEnabled?'analysis-enabled':'collection-only',
      worker:{status:'absent',heartbeat_at:null,expires_at:null,execution_mode:null}, ai:{enabled:aiEnabled,homologated:true},
      collection:{sources:[], latest_news_fetch:null,latest_price_fetch:null,latest_macro_fetch:null},latest_analysis_snapshot:null,queried_at:'2026-10-30T18:00:00Z'}));
    if (url.startsWith('/api/v1/assets?')) {
      if (new URL('https://qa.invalid'+url).searchParams.get('search') === 'BETA3') {
        return new Promise<Response>(resolve => {settleAssets = resolve;});
      }
      return Promise.resolve(ok({items:[asset('ALFA3')], total:1}));
    }
    if (url === '/api/v1/jobs') {
      jobStarted++; active++; maxActive = Math.max(maxActive, active);
      return new Promise<Response>((resolve, reject) => {
        let finished = false;
        const finish = () => {if (!finished) {finished = true; active--; jobCompleted++; resolve(ok({items:jobs,total:jobs.length}));}};
        const timer = jobDelay ? setTimeout(finish, jobDelay) : undefined;
        init?.signal?.addEventListener('abort', () => {
          if (!finished) {finished = true; active--; jobAborted++; clearTimeout(timer); reject(new DOMException('Aborted','AbortError'));}
        }, {once:true});
        if (!jobDelay) finish();
      });
    }
    throw Error('Unexpected request forbidden: ' + url);
  }));
  ({default: App} = await import('./App'));
  container = document.createElement('div'); document.body.append(container); root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  expect(vi.getTimerCount()).toBe(0);
  vi.useRealTimers(); vi.restoreAllMocks(); dom.window.close(); vi.unstubAllGlobals();
});

async function mount(view = 'market') {
  window.history.replaceState(null, '', view === 'market' ? '?view=market&search=ALFA3' : '?view=jobs');
  await act(async () => root.render(<App/>));
}

it('sincroniza gráfico, lista e revisão com o evento de origem e preserva a escolha manual nesse contexto', async () => {
  const events=['Primeiro','Segundo'].map((title,i)=>({event_id:'event-'+i,title,event_date:'2026-10-2'+i,evidence_ids:['ref-'+i]}));
  const evidence=events.map((e,i)=>({evidence_id:'ref-'+i,record_type:'article',source_id:'br_uol',title:e.title,content:'Trecho.',
    content_sha256:'hash-'+i,access_level:'excerpt',published_at:e.event_date,timestamp_precision:'date',entity_ids:[],quality_flags:[]}));
  const previous=vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation((input,init)=>{
    const path=String(input);
    if(path.endsWith('/analysis-job')) return Promise.resolve(ok({asset_id:decodeURIComponent(path.split('/').at(-2)!),job:null,queue_position:null,result_analysis:null}));
    if(path==='/api/v1/events/missing') return Promise.resolve(ok({id:'missing',title:'Somente na busca de origem',asset_ids:['qa:ALFA3']}));
    for(const ticker of ['ALFA3','DEMO4']) if(path==='/api/v1/assets/qa%3A'+ticker) return Promise.resolve(ok({
      ...asset(ticker),events,evidence,metrics:[],articles:[],analysis:null,prices:[{date:'2026-10-19',close:10},{date:'2026-10-22',close:11}],
    }));
    return previous(input,init);
  });
  async function route(ticker:string,origin:string) {
    window.history.replaceState(null,'','?'+new URLSearchParams({view:'market',asset:'qa:'+ticker,origin_asset:'qa:ALFA3',origin_event:origin}));
    await act(async()=>{window.dispatchEvent(new dom.window.PopStateEvent('popstate'));});
  }
  function active(index:number|null) {
    expect(container.querySelector('.event-selected button')?.textContent).toBe(index===null?undefined:events[index].title);
    expect(container.querySelector('.event-line.active + circle')?.getAttribute('aria-label')).toBe(index===null?undefined:'Evento: '+events[index].title);
    expect(container.querySelector('.selected-evidence')?.id).toBe(index===null?undefined:'evidence-ref-'+index);
  }
  await mount();
  await route('ALFA3','event-1'); active(1);
  await act(async()=>button('Primeiro',container.querySelector('.event-list')!).click()); active(0);
  await route('ALFA3','event-1'); active(0);
  await route('ALFA3','missing'); active(null);
  await route('ALFA3','event-1'); active(1);
  await route('DEMO4','event-1'); active(null);
  // jsdom queues selectionchange at zero delay when each asset heading receives focus.
  await act(async()=>{await vi.advanceTimersByTimeAsync(0);});
});

it('shows a vintage session date in the market row without inventing a trade timestamp',async()=>{
  const previous=vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation((input,init)=>String(input).startsWith('/api/v1/assets?')
    ? Promise.resolve(ok({items:[{...asset('ALFA3'),price:62.02,price_at:null,price_timestamp_precision:'date',price_session_date:'2026-10-30'}],total:1}))
    : previous(input,init));
  await mount();
  expect(row('ALFA3').textContent).toContain('30 de out. de 2026');
  expect(row('ALFA3').textContent).not.toContain('Preço não coletado');
  expect(row('ALFA3').textContent).not.toContain('00:00');
});
async function searchBeta() {
  await act(async () => {
    window.history.pushState(null, '', '?view=market&search=BETA3');
    window.dispatchEvent(new dom.window.PopStateEvent('popstate'));
  });
}

it('hides old market rows for a pending or failed new query and recovers with matching rows', async () => {
  const exported = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:qa');
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
  vi.spyOn(dom.window.HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  await mount();
  expect(button('Exportar página').disabled).toBe(false);
  await searchBeta();
  expect(row('ALFA3')).toBeUndefined();
  expect(button('Exportar página').disabled).toBe(true);
  await act(async () => button('Exportar página').click());
  expect(exported).not.toHaveBeenCalled();
  await act(async () => settleAssets(new Response(JSON.stringify({detail:'Falha sintética'}), {status:503})));
  expect(row('ALFA3')).toBeUndefined();
  expect(container.textContent).toContain('Falha sintética');
  expect(button('Exportar página').disabled).toBe(true);
  await act(async () => button('Tentar novamente').click());
  await act(async () => settleAssets(ok({items:[asset('BETA3')],total:1})));
  expect(row('BETA3')).toBeDefined();
  expect(row('ALFA3')).toBeUndefined();
  expect(button('Exportar página').disabled).toBe(false);
});

it('exports matching filters and rows after the replacement query succeeds', async () => {
  const exported = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:qa');
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
  vi.spyOn(dom.window.HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  await mount(); await searchBeta();
  await act(async () => settleAssets(ok({items:[asset('BETA3')],total:1,as_of:'2026-10-30T18:05:00Z'})));
  expect(button('Exportar página').disabled).toBe(false);
  await act(async () => button('Exportar página').click());
  const payload = JSON.parse(await (exported.mock.calls[0][0] as Blob).text());
  expect(payload.filters.search).toBe('BETA3');
  expect(payload.as_of).toBe('2026-10-30T18:05:00Z');
  expect(payload.items.map((item: Asset) => item.ticker)).toEqual(['BETA3']);
});

it('opens on eligible assets by event count, offers real coverage shortcuts and shows no empty detail panel', async () => {
  await mount();
  const lastQuery = () => {
    const paths=vi.mocked(fetch).mock.calls.map(([path])=>String(path)).filter(path=>path.startsWith('/api/v1/assets?'));
    return new URL(paths.at(-1)!,'https://qa.invalid').searchParams;
  };
  expect(lastQuery().get('sort')).toBe('-event_count');
  expect(lastQuery().get('type')).toBe('eligible');
  expect(container.querySelector('.detail-panel')).toBeNull();
  const cuts = container.querySelector('[aria-label="Recortes rápidos"]')!;
  expect([...cuts.querySelectorAll('button')].map(b=>b.textContent)).toEqual(['Todos','Com evento','Com preço','Com análise']);
  await act(async()=>button('Com evento',cuts).click());
  expect(lastQuery().get('quality')).toBe('low');
  await act(async()=>button('Com preço').click());
  expect(lastQuery().get('state')).toBe('priced');
  expect(lastQuery().has('quality')).toBe(false);
  await act(async()=>button('Com análise').click());
  expect(lastQuery().get('state')).toBe('analysed');
  await act(async()=>button('Filtros').click());
  const type = [...container.querySelectorAll('select')].find(select=>select.value==='eligible')!;
  await act(async()=>{type.value='unknown';type.dispatchEvent(new dom.window.Event('change',{bubbles:true}));});
  expect(lastQuery().get('type')).toBe('unknown');
  expect(container.querySelector('[aria-label="Filtros ativos"]')?.textContent).toContain('Tipo: Não classificados');
  await act(async()=>button('Todos',cuts).click());
  expect(lastQuery().has('quality')||lastQuery().has('state')).toBe(false);
  expect(lastQuery().get('type')).toBe('unknown');
  expect(lastQuery().get('search')).toBe('ALFA3');
  expect(lastQuery().get('page')).toBe('1');
});

it('abre o total global no recorte correspondente e preserva a busca anterior no histórico', async () => {
  await mount();
  const initial = window.location.search;
  const entries = window.history.length;
  const coverage = container.querySelector<HTMLDetailsElement>('.market-context')!;
  coverage.open = true;
  const indicator = coverage.querySelector<HTMLAnchorElement>('a[href*="state=analysed"]')!;
  expect(indicator.querySelector('strong')?.textContent).toBe('1');
  await act(async () => indicator.click());
  const url = new URLSearchParams(window.location.search);
  expect(Object.fromEntries(url)).toEqual({view:'market',type:'all',state:'analysed'});
  expect(window.history.length).toBe(entries+1);
  expect(coverage.open).toBe(false);
  expect(button('Com análise').getAttribute('aria-pressed')).toBe('true');
  const reads = vi.mocked(fetch).mock.calls.map(([path])=>String(path)).filter(path=>path.startsWith('/api/v1/assets?'));
  const requested = new URL(reads.at(-1)!,'https://qa.invalid').searchParams;
  expect(requested.get('state')).toBe('analysed');
  expect(requested.has('type') || requested.has('search')).toBe(false);
  await act(async () => { window.history.back(); await vi.advanceTimersByTimeAsync(10); });
  expect(window.location.search).toBe(initial);
  expect(container.querySelector<HTMLInputElement>('.search-field input')?.value).toBe('ALFA3');
});

it('completes six-second Jobs reads without overlapping requests or interval aborts', async () => {
  jobDelay = 6000;
  jobs = [{id:'slow-job',kind:'macro',status:'succeeded',created_at:'2026-10-30T18:00:00Z'}];
  await mount('jobs');
  for (let second = 0; second < 30; second++) {
    await act(async () => {await vi.advanceTimersByTimeAsync(1000);});
  }
  expect(jobCompleted).toBeGreaterThanOrEqual(1);
  expect(jobAborted).toBe(0);
  expect(maxActive).toBe(1);
  expect(container.textContent).toContain('slow-job');
});

it('pauses Jobs polling while hidden and resumes without concurrent reads', async () => {
  await mount('jobs');
  const reads = jobStarted;
  await act(async () => {Object.defineProperty(document,'visibilityState',{configurable:true,value:'hidden'}); document.dispatchEvent(new dom.window.Event('visibilitychange'));});
  await act(async () => {await vi.advanceTimersByTimeAsync(30_000);});
  expect(jobStarted).toBe(reads);
  await act(async () => {Object.defineProperty(document,'visibilityState',{configurable:true,value:'visible'}); document.dispatchEvent(new dom.window.Event('visibilitychange'));});
  expect(jobStarted).toBe(reads+1);
  expect(maxActive).toBe(1);
});

it('cancels an in-flight Jobs read when leaving the page and schedules no further Jobs read', async () => {
  jobDelay = 6000;
  await mount('jobs');
  await act(async () => {await vi.advanceTimersByTimeAsync(1000);});
  await act(async () => {
    const market = [...container.querySelectorAll('nav a')].find(link => link.textContent === 'Mercado') as HTMLAnchorElement;
    market.click();
  });
  expect(jobAborted).toBe(1);
  expect(active).toBe(0);
  for (let second = 0; second < 30; second++) {
    await act(async () => {await vi.advanceTimersByTimeAsync(1000);});
  }
  expect(jobStarted).toBe(1);
});

it.each([true,false])('renders independent cancel/retry controls; analysis permission=%s', async allowed => {
  aiEnabled = allowed;
  const read=vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation(async(url,init)=>{
    const response=await read(url,init);
    if(String(url)!=='/api/v1/operations')return response;
    return ok({...await response.json(),job_control:{reason:'ready',
      allowed_kinds:allowed?['macro','analyse','analysis']:['macro'],
      expires_at:new Date(Date.now()+90_000).toISOString()}});
  });
  jobs = ['paused','blocked'].flatMap(status => ['analyse','analysis','macro'].map(kind => ({id:kind+'-'+status, kind,status,created_at:'2026-10-30T18:00:00Z'})));
  await mount('jobs');
  for (const job of jobs) {
    expect(button('Cancelar',row(job.id))).toBeDefined();
    const retry = button('Tentar novamente',row(job.id));
    expect(retry).toBeDefined();
    expect(retry.disabled).toBe(['analyse','analysis'].includes(job.kind) && !allowed);
  }
});

it('uses not_before for pending retries and does not announce retries for terminal jobs', async () => {
  jobs = ['retry_wait','succeeded'].map(status => ({id:'retry-'+status,kind:'macro',status,created_at:'2026-10-30T18:00:00Z',not_before:'2026-10-30T18:30:00Z'}));
  await mount('jobs');
  expect(row('retry-retry_wait').textContent).toContain('Próxima tentativa:');
  expect(row('retry-retry_wait').textContent).toContain('15:30');
  expect(row('retry-succeeded').textContent).not.toContain('Próxima tentativa:');
});
