import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { JSDOM } from 'jsdom';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import type { AnalysisJob, Job, Operations } from './types';

let dom: JSDOM, root: Root, container: HTMLDivElement;
let AnalysisTracking: typeof import('./AnalysisTracking').AnalysisTracking;
let OperationalStatus: typeof import('./OperationalStatus').OperationalStatus;
let App: typeof import('./App').default;
let operation: Operations, snapshot: AnalysisJob, jobs: Job[];
let writes: string[], reads: string[], deferPost: boolean, settlePost: (response: Response) => void;
let bodies: unknown[], idempotencyKeys: (string | null)[];
let availability: ReturnType<typeof vi.fn<() => void>>, resultChanged: ReturnType<typeof vi.fn<() => void>>;
const assetPath = '/api/v1/assets/qa%3ADEMO3';
const ok = (value: unknown, status = 200) => new Response(JSON.stringify(value), {status});
const button = (text: string, scope: ParentNode = container) =>
  [...scope.querySelectorAll('button')].find(item => item.textContent === text) as HTMLButtonElement;

beforeEach(async () => {
  vi.resetModules();
  dom = new JSDOM('<!doctype html><html><body></body></html>', {url:'https://qa.invalid/?view=jobs', pretendToBeVisual:true});
  for (const name of ['window','document','HTMLElement','Node','navigator']) vi.stubGlobal(name,(dom.window as any)[name]);
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT',true);
  vi.useFakeTimers();
  dom.window.setTimeout = globalThis.setTimeout as any;
  dom.window.clearTimeout = globalThis.clearTimeout as any;
  dom.window.setInterval = globalThis.setInterval as any;
  dom.window.clearInterval = globalThis.clearInterval as any;
  operation = {
    execution_mode:'manual-analysis',
    manual_analysis:{state:'ready',pending_limit:1,repair_attempts:0,quota:{used:2,limit:5,remaining:3,period:'2026-10-31'}},
    worker:{status:'active',heartbeat_at:'2026-10-31T05:00:00Z',expires_at:null,execution_mode:'manual-analysis'},
    ai:{enabled:true,homologated:true,execution_mode:'manual-analysis',reason:'Pronta para uma análise manual.'},
    collection:{sources:[],latest_news_fetch:null,latest_price_fetch:null,latest_macro_fetch:null},
    latest_analysis_snapshot:null,queried_at:'2026-10-31T05:00:00Z',
  };
  snapshot = {asset_id:'qa:DEMO3',manual_scope:'outside',queue_position:null,queue_position_reason:'manual_scope_inactive',
    result_analysis:null,as_of:'2026-10-31T05:00:00Z',
    job:{id:'historical-job',kind:'analyse',status:'queued',created_at:'2026-10-29T12:00:00Z'}};
  jobs = [];
  writes = []; reads = []; bodies = []; idempotencyKeys = []; deferPost = false; availability = vi.fn(); resultChanged = vi.fn();
  vi.stubGlobal('fetch',vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method && init.method !== 'GET') {
      writes.push(url);
      if (url !== assetPath+'/analyse') throw Error('Unexpected mutation blocked: '+url);
      bodies.push(JSON.parse(String(init.body)));
      idempotencyKeys.push(new Headers(init.headers).get('Idempotency-Key'));
      snapshot = {...snapshot,manual_scope:'current',queue_position:1,queue_position_reason:null,
        job:{id:'current-job',kind:'analyse',status:'queued',created_at:'2026-10-31T05:01:00Z'}};
      return deferPost ? new Promise<Response>(resolve=>{settlePost=resolve;}) : ok(snapshot.job,202);
    }
    reads.push(url);
    if (url === '/api/v1/session') return ok({token:'SYNTHETIC_QA_TOKEN'});
    if (url === assetPath+'/analysis-job') return ok(snapshot);
    if (url === '/api/v1/operations') return ok({...operation,private_owner:'PRIVATE_OWNER_MUST_NOT_RENDER'});
    if (url === '/api/v1/overview') return ok({as_of:'2026-10-31T05:00:00Z',window_start:'2026-07-31T03:00:00Z',mode:'demo',
      counts:{assets:2,priced:0,articles:0,events:0,analysed:0,pending:1},ai:operation.ai,last_run:null});
    if (url === '/api/v1/jobs') return ok({items:jobs,total:jobs.length});
    if (url === '/api/v1/sources') return ok({items:[],total:0});
    if (url === '/api/v1/coverage') return ok({});
    throw Error('Unexpected request blocked: '+url);
  }));
  ({AnalysisTracking}=await import('./AnalysisTracking'));
  ({OperationalStatus}=await import('./OperationalStatus'));
  ({default:App}=await import('./App'));
  container=document.createElement('div'); document.body.append(container); root=createRoot(container);
});

