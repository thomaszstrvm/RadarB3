// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { AnalysisTrackingStatus } from './AnalysisTracking';
import type { TrackingState } from './pollAnalysis';
import type { AnalysisValidationIssue } from './types';

const issue = ():AnalysisValidationIssue => ({
  code:'factual_number_not_in_cited_evidence',path:'/claims/0/statement',rule:null,
});
let root:Root,container:HTMLDivElement;
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT',true);
  vi.stubGlobal('fetch',vi.fn(() => {throw new Error('Network forbidden in numeric diagnostic DOM tests');}));
  container=document.createElement('div');document.body.append(container);root=createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());container.remove();expect(fetch).not.toHaveBeenCalled();vi.unstubAllGlobals();
});
async function render(value:AnalysisValidationIssue) {
  const state:TrackingState={phase:'idle',error:'',snapshot:{
    asset_id:'qa:DEMO3',as_of:'2026-10-31T04:30:00Z',queue_position:null,queue_position_reason:null,result_analysis:null,
    job:{id:'qa-numeric-job',kind:'analyse',status:'failed',created_at:'2026-10-31T04:25:00Z',error:'cli_analysis_validation_rejected'},
    validation_diagnostics:[{run_id:'qa-numeric-run',attempt:1,recorded_at:'2026-10-31T04:29:00Z',
      diagnostic:{version:1,stage:'semantic',issues:[value]}}],
  }};
  await act(async () => root.render(<AnalysisTrackingStatus state={state} onRefresh={()=>{}} openJobs={()=>{}}/>));
}

describe('Causa numérica enumerada sem revelar conteúdo rejeitado',() => {
  it.each([
    ['unsupported_format','Formato numérico não suportado'],
    ['unsupported_date','Data sem suporte no conteúdo citado'],
    ['unit_mismatch','Unidade, escala ou moeda divergente'],
    ['quantity_not_found','Quantidade não encontrada na evidência citada'],
    ['filing_account_unrecognized','Conta do demonstrativo não reconhecida na afirmação'],
    ['filing_account_ambiguous','Mais de uma conta do demonstrativo identificada na afirmação'],
    ['filing_period_unrecognized','Período do demonstrativo não reconhecido na afirmação'],
    ['filing_period_ambiguous','Mais de um período do demonstrativo identificado na afirmação'],
    ['filing_period_incompatible','Período incompatível com a observação citada do demonstrativo'],
  ] as const)('shows the approved description for %s',async (numeric_reason,description) => {
    await render({...issue(),numeric_reason});
    const detail=container.querySelector('details');
    expect(detail?.open).toBe(false);
    await act(async () => detail?.querySelector('summary')?.click());
    expect(detail?.open).toBe(true);
    expect(detail?.querySelector('summary')?.textContent).toBe('Diagnóstico da validação · tentativa 1');
    expect(detail?.querySelector('li')?.textContent).toContain(`Causa: ${description}.`);
    expect(detail?.textContent).toContain('/claims/0/statement');
    expect(detail?.textContent).toContain('Consistência com as evidências');
    expect(container.textContent).not.toContain('Diagnóstico da execução');
    expect(container.textContent).not.toContain(numeric_reason);
  });

  it.each(['PRIVATE_SENTINEL 999 <script>executar()</script>','toString','constructor'])('omits unknown or private reason %s',async numeric_reason => {
    await render({...issue(),numeric_reason,rejected_value:'PRIVATE_SENTINEL 999',token_hash:'PRIVATE_SENTINEL'} as unknown as AnalysisValidationIssue);
    expect(container.textContent).not.toContain('Causa:');
    expect(container.textContent).not.toContain(numeric_reason);
    expect(container.textContent).not.toContain('PRIVATE_SENTINEL');
    expect(container.textContent).not.toContain('999');
    expect(container.querySelector('script')).toBeNull();
    expect(container.textContent).toContain('Regra: factual_number_not_in_cited_evidence.');
  });

  it('does not infer a reason for a historical diagnostic without the new field',async () => {
    await render(issue());
    expect(container.querySelector('li')?.textContent).toBe('Regra: factual_number_not_in_cited_evidence. Campo: /claims/0/statement.');
    expect(container.textContent).not.toContain('Causa:');
  });

  it('does not attach a numeric reason to an unrelated validation rule',async () => {
    await render({code:'schema_invalid',path:'/status',rule:'enum',numeric_reason:'unsupported_format'});
    expect(container.querySelector('li')?.textContent).toBe('Regra: schema_invalid. Campo: /status. Contrato: enum.');
    expect(container.textContent).not.toContain('Formato numérico não suportado');
  });
});
