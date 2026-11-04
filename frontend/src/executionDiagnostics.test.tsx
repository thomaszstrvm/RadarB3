// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { AnalysisTrackingStatus } from './AnalysisTracking';
import type { TrackingState } from './pollAnalysis';
import type { AnalysisExecutionDiagnostic, AnalysisJob } from './types';

const record = (category:AnalysisExecutionDiagnostic['diagnostic']['category']='nonzero_exit'):AnalysisExecutionDiagnostic => ({
  run_id:'qa-cli-run',attempt:2,recorded_at:'2026-10-31T01:45:00Z',
  diagnostic:{version:1,stage:'cli',exit_code:1,category,event_types:['error','turn.failed']},
});
const snapshot = ():AnalysisJob => ({
  asset_id:'qa:DEMO3',as_of:'2026-10-31T01:46:00Z',queue_position:null,queue_position_reason:null,result_analysis:null,
  job:{id:'qa-cli-job',kind:'analyse',status:'failed',created_at:'2026-10-31T01:40:00Z',error:'cli_nonzero_exit'},
});
let root:Root,container:HTMLDivElement;
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT',true);
  vi.stubGlobal('fetch',vi.fn(() => {throw new Error('Network forbidden in diagnostic DOM tests');}));
  container=document.createElement('div');document.body.append(container);root=createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());container.remove();expect(fetch).not.toHaveBeenCalled();vi.unstubAllGlobals();
});
async function render(data:AnalysisJob) {
  const state:TrackingState={phase:'idle',error:'',snapshot:data};
  await act(async () => root.render(<AnalysisTrackingStatus state={state} onRefresh={()=>{}} openJobs={()=>{}}/>));
}
const disclosure = (title:string) => [...container.querySelectorAll('details')].find(item=>item.querySelector('summary')?.textContent?.startsWith(title));

describe('Diagnóstico de execução separado da validação',() => {
  it.each([
    ['nonzero_exit','CLI encerrado com erro'],
    ['output_schema_rejected','Contrato de saída recusado pelo CLI'],
    ['authentication','Falha de autenticação do CLI'],
    ['rate_limit','Limite de uso do CLI'],
    ['model_unavailable','Modelo indisponível no CLI'],
  ] as const)('renders category %s in its own expandable CLI disclosure',async (category,text) => {
    await render({...snapshot(),execution_diagnostics:[record(category)]});
    const detail=disclosure('Diagnóstico da execução');
    expect(detail?.open).toBe(false);
    await act(async () => detail?.querySelector('summary')?.click());
    expect(detail?.open).toBe(true);
    expect(detail?.querySelector('summary')?.textContent).toBe('Diagnóstico da execução · tentativa 2');
    expect(detail?.textContent).toContain('Etapa: Execução do CLI');
    expect(detail?.textContent).toContain('Categoria: '+text);
    expect(detail?.textContent).toContain('Código de saída: 1.');
    expect(detail?.textContent).toContain('Eventos do CLI: error · turn.failed.');
    expect(container.textContent).not.toContain('Consistência com as evidências');
    expect(container.textContent).not.toContain('Diagnóstico da validação');
  });

  it('keeps financial validation in its existing separate disclosure',async () => {
    await render({...snapshot(),execution_diagnostics:[record()],validation_diagnostics:[{
      run_id:'qa-validation-run',attempt:1,recorded_at:'2026-10-31T01:44:00Z',
      diagnostic:{version:1,stage:'semantic',issues:[{code:'factual_number_not_in_cited_evidence',path:'/claims/0/statement',rule:null}]},
    }]});
    expect(container.querySelectorAll('details')).toHaveLength(2);
    expect(disclosure('Diagnóstico da execução')?.textContent).not.toContain('Consistência com as evidências');
    const validation=disclosure('Diagnóstico da validação');
    expect(validation?.textContent).toContain('Consistência com as evidências');
    expect(validation?.textContent).toContain('factual_number_not_in_cited_evidence');
    expect(validation?.textContent).toContain('/claims/0/statement');
    expect(validation?.textContent).not.toContain('Execução do CLI');
  });

  it('does not reinterpret an old failure without execution diagnostics',async () => {
    await render(snapshot());
    expect(disclosure('Diagnóstico da execução')).toBeUndefined();
    expect(container.textContent).toContain('Esta solicitação encontrou um impedimento.');
    expect(container.textContent).not.toContain('Código de saída:');
  });

  it('preserves missing exit code and events rather than manufacturing zero',async () => {
    const item=record();item.diagnostic.exit_code=null;item.diagnostic.event_types=[];
    await render({...snapshot(),execution_diagnostics:[item]});
    const text=disclosure('Diagnóstico da execução')?.textContent;
    expect(text).toContain('Código de saída: Não informado.');
    expect(text).not.toContain('Código de saída: 0.');
    expect(text).toContain('Sem evento estruturado disponível.');
  });

  it('formats recording time in São Paulo while retaining its original instant',async () => {
    await render({...snapshot(),execution_diagnostics:[record()]});
    const time=disclosure('Diagnóstico da execução')?.querySelector('time');
    expect(time?.getAttribute('datetime')).toBe('2026-10-31T01:45:00Z');
    expect(time?.textContent).toContain('30 de out. de 2026');
    expect(time?.textContent).toContain('22:45');
    expect(time?.textContent).toContain('Brasília');
  });

  it('renders only known categories, events and fields, never a raw CLI payload',async () => {
    const item={...record(),diagnostic:{...record().diagnostic,
      category:'PRIVATE_SENTINEL',event_types:['error','PRIVATE_SENTINEL'],stderr:'PRIVATE_SENTINEL',stdout:'PRIVATE_SENTINEL',
    }} as unknown as AnalysisExecutionDiagnostic;
    await render({...snapshot(),execution_diagnostics:[item]});
    const text=disclosure('Diagnóstico da execução')?.textContent;
    expect(text).toContain('Categoria não reconhecida');
    expect(text).toContain('Eventos do CLI: error.');
    expect(container.textContent).not.toContain('PRIVATE_SENTINEL');
  });
});
