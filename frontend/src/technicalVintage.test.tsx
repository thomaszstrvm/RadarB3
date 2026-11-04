// @vitest-environment jsdom
import { afterEach,beforeEach,expect,it,vi } from 'vitest';
import { act } from 'react';
import { createRoot,type Root } from 'react-dom/client';
import { AnalysisView,PriceChart } from './components';
import { CurrentPriceSnapshot } from './CurrentPriceSnapshot';
import type { Analysis,PriceSnapshot } from './types';

let root:Root,container:HTMLDivElement;
beforeEach(()=>{vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT',true);vi.stubGlobal('fetch',vi.fn(()=>{throw Error('No network');}));container=document.createElement('div');document.body.append(container);root=createRoot(container);});
afterEach(async()=>{await act(async()=>root.unmount());container.remove();expect(fetch).not.toHaveBeenCalled();vi.unstubAllGlobals();});
const snapshot:PriceSnapshot={vintage_id:'synthetic-capture',fetched_at:'2026-11-01T02:17:43Z',latest_capture_at:'2026-11-01T03:20:00Z',available_at:'2026-11-01T03:21:00Z',response_sha256:'synthetic-public-hash',policy_sha256:'synthetic-policy-hash'};
const analysis:Analysis={schema_version:'1.2',analysis_id:'synthetic',asset_id:'synthetic:DEMO3',as_of:snapshot.available_at,status:'inconclusive',synthesis:'Hipótese.',claims:[],scenarios:[],opportunity:{classification:'watch',rationale:'Contexto limitado.',evidence_ids:[]},risks:[],missing_evidence:[],data_quality_flags:[],evidence_ids:[],signal:{action:'wait',horizon_days:90,as_of:snapshot.available_at,rationale:'Hipótese técnica experimental.',evidence_ids:[],risks:['Reversão.'],counterpoints:['Hipótese de mudança.'],invalidation_conditions:['Revisão.'],method:{policy_id:'technical_alignment_v1',policy_sha256:'hash',experimental:true,horizon_semantics:'conditional_hypothesis_reassessment',eligible_action:'wait'}}};

it.each([30,90])('presents %s as reassessment with the same windows, never calibrated forecast',async horizon=>{
  await act(async()=>root.render(<AnalysisView analysis={{...analysis,signal:{...analysis.signal!,horizon_days:horizon}}}/>));
  expect(container.textContent).toContain(`Reavaliação da hipótese atual em ${horizon} dias`);
  expect(container.textContent).toContain('Heurística técnica experimental');
  expect(container.textContent).toContain('cinco e vinte e um');
  expect(container.textContent).toContain('Sem calibração preditiva');
  expect(container.textContent).toContain('não são votos');
  expect(container.textContent).toContain('Elegibilidade técnica: Aguardar');
  expect(container.querySelectorAll('button,input,form')).toHaveLength(0);
});

it('keeps capture statistics distinct from historical AI and does not infer a direction',async()=>{
  await act(async()=>root.render(<CurrentPriceSnapshot snapshot={snapshot} metrics={[
    {metric_id:'provider_adjusted_change_5',label:'Variação ajustada · cinco pregões',value:.002,unit:'fraction'},
    {metric_id:'provider_adjusted_change_63',value:null,missing_reason:'insufficient_observations'},
  ]}/>));
  expect(container.textContent).toContain('close informado');expect(container.textContent).toContain('adjustedClose');
  expect(container.textContent).toContain('separadas do snapshot da análise IA');
  expect(container.textContent).toContain('Observações insuficientes');
  expect(container.textContent).toContain('não produz sinal de compra ou venda');
  expect(container.textContent).toContain(snapshot.vintage_id);
  expect(container.textContent).toContain('Somente períodos encerrados elegíveis entram nos cálculos');
  expect(container.querySelector('details')?.style.overflowWrap).toBe('anywhere');
  expect(container.textContent).not.toContain('00:00');
});

it('preserves no-capture historical display',async()=>{
  await act(async()=>root.render(<CurrentPriceSnapshot metrics={[]}/>));expect(container.textContent).toBe('');
});

it('shows separate close and adjustedClose columns, with no substitution in chart',async()=>{
  await act(async()=>root.render(<PriceChart prices={[{date:'2026-08-03',close:67.14,adjustedClose:66.82,provider:'brapi',vintage_id:'fixture'}]} events={[]} onSelect={()=>{}}/>));
  expect(container.querySelector('svg title')?.textContent).toContain('67,14');
  await act(async()=>container.querySelector('button')!.click());
  const table=container.querySelector('table')!;
  expect(table.textContent).toContain('Preço informado');expect(table.textContent).toContain('Fechamento ajustado pelo provedor');
  expect(table.textContent).toContain('67,14');expect(table.textContent).toContain('66,82');
});