afterEach(async () => {
  expect(container.textContent).not.toContain('PRIVATE_OWNER_MUST_NOT_RENDER');
  await act(async()=>root.unmount());
  // Drain zero-delay DOM tasks created by clicks; polling deadlines must be gone.
  await vi.advanceTimersByTimeAsync(0);
  expect(vi.getTimerCount()).toBe(0);
  vi.useRealTimers(); vi.restoreAllMocks(); dom.window.close(); vi.unstubAllGlobals();
});

async function mountTracking(hasAnalysis = true, busy = false) {
  await act(async()=>root.render(<><OperationalStatus data={operation}/><AnalysisTracking assetId="qa:DEMO3" ai={operation.ai}
    busy={busy} revision={0} onResult={resultChanged} onAvailabilityChange={availability} openJobs={()=>{}}
    currentAnalysisId={hasAnalysis ? 'previous-validated' : undefined} currentAnalysisAt="2026-10-29T12:00:00Z" resultLoading={false} resultError={false}/></>));
}

it('outside history stays visible, stops polling and permits one new manual click',async()=>{
  await mountTracking();
  expect(container.textContent).toContain('Solicitação histórica fora do escopo manual atual');
  expect(container.textContent).toContain('Estado histórico registrado');
  expect(container.textContent).toContain('Resultado exibido:');
  expect(container.textContent).not.toContain('Análise na fila');
  expect(button('Reanalisar ativo').getAttribute('aria-disabled')).toBe('false');
  await act(async()=>{await vi.advanceTimersByTimeAsync(60_000);});
  expect(reads.filter(path=>path.endsWith('/analysis-job'))).toHaveLength(1);
  expect(resultChanged).not.toHaveBeenCalled();
  expect(availability).not.toHaveBeenCalled();
});

it.each([
  ['current', 'blocked'], ['outside', 'blocked'], ['current', 'failed'], ['outside', 'failed'],
] as const)('separates a saved result from %s scope job %s without inferring save outcome from the missing link', async (scope, status) => {
  snapshot.manual_scope = scope; snapshot.queue_position_reason = 'not_waiting';
  snapshot.job = {...snapshot.job!,status,error:status === 'blocked' ? 'cli_analysis_validation_rejected' : 'synthetic_failure_after_save',updated_at:'2026-10-31T05:00:00Z'};
  const original = structuredClone(snapshot);
  await mountTracking();
  const text = container.textContent!;
  expect(text).toContain('Resultado exibido: previous-validated');
  expect(text).toContain('Dados até 29 de out. de 2026, 09:00 · Brasília (corte dos dados)');
  expect(text).toContain('O resultado validado exibido continua disponível. O impedimento abaixo se refere à solicitação acompanhada.');
  expect(text).toContain('Solicitação acompanhada: historical-job');
  expect(text).toContain(status === 'blocked' ? 'A resposta desta solicitação foi recusada na validação' : 'Esta solicitação encontrou um impedimento');
  expect(text.indexOf('Resultado exibido:')).toBeLessThan(text.indexOf('Solicitação acompanhada:'));
  expect(text).not.toContain('não foi aprovada');
  expect(text).not.toContain('gerada em');
  expect(text).not.toContain('Análise bloqueada');
  expect(text).not.toContain('Nenhum resultado dela foi validado');
  expect(text).not.toContain('Nenhum novo resultado foi validado');
  expect(text).not.toContain('não substituiu');
  expect(snapshot.result_analysis).toBeNull();
  expect(snapshot.job?.result).toBeUndefined();
  expect(snapshot).toEqual(original);
  expect(writes).toHaveLength(0);
});

