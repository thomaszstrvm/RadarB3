// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { AnalysisView } from './components';
import { AssetEvidenceSources } from './SnapshotEvidence';
import type { Analysis, AnalysisSignal, SnapshotEvidence } from './types';

// Synthetic fixtures only: no API, database or model call participates in these tests.
const historical:Analysis = {
  schema_version:'1.0', analysis_id:'fixture-analysis', asset_id:'fixture:DEMO3',
  as_of:'2026-11-03T01:30:00Z', status:'inconclusive', synthesis:'Síntese preservada.',
  claims:[], scenarios:[], opportunity:{classification:'watch',rationale:'Investigar evidências.',evidence_ids:[]},
  risks:[], missing_evidence:[], data_quality_flags:[], evidence_ids:[],
};
const signal = (action:AnalysisSignal['action']):AnalysisSignal => ({
  action, horizon_days:30, as_of:historical.as_of, rationale:'Hipótese condicional apoiada no registro sintético.',
  evidence_ids:['fixture-evidence'], risks:['A exposição pode mudar.'],
  counterpoints:['O efeito pode já estar incorporado.'], invalidation_conditions:['A revisão do documento contradiz a hipótese.'],
});
const current = (action:AnalysisSignal['action']):Analysis => ({
  ...historical, schema_version:'1.1', signal:signal(action), evidence_ids:['fixture-evidence'],
  status:action==='insufficient_data'?'insufficient_data':action==='wait'?'inconclusive':'supported_hypothesis',
  opportunity:{classification:({buy:'investigate',sell:'avoid_for_now',wait:'watch',insufficient_data:'insufficient_data'})[action],rationale:'Investigar evidências.',evidence_ids:[]},
});
let root:Root, container:HTMLDivElement;
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT',true);
  vi.stubGlobal('fetch',vi.fn(() => {throw new Error('Network is forbidden in signal tests');}));
  container=document.createElement('div');document.body.append(container);root=createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());container.remove();
  expect(fetch).not.toHaveBeenCalled();vi.unstubAllGlobals();
});
const render = async (analysis:Analysis) => act(async () => root.render(<AnalysisView analysis={analysis}/>));
const signalSection = () => container.querySelector('section[aria-label="Sinal informativo"]');

describe('Sinal informativo no detalhe do ativo',() => {
  it.each([
    ['buy','Hipótese de compra'],['sell','Hipótese de venda'],['wait','Aguardar'],['insufficient_data','Dados insuficientes'],
  ] as const)('shows %s with the complete explanation and no trading controls',async (action,text) => {
    await render(current(action));
    const section=signalSection();
    expect(section?.querySelector('.badge')?.textContent).toBe(text);
    for (const content of ['Não personalizado','Horizonte: 30 dias de calendário','Justificativa',
      signal(action).rationale,'Riscos do sinal',signal(action).risks[0],'Contrapontos',signal(action).counterpoints[0],
      'O que invalida o sinal',signal(action).invalidation_conditions[0]]) expect(section?.textContent).toContain(content);
    expect(section?.querySelectorAll('button,input,form')).toHaveLength(0);
    expect(container.textContent).toContain('Síntese preservada.');
    expect(container.textContent).not.toMatch(/preço-alvo|\d+% de confiança/i);
  });

  it.each(['investigate','watch','avoid_for_now','insufficient_data'])('keeps v1.0 %s historical without converting opportunity',async classification => {
    await render({...historical,opportunity:{...historical.opportunity,classification}});
    expect(container.textContent).toContain('Análise histórica sem sinal informativo');
    expect(signalSection()).toBeNull();
    expect(container.textContent).not.toMatch(/Comprar|Vender/);
    expect(container.textContent).toContain('Síntese preservada.');
  });

  it('preserves older payloads without schema metadata or signal',async () => {
    const {schema_version:_,...unversioned}=historical;
    await render(unversioned);
    expect(container.textContent).toContain('Análise histórica sem sinal informativo');
    expect(signalSection()).toBeNull();
  });

  it('does not invent a direction or date when a signal is absent',async () => {
    await render({...historical,schema_version:'1.1'});
    expect(container.textContent).toContain('Análise histórica sem sinal informativo');
    expect(container.querySelector('time')).toBeNull();
    expect(container.textContent).not.toContain('Horizonte:');
  });

  it.each(['unknown','toString'])('keeps an unsupported signal action %s unavailable',async action => {
    await render({...current('buy'),signal:{...signal('buy'),action} as AnalysisSignal});
    expect(container.textContent).toContain('Sinal informativo indisponível');
    expect(signalSection()).toBeNull();
    expect(container.textContent).not.toMatch(/Comprar|Vender/);
  });

  it('formats the signal cutoff in São Paulo and keeps the original ISO timestamp',async () => {
    await render(current('wait'));
    const time=signalSection()?.querySelector('time');
    expect(time?.getAttribute('datetime')).toBe('2026-11-03T01:30:00Z');
    expect(time?.textContent).toContain('02 de nov. de 2026');
    expect(time?.textContent).toContain('22:30');
    expect(time?.textContent).toContain('Brasília');
  });

  it('opens the cited snapshot through the existing evidence anchor',async () => {
    const evidence:SnapshotEvidence={
      evidence_id:'fixture-evidence',record_type:'filing',source_id:'fixture-source',url:'https://example.invalid/document',
      title:'Documento sintético',content:'Conteúdo sintético preservado.',content_sha256:'fixture-hash',access_level:'full_text',
      published_at:'2026-11-02T12:00:00Z',modified_at:null,first_seen_at:'2026-11-02T12:05:00Z',
      fetched_at:'2026-11-02T12:05:00Z',market_timestamp:null,timestamp_precision:'instant',publisher_group:null,
      original_publisher:null,entity_ids:['fixture:DEMO3'],quality_flags:[],available_at:'2026-11-02T12:05:00Z',
    };
    const analysis=current('buy');
    await act(async () => root.render(<><AnalysisView analysis={analysis}/><AssetEvidenceSources evidence={[evidence]} articles={[]} referencedIds={analysis.evidence_ids} hasAnalysis/></>));
    const link=signalSection()?.querySelector<HTMLAnchorElement>('a[href="#evidence-fixture-evidence"]');
    const disclosure=container.querySelector<HTMLDetailsElement>('#evidence-fixture-evidence details');
    expect(link).not.toBeNull();expect(disclosure?.open).toBe(false);
    await act(async () => link?.click());
    expect(disclosure?.open).toBe(true);
    expect(container.querySelectorAll('#evidence-fixture-evidence')).toHaveLength(1);
    const source=container.querySelector('a[href="https://example.invalid/document"]');
    expect(source?.getAttribute('rel')).toBe('noopener noreferrer');
    expect(source?.getAttribute('target')).toBe('_blank');
  });

  it('keeps insufficient data explicit even without referenced evidence',async () => {
    const analysis=current('insufficient_data');analysis.signal!.evidence_ids=[];
    await render(analysis);
    expect(signalSection()?.querySelector('.badge')?.textContent).toBe('Dados insuficientes');
    expect(signalSection()?.textContent).toContain('Sem evidência referenciada.');
    expect(signalSection()?.querySelector('a')).toBeNull();
  });

  it('renders source text as text rather than executable markup',async () => {
    const analysis=current('wait');analysis.signal!.rationale='<script>executar()</script>';
    await render(analysis);
    expect(signalSection()?.textContent).toContain('<script>executar()</script>');
    expect(signalSection()?.querySelector('script')).toBeNull();
  });
});
