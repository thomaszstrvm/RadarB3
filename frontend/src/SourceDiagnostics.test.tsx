// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { SourceDiagnostics, sourceLimitations } from './Sources';
import type { Source } from './types';

const captureReason = 'Captura histórica preservada; base de ajuste não verificada.';
const recentError = 'Limite de requisições (HTTP 429)';
const source: Source = {id:'fixture-prices',name:'Preços de teste',status:'partial',reason:captureReason,error:'http_429'};
let container: HTMLDivElement, root: Root;
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT',true);
  vi.stubGlobal('fetch',vi.fn(() => {throw Error('Network forbidden');}));
  container=document.createElement('div'); document.body.append(container); root=createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount()); container.remove();
  expect(fetch).not.toHaveBeenCalled(); vi.unstubAllGlobals();
});

it('keeps the capture limitation and the distinct recent source error', () => {
  expect(sourceLimitations(source)).toEqual([captureReason,recentError]);
});
it('does not duplicate an identical reason and error', () => {
  expect(sourceLimitations({...source,reason:'http_429'})).toEqual([recentError]);
});
it('deduplicates equal rendered labels even when the source codes differ', () => {
  expect(sourceLimitations({...source,reason:'available',error:'success'})).toEqual(['Disponível']);
});
it.each([
  {reason:'timeout',error:null},
  {reason:null,error:'timeout'},
  {reason:'',error:'timeout'},
])('keeps the available explanation with optional fields %j', fields => {
  expect(sourceLimitations({...source,...fields})).toEqual(['Tempo limite de resposta excedido']);
});
it('keeps the fallback when neither field is available', () => {
  expect(sourceLimitations({id:'empty',name:'Fonte sem diagnóstico'})).toEqual(['Cobertura histórica não confirmada. Consulte os detalhes da fonte.']);
});
it('preserves the RSS caveat alongside capture and recent error explanations', () => {
  expect(sourceLimitations({...source,details:{historical_coverage:'feed_window_only'}})).toEqual([
    'RSS recente; cobertura de três meses não garantida.',captureReason,recentError,
  ]);
});
it.each(['.source-desktop','.source-mobile'])('shows both explanations outside collapsed details in %s', async selector => {
  await act(async () => root.render(<SourceDiagnostics items={[source]}/>));
  const limitation=container.querySelector(selector+' .source-limitation')!;
  expect(Array.from(limitation.querySelectorAll('p'),p=>p.textContent)).toEqual([captureReason,recentError]);
  expect(limitation.closest('details')).toBeNull();
  expect(container.querySelector(selector)?.textContent).toContain('Parcial');
});
it.each(['.source-desktop','.source-mobile'])('renders an identical reason and error once in %s', async selector => {
  await act(async () => root.render(<SourceDiagnostics items={[{...source,reason:'http_429'}]}/>));
  const messages=container.querySelectorAll(selector+' .source-limitation p');
  expect(Array.from(messages,p=>p.textContent)).toEqual([recentError]);
});
it.each(['.source-desktop','.source-mobile'])('renders the newly visible error as text, not markup, in %s', async selector => {
  const error='<img src=x onerror=alert(1)>';
  await act(async () => root.render(<SourceDiagnostics items={[{...source,error}]}/>));
  const limitation=container.querySelector(selector+' .source-limitation')!;
  expect(limitation.textContent).toContain(error);
  expect(limitation.querySelector('img')).toBeNull();
});

const invalidClockCode = 'provider_state_clock_invalid';
const invalidClockLabel = 'Registro operacional com horário inválido ou futuro; diagnóstico não confirmado nesse corte.';
const invalidClockFields = [
  {reason:invalidClockCode,error:null},
  {reason:null,error:invalidClockCode},
  {reason:invalidClockCode,error:invalidClockCode},
];
it.each(invalidClockFields)('explains an untrusted provider clock without duplication for %j', fields => {
  expect(sourceLimitations({...source,...fields})).toEqual([invalidClockLabel]);
});
for (const selector of ['.source-desktop','.source-mobile']) {
  it.each(invalidClockFields)(`shows the invalid-clock caveat with sanitized diagnostics in ${selector} for %j`, async fields => {
    // This is the sanitized API contract, not a client-side clock validator.
    const sanitized: Source = {
      id:'fixture-clock',name:'Fonte com relógio não confirmado',status:'not_tested',
      ...fields,last_success_at:null,last_attempt_at:null,details:{},
    };
    await act(async () => root.render(<SourceDiagnostics items={[sanitized]}/>));
    const layout=container.querySelector(selector)!;
    const limitation=layout.querySelector('.source-limitation')!;
    expect(Array.from(limitation.querySelectorAll('p'),p=>p.textContent)).toEqual([invalidClockLabel]);
    expect(limitation.closest('details')).toBeNull();
    expect(layout.textContent).toContain('Não testada');
    expect(layout.textContent).not.toContain('Disponível');
    expect(layout.textContent).not.toContain('Limite de requisições');
    expect(layout.textContent).not.toContain(captureReason);
    expect(layout.querySelector('.source-diagnostics pre')?.textContent).toContain('"details": {}');
  });
}
const invalidCaptureLabel = 'Captura de preços inválida; dados indisponíveis até uma nova captura válida.';
it.each(['.source-desktop','.source-mobile'])('shows an invalid capture without converting a missing count to zero in %s', async selector => {
  const invalid: Source = {id:'fixture-invalid-capture',name:'Captura inválida',status:'unavailable',
    reason:'price_capture_invalid',error:'price_capture_invalid',count:null,item_count:null,
    available_start:null,available_end:null,last_success_at:null,details:{}};
  await act(async () => root.render(<SourceDiagnostics items={[invalid]}/>));
  const layout=container.querySelector(selector)!;
  expect(Array.from(layout.querySelectorAll('.source-limitation p'),p=>p.textContent)).toEqual([invalidCaptureLabel]);
  expect(layout.querySelector('.source-limitation')?.closest('details')).toBeNull();
  const count=selector==='.source-desktop'
    ? layout.querySelector('tbody tr td:nth-child(4)')!.firstChild!.textContent
    : Array.from(layout.querySelectorAll('dt')).find(dt=>dt.textContent==='Itens registrados')!.nextElementSibling!.textContent;
  expect(count).toBe('Indisponível');
  expect(count).not.toBe('0');
  expect(layout.textContent).toContain('Intervalo não informado');
  expect(layout.textContent).not.toContain(captureReason);
});