it('rapid duplicate clicks dispatch one POST and update availability without replay',async()=>{
  deferPost=true;
  await mountTracking();
  await act(async()=>{button('Reanalisar ativo').click();button('Reanalisar ativo').click();});
  expect(writes).toEqual([assetPath+'/analyse']);
  expect(bodies).toEqual([{reanalyze:true}]);
  expect(idempotencyKeys).toEqual([expect.stringMatching(/^radar-[a-zA-Z0-9_-]+$/)]);
  expect(button('Solicitando análise…').getAttribute('aria-disabled')).toBe('true');
  await act(async()=>settlePost(ok(snapshot.job,202)));
  expect(button('Análise solicitada').getAttribute('aria-disabled')).toBe('true');
  expect(container.textContent).toContain('Fila manual: uma solicitação por vez');
  expect(container.textContent).not.toContain('incluindo coletas');
  expect(availability).toHaveBeenCalledTimes(1);
  await act(async()=>button('Análise solicitada').click());
  expect(writes).toHaveLength(1);
});

it('explicit reanalysis explains quota and the possible unchanged cutoff while preserving the busy guard', async () => {
  await mountTracking(true, true);
  const control = button('Reanalisar ativo');
  expect(control.getAttribute('aria-disabled')).toBe('true');
  const help = document.getElementById(control.getAttribute('aria-describedby')!);
  expect(help?.textContent).toBe('Nova chamada de IA, sujeita à cota local. Usa as evidências salvas; o corte dos dados pode permanecer igual.');
  await act(async () => {control.click(); control.click();});
  expect(writes).toHaveLength(0);
});

it.each([
  ['manual-analysis', false], ['analysis-enabled', true], [undefined, true],
] as const)('keeps the normal action and omits reanalyze in mode=%s with saved analysis=%s', async (mode, hasAnalysis) => {
  operation.ai.execution_mode = mode;
  operation.execution_mode = mode || 'analysis-enabled';
  await mountTracking(hasAnalysis);
  const control = button('Analisar ativo');
  expect(control.getAttribute('aria-describedby')).toBeNull();
  expect(container.textContent).not.toContain('Nova chamada de IA, sujeita à cota local');
  await act(async () => control.click());
  expect(writes).toEqual([assetPath + '/analyse']);
  expect(bodies).toEqual([{}]);
});

it.each([
  ['busy','Uma solicitação em andamento','Outra análise manual está em andamento.'],
  ['unavailable','Indisponível','Worker manual indisponível.'],
] as const)('manual %s disables submission and shows its bounded state',async(state,copy,reason)=>{
  operation.manual_analysis!.state=state; operation.ai={...operation.ai,enabled:false,reason};
  await mountTracking();
  expect(container.textContent).toContain(copy);
  expect(container.textContent).toContain(reason);
  expect(button('Reanalisar ativo').getAttribute('aria-disabled')).toBe('true');
  await act(async()=>button('Reanalisar ativo').click());
  expect(writes).toHaveLength(0);
});

it('shows zero remaining quota and distinguishes local cap from subscription',async()=>{
  operation.manual_analysis!.state='unavailable';
  operation.manual_analysis!.quota={used:5,limit:5,remaining:0,period:'2026-10-31'};
  operation.ai={...operation.ai,enabled:false,error_code:'manual_quota_exhausted',reason:'Cota local esgotada.'};
  await mountTracking();
  expect(container.textContent).toContain('Cota local: 5 de 5 · 0 restantes');
  expect(container.textContent).toContain('31 de out. de 2026');
  expect(container.textContent).toContain('não representa a franquia oficial');
  expect(container.textContent).toContain('Reservas em andamento ou sem confirmação também contam');
  await act(async()=>button('Reanalisar ativo').click());
  expect(writes).toHaveLength(0);
});

it('missing manual metadata remains unavailable, never inventing a zero quota',async()=>{
  delete operation.manual_analysis; operation.ai.enabled=false;
  await mountTracking();
  expect(container.textContent).toContain('Cota local indisponível');
  expect(container.textContent).not.toContain('0 restantes');
});

