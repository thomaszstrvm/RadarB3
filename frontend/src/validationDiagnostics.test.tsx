import { expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { AnalysisTrackingStatus } from './AnalysisTracking';
import type { TrackingState } from './pollAnalysis';

it('explica orçamento sem expor código técnico e mostra diagnóstico da tentativa', () => {
  const state: TrackingState = {phase:'idle',error:'',snapshot:{asset_id:'synthetic',as_of:'2026-10-26T12:00:00Z',
    queue_position:null,queue_position_reason:null,result_analysis:null,
    job:{id:'job',kind:'analyse',status:'paused',created_at:'2026-10-26T12:00:00Z',error:'local_request_budget_exhausted'},
    validation_diagnostics:[{run_id:'run',attempt:1,recorded_at:'2026-10-26T12:00:00Z',diagnostic:{version:1,stage:'schema',issues:[{code:'schema_invalid',path:'/status',rule:'enum'}]}}]}};
  const html = renderToStaticMarkup(<AnalysisTrackingStatus state={state} onRefresh={()=>{}} openJobs={()=>{}}/>);
  expect(html).toContain('limite diário');
  expect(html).not.toContain('local_request_budget_exhausted');
  expect(html).toContain('tentativa 1');
  expect(html).toContain('/status');
  expect(html).toContain('enum');
});
