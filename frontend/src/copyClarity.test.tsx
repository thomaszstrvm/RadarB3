import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { AssetBrief } from './AssetBrief';
import { AnalysisView } from './components';
import { PreservedContent } from './EvidenceCitation';
import type { Analysis, AnalysisSignal, AssetDetail, SnapshotEvidence } from './types';

beforeEach(() => vi.stubGlobal('fetch',vi.fn(() => { throw Error('Network forbidden in copy fixtures'); })));
afterEach(() => { expect(fetch).not.toHaveBeenCalled(); vi.unstubAllGlobals(); });
const signal=(action:AnalysisSignal['action']):AnalysisSignal=>({action,horizon_days:30,as_of:'2026-11-01T04:06:00Z',
  rationale:'Hipótese sintética.',evidence_ids:[],risks:['Pode estar errada.'],counterpoints:[],invalidation_conditions:[]});
const analysis=(value:AnalysisSignal):Analysis=>({analysis_id:'fixture-analysis',asset_id:'fixture',as_of:value.as_of,
  status:'inconclusive',synthesis:'Sem confirmação.',claims:[],scenarios:[],opportunity:{classification:'watch',rationale:'',evidence_ids:[]},
  risks:[],missing_evidence:['Falta evidência.'],data_quality_flags:[],evidence_ids:[],signal:value});
const asset=(value:Analysis):AssetDetail=>({id:'fixture',ticker:'TEST3',name:'Teste',price:null,return_21:null,
  watchlisted:false,prices:[],events:[],articles:[],analysis:value,metrics:[]});
const evidence=(content:string):SnapshotEvidence=>({evidence_id:'fixture-doc',record_type:'article',source_id:'fixture',
  url:null,title:'Documento de teste',content,content_sha256:'fixture-hash',access_level:'excerpt',published_at:'2026-10-29',
  modified_at:null,first_seen_at:'2026-10-29T18:00:00Z',fetched_at:'2026-10-29T18:00:00Z',available_at:'2026-10-29T18:00:00Z',
  market_timestamp:null,timestamp_precision:'date',publisher_group:null,original_publisher:null,entity_ids:[],quality_flags:[]});

it.each([['buy','Hipótese de compra'],['sell','Hipótese de venda'],['wait','Aguardar'],['insufficient_data','Dados insuficientes']] as const)(
  'uses the same %s label in summary and complete explanation', (action,text)=>{
    const value=analysis(signal(action));
    expect(renderToStaticMarkup(<AssetBrief asset={asset(value)}/>)).toContain(text);
    expect(renderToStaticMarkup(<AnalysisView analysis={value}/>)).toContain(text);
  });
it('qualifies a confirmed experimental method at the first signal without merging conclusion and action',()=>{
  const value=signal('wait');value.method={policy_id:'technical_alignment_v1',policy_sha256:'fixture',experimental:true,
    horizon_semantics:'conditional_hypothesis_reassessment',eligible_action:'wait'};
  const html=renderToStaticMarkup(<AssetBrief asset={asset(analysis(value))}/>);
  expect(html).toContain('Hipótese experimental · não personalizada');
  expect(html).toContain('Inconclusiva');expect(html).toContain('Sinal informativo · Aguardar');
  expect(html).toContain('Reavaliação em 30 dias');expect(html).toContain('Falta evidência.');
});
it('does not assign an experimental method to a historical signal',()=>{
  const html=renderToStaticMarkup(<AssetBrief asset={asset(analysis(signal('wait')))}/>);
  expect(html).not.toContain('Hipótese experimental');expect(html).toContain('Horizonte de 30 dias');
});
it('removes only the redundant inline caption and preserves the full audit caption and literal content',()=>{
  const item=evidence('Texto original sem modificação.');
  const preview=renderToStaticMarkup(<PreservedContent item={item} preview/>);
  expect(preview).not.toContain('Conteúdo preservado da fonte');expect(preview).toContain(item.content);
  const full=renderToStaticMarkup(<PreservedContent item={item}/>);
  expect(full).toContain('Conteúdo preservado da fonte');expect(full).toContain(item.content);
});
it('retains the truncation warning for long inline content',()=>{
  const item=evidence('x'.repeat(601));const html=renderToStaticMarkup(<PreservedContent item={item} preview/>);
  expect(html).toContain('Início do conteúdo preservado');expect(html).toContain('x'.repeat(600)+'…');
  expect(html).not.toContain(item.content);
});
it('retains the structured-data label in the preview',()=>{
  const item={...evidence('{"value":null}'),access_level:'structured' as const};
  expect(renderToStaticMarkup(<PreservedContent item={item} preview/>)).toContain('Campos e valores preservados');
});