it.each(['queued','running','retry_wait'])('current %s blocks another request even if availability is stale',async status=>{
  snapshot.manual_scope='current';snapshot.queue_position_reason=null;snapshot.job!.status=status;
  await mountTracking();
  expect(button('Análise solicitada').getAttribute('aria-disabled')).toBe('true');
  await act(async()=>button('Análise solicitada').click());
  expect(writes).toHaveLength(0);
});

it('inactive-scope reason is safe even without the optional manual_scope field',async()=>{
  delete snapshot.manual_scope;
  await mountTracking();
  expect(container.textContent).toContain('fora do escopo manual atual');
  expect(button('Reanalisar ativo').getAttribute('aria-disabled')).toBe('false');
});

it.each([0,1])('cache reuse only claims no new call when calls=%s',async calls=>{
  snapshot.manual_scope='current'; snapshot.queue_position_reason=null;
  snapshot.job={...snapshot.job!,status:'succeeded',result:{cache_reused:true,calls,analysis_id:'previous-validated'}};
  snapshot.result_analysis={analysis_id:'previous-validated',status:'insufficient_data',as_of:snapshot.as_of};
  await mountTracking();
  expect(container.textContent?.includes('Nenhuma nova chamada de IA.')).toBe(calls===0);
  expect(container.textContent).toContain('resultado validado disponível');
  expect(availability).toHaveBeenCalledTimes(1);
  expect(writes).toHaveLength(0);
});

it('legacy payload without manual fields keeps existing queued tracking',async()=>{
  operation.execution_mode='analysis-enabled';operation.ai.execution_mode='analysis-enabled';delete operation.manual_analysis;
  delete snapshot.manual_scope;snapshot.queue_position_reason=null;snapshot.queue_position=2;
  await mountTracking();
  expect(container.textContent).toContain('Análise na fila · posição 2');
  expect(container.textContent).toContain('incluindo coletas');
  expect(button('Análise solicitada').getAttribute('aria-disabled')).toBe('true');
});

it('manual Jobs disables collection and all generic retries while preserving cancel',async()=>{
  jobs=[{id:'failed-analysis',kind:'analyse',status:'failed',created_at:snapshot.as_of},
    {id:'failed-news',kind:'news',status:'failed',created_at:snapshot.as_of},
    {id:'historical-pending',kind:'analyse',status:'queued',created_at:snapshot.as_of}];
  await act(async()=>root.render(<App/>));
  expect(button('Solicitar atualização').disabled).toBe(true);
  expect(button('Executar diagnóstico').disabled).toBe(true);
  const retries=[...container.querySelectorAll('button')].filter(item=>item.textContent==='Tentar novamente');
  expect(retries).toHaveLength(2);expect(retries.every(item=>item.disabled)).toBe(true);
  expect(button('Cancelar').disabled).toBe(false);
  await act(async()=>{button('Solicitar atualização').click();retries.forEach(item=>item.click());});
  expect(writes).toHaveLength(0);
  expect(container.textContent).toContain('historical-pending');
});

it.each(['manual-analysis','collection-only','analysis-enabled'] as const)('retry_wait in %s only presents a future retry outside manual mode',async mode=>{
  operation.execution_mode=mode;
  operation.ai={...operation.ai,execution_mode:mode,enabled:mode!=='collection-only'};
  if(mode!=='manual-analysis') delete operation.manual_analysis;
  jobs=[{id:'historical-retry',kind:'analyse',status:'retry_wait',created_at:snapshot.as_of,not_before:'2026-11-03T15:30:00Z'}];
  await act(async()=>root.render(<App/>));
  expect(container.textContent).toContain('historical-retry');
  expect(container.textContent?.includes('Próxima tentativa:')).toBe(mode!=='manual-analysis');
  if(mode==='manual-analysis') expect(container.textContent).toContain('Repetição indisponível no modo manual; histórico preservado.');
  else expect(container.textContent).toContain('3 de nov. de 2026, 12:30 · Brasília');
  expect(writes).toHaveLength(0);
});

it('manual Sources disables historical collection and diagnostics',async()=>{
  window.history.replaceState(null,'','?view=sources');
  await act(async()=>root.render(<App/>));
  expect(button('Buscar histórico').disabled).toBe(true);
  expect(button('Executar diagnóstico').disabled).toBe(true);
  await act(async()=>{button('Buscar histórico').click();button('Executar diagnóstico').click();});
  expect(writes).toHaveLength(0);
});

