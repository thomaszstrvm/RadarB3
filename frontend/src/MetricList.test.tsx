// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MetricList, metricValue } from './MetricList';

let root:Root, container:HTMLDivElement;
beforeEach(()=>{vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT',true);vi.stubGlobal('fetch',vi.fn(()=>{throw Error('Network forbidden');}));container=document.createElement('div');document.body.append(container);root=createRoot(container);});
afterEach(async()=>{await act(async()=>root.unmount());container.remove();expect(fetch).not.toHaveBeenCalled();vi.unstubAllGlobals();});

it.each([
  [-.029,'fraction','-2,90%'], [.307,'annualized_fraction','30,70% anualizados'],
  [0,'fraction','0,00%'], [1.42,'multiple','1,42 vezes'], [-1.532,'dimensionless','-1,532 sem unidade'],
  [null,'fraction','Indisponível'], [NaN,'multiple','Indisponível'], [Infinity,'fraction','Indisponível'],
  [2,'unknown-unit','2 unknown-unit'], [5,undefined,'5'], [.03280599500416326,'fraction','3,28%'],
  [2,'constructor','2 constructor'], [2,'toString','2 toString'], [2,'__proto__','2 __proto__'],
])('formats %s %s without changing meaning',(value,unit,expected)=>{
  expect(metricValue(value as number|null,unit as string|undefined)).toBe(expected);
});

it('does not combine canonical and legacy periods or invent a missing endpoint',async()=>{
  await act(async()=>root.render(<MetricList metrics={[
    {metric_id:'partial',value:.1,start_at:null,end_at:'2026-10-30',start_date:'2026-08-03'},
    {metric_id:'nulls',value:null,start_at:null,end_at:null,start_date:'2026-08-03',end_date:'2026-08-31'},
  ]}/>));
  const rows=container.querySelectorAll('dl > div');
  expect(rows[0].textContent).toContain('Não informada — 30 de out. de 2026');
  expect(container.textContent).not.toContain('ago.');
  expect(rows[1].textContent).toBe('nullsIndisponível');
});

it('preserves modern and historical provenance, absence reasons and input values',async()=>{
  const metrics=[
    {metric_id:'current',value:.1,unit:'fraction',formula_version:'current:v1',start_at:'2026-10-01',end_at:'2026-10-30',formula:'old:ignored',start_date:'2026-09-01'},
    {name:'historical',value:2,unit:'multiple',formula:'legacy:v1',start_date:'2026-08-03',end_date:'2026-08-31'},
    {metric_id:'R63',value:null,unit:'fraction',missing_reason:'insufficient_observations',formula_version:'R63:v1'},
  ];
  const before=JSON.stringify(metrics);
  await act(async()=>root.render(<MetricList metrics={metrics}/>));
  const rows=container.querySelectorAll('dl > div');
  expect(rows[0].textContent).toContain('10,00%');expect(rows[0].textContent).toContain('current:v1');
  expect(rows[0].textContent).toContain('01 de out. de 2026 — 30 de out. de 2026');
  expect(rows[0].textContent).not.toContain('old:ignored');
  expect(rows[1].textContent).toContain('legacy:v1');expect(rows[1].textContent).toContain('03 de ago. de 2026 — 31 de ago. de 2026');
  expect(rows[2].textContent).toBe('R63IndisponívelObservações insuficientesR63:v1');
  expect(JSON.stringify(metrics)).toBe(before);
});