it('collection-only permits collection and non-analysis retry with an explicit compatible consumer',async()=>{
  operation.execution_mode='collection-only';operation.ai={enabled:false,execution_mode:'collection-only'};
  operation.job_control={reason:'ready',allowed_kinds:['refresh','news','probe','backfill'],expires_at:new Date(Date.now()+90_000).toISOString()};
  delete operation.manual_analysis;
  jobs=[{id:'failed-news',kind:'news',status:'failed',created_at:snapshot.as_of}];
  await act(async()=>root.render(<App/>));
  expect(button('Solicitar atualização').disabled).toBe(false);
  expect(button('Tentar novamente').disabled).toBe(false);
});

it.each(['manual_consumer_absent','manual_consumer_expired','manual_consumer_restricted','manual_consumer_unverified'])(
  'blocks every manual collection control for %s even with an active heartbeat',async reason=>{
    operation.execution_mode='collection-only';
    operation.worker.status='active';
    operation.worker.execution_mode='collection-only';
    operation.job_control={reason,allowed_kinds:[],expires_at:null};
    delete operation.manual_analysis;
    jobs=[{id:'failed-news',kind:'news',status:'failed',created_at:snapshot.as_of}];
    await act(async()=>root.render(<App/>));
    expect(button('Solicitar atualização').disabled).toBe(true);
    expect(button('Executar diagnóstico').disabled).toBe(true);
    expect(button('Tentar novamente').disabled).toBe(true);
    expect(container.querySelector('#manual-collection-state')?.textContent).toBeTruthy();
    await act(async()=>{button('Solicitar atualização').click();button('Executar diagnóstico').click();button('Tentar novamente').click();});
    window.history.replaceState(null,'','?view=sources');
    await act(async()=>window.dispatchEvent(new dom.window.PopStateEvent('popstate')));
    expect(button('Buscar histórico').disabled).toBe(true);
    await act(async()=>button('Buscar histórico').click());
    expect(writes).toHaveLength(0);
  });

it('disables manual controls when the advertised lease expires',async()=>{
  operation.execution_mode='collection-only';
  operation.job_control={reason:'ready',allowed_kinds:['refresh','probe','news'],expires_at:new Date(Date.now()+1000).toISOString()};
  await act(async()=>root.render(<App/>));
  expect(button('Solicitar atualização').disabled).toBe(false);
  await act(async()=>vi.advanceTimersByTimeAsync(1001));
  expect(button('Solicitar atualização').disabled).toBe(true);
  expect(container.querySelector('#manual-collection-state')?.textContent).toContain('venceu');
  expect(writes).toHaveLength(0);
});

it('closes admission if renewing operational status fails',async()=>{
  operation.execution_mode='collection-only';
  operation.job_control={reason:'ready',allowed_kinds:['refresh','news'],expires_at:new Date(Date.now()+90_000).toISOString()};
  await act(async()=>root.render(<App/>));
  expect(button('Solicitar atualização').disabled).toBe(false);
  const read=vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation((url,init)=>String(url)==='/api/v1/operations'?Promise.reject(Error('synthetic offline')):read(url,init));
  await act(async()=>vi.advanceTimersByTimeAsync(30_000));
  expect(button('Solicitar atualização').disabled).toBe(true);
  expect(writes).toHaveLength(0);
});

it('surfaces transactional rejection after a previously ready read without retrying it',async()=>{
  operation.execution_mode='collection-only';
  operation.job_control={reason:'ready',allowed_kinds:['refresh','news'],expires_at:new Date(Date.now()+90_000).toISOString()};
  const read=vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation(async(url,init)=>{
    if(String(url)==='/api/v1/jobs'&&init?.method==='POST'){
      writes.push(String(url));
      operation.job_control={reason:'manual_consumer_restricted',allowed_kinds:[],expires_at:null};
      return ok({detail:'manual_consumer_restricted'},409);
    }
    return read(url,init);
  });
  await act(async()=>root.render(<App/>));
  await act(async()=>button('Solicitar atualização').click());
  expect(writes).toEqual(['/api/v1/jobs']);
  expect(container.querySelector('#action-error')?.textContent).toContain('A coleta diária não consome pedidos manuais');
  expect(button('Solicitar atualização').disabled).toBe(true);
  expect(container.textContent).not.toContain('Execução solicitada.');
  await act(async()=>button('Solicitar atualização').click());
  expect(writes).toHaveLength(1);
});

it.each(['jobs','market'])('keeps %s diagnostics consistent with disabled controls during a pending renewal',async view=>{
  window.history.replaceState(null,'','?view='+view);
  operation.execution_mode='collection-only';
  operation.job_control={reason:'ready',allowed_kinds:['refresh','news'],expires_at:new Date(Date.now()+90_000).toISOString()};
  const read=vi.mocked(fetch).getMockImplementation()!;
  let pending=false, settle!: (response:Response)=>void;
  vi.mocked(fetch).mockImplementation((url,init)=>{
    if(String(url).startsWith('/api/v1/assets?'))return Promise.resolve(ok({items:[],total:0}));
    if(String(url)==='/api/v1/operations'&&pending)return new Promise<Response>(resolve=>{settle=resolve;});
    return read(url,init);
  });
  await act(async()=>root.render(<App/>));
  expect(container.textContent).toContain('Pedidos manuais de coleta disponíveis.');
  pending=true;
  await act(async()=>vi.advanceTimersByTimeAsync(30_000));
  expect(button('Solicitar atualização').disabled).toBe(true);
  expect(container.textContent).not.toContain('Pedidos manuais de coleta disponíveis.');
  expect(container.textContent).toContain('Aguardando confirmação de um executor');
  pending=false;
  await act(async()=>settle(ok(operation)));
  expect(button('Solicitar atualização').disabled).toBe(false);
  expect(container.textContent).toContain('Pedidos manuais de coleta disponíveis.');
  expect(writes).toHaveLength(0);
});

it('translates a rejection recovered from its receipt without resending the command',async()=>{
  const key='radar-recovery-collection';
  window.sessionStorage.setItem('radar-pending-mutation-v1',JSON.stringify({
    key,path:'/jobs',method:'POST',body:{kind:'refresh',scope:{}},createdAt:new Date().toISOString(),
  }));
  const read=vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation((url,init)=>{
    if(String(url)==='/api/v1/mutations/'+key)return Promise.resolve(ok({
      key,path:'/api/v1/jobs',method:'POST',state:'completed',status_code:409,response:{detail:'manual_consumer_restricted'},
    }));
    return read(url,init);
  });
  vi.stubGlobal('requestAnimationFrame',vi.fn());
  vi.resetModules();
  ({default:App}=await import('./App'));
  await act(async()=>root.render(<App/>));
  await act(async()=>button('Consultar resultado').click());
  expect(container.querySelector('#action-error')?.textContent).toContain('A coleta diária não consome pedidos manuais');
  expect(container.textContent).not.toContain('manual_consumer_restricted');
  expect(window.sessionStorage.getItem('radar-pending-mutation-v1')).toBeNull();
  expect(writes).toHaveLength(0);
});

it('guides an empty catalogue to read-only status while execution remains unavailable',async()=>{
  operation.execution_mode='collection-only';delete operation.manual_analysis;
  operation.job_control={reason:'manual_consumer_absent',allowed_kinds:[],expires_at:null};
  const read=vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation(async(url,init)=>{
    if(String(url)==='/api/v1/overview')return ok({as_of:snapshot.as_of,window_start:'2026-07-31T03:00:00Z',mode:'demo',
      counts:{assets:0,priced:0,articles:0,events:0,analysed:0,pending:0},ai:operation.ai,last_run:null});
    if(String(url).startsWith('/api/v1/assets?'))return ok({items:[],total:0});
    return read(url,init);
  });
  window.history.replaceState(null,'','?view=overview');
  await act(async()=>root.render(<App/>));
  expect(container.textContent).toContain('Consulte Fontes e cobertura para verificar o estado da coleta.');
  expect(button('Executar diagnóstico').disabled).toBe(true);
  const disclosure=[...container.querySelectorAll('summary')].find(item=>item.textContent?.includes('Ver estado operacional'))!;
  await act(async()=>disclosure.click());
  expect(disclosure.parentElement?.hasAttribute('open')).toBe(true);
  expect(writes).toHaveLength(0);
});
