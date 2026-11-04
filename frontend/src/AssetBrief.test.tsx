// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { AssetBrief } from './AssetBrief';
import { SnapshotCitations } from './EvidenceCitation';
import { AssetEvidenceSources } from './SnapshotEvidence';
import { AnalysisView, EvidenceRefs } from './components';
import type { AssetDetail, SnapshotEvidence } from './types';

const old: SnapshotEvidence = {
  evidence_id:'document-version-1', record_type:'article', source_id:'fin_infomoney', url:null,
  title:'Companhia divulga comunicado inicial', content:'Trecho da versão inicial.', content_sha256:'sha-of-old-content',
  access_level:'excerpt', published_at:'2026-10-20', modified_at:null, first_seen_at:'2026-10-20T10:00:00Z',
  fetched_at:'2026-10-20T11:00:00Z', available_at:'2026-10-20T11:00:00Z', timestamp_precision:'date',
  market_timestamp:null, publisher_group:null, original_publisher:null, entity_ids:[], quality_flags:[],
};
const article = {id:'document', revision_id:'document-version-2', title:'Companhia amplia comunicado', source:'InfoMoney', published_at:'2026-10-22'};
const asset: AssetDetail = {
  id:'fixture', ticker:'TEST3', name:'Teste', price:null, return_21:null, watchlisted:false, prices:[], metrics:[],
  events:[{id:'event',title:article.title,first_disclosed_at:'2026-10-19',articles:[article]}],articles:[article],
  evidence:[old], analysis:{analysis_id:'fixture-analysis',asset_id:'fixture',as_of:'2026-10-21T18:00:00Z',
    status:'insufficient_data', synthesis:'Sem confirmação.', opportunity:{classification:'watch',rationale:'',evidence_ids:[old.evidence_id]},
    claims:[],scenarios:[],risks:[],missing_evidence:['Falta confirmação independente.'],data_quality_flags:[],evidence_ids:[old.evidence_id]},
};
let container: HTMLDivElement, root: Root;
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT',true);
  container=document.createElement('div'); document.body.append(container); root=createRoot(container);
  history.replaceState(null,'','/?view=market&asset=fixture');
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals(); });

async function render(detail: AssetDetail, showCurrentEvent = true) {
  const ids=detail.analysis?.evidence_ids || [];
  await act(async () => root.render(<SnapshotCitations evidence={detail.evidence || []}>
    <AssetBrief asset={detail} showCurrentEvent={showCurrentEvent}/><EvidenceRefs ids={ids}/>
    <AssetEvidenceSources evidence={detail.evidence || []} articles={detail.articles} referencedIds={ids} hasAnalysis={!!detail.analysis}/>
  </SnapshotCitations>));
}

it('separa a divulgação e a notícia atuais da revisão citada e navega até sua auditoria', async () => {
  await render(asset);
  const brief=container.querySelector('.asset-brief')!;
  expect(brief.textContent).toContain(article.title);
  expect(brief.textContent).toContain('Primeira divulgação em 19 de out.');
  expect(brief.textContent).toContain('Dados insuficientes');
  expect(brief.textContent).toContain('Falta confirmação independente.');
  for(const summary of container.querySelectorAll('.citation-inline > summary')){
    expect(summary.textContent).toContain(old.title);
    expect(summary.textContent).toContain('InfoMoney · 20 de out.');
    expect(summary.textContent).not.toContain('document-version');
  }
  await act(async () => { brief.querySelector('summary')!.click(); await new Promise(resolve => setTimeout(resolve, 0)); });
  expect(brief.querySelector('.citation-preview')?.textContent).toContain(old.content);
  const link=brief.querySelector<HTMLAnchorElement>('a.citation-record')!;
  const details=container.querySelector<HTMLDetailsElement>('#evidence-document-version-1 details')!;
  expect(details.open).toBe(false);
  await act(async () => link.click());
  expect(details.open).toBe(true);
  expect(document.activeElement).toBe(details.querySelector('summary'));
  expect(location.search).toBe('?view=market&asset=fixture');
  expect(details.textContent).toContain(old.evidence_id);
  expect(details.textContent).toContain(old.content_sha256);
  expect(details.textContent).not.toContain(article.title);
});

it('explicita ausência de análise e permite que o evento de origem substitua o destaque atual', async () => {
  await render({...asset,analysis:null}, false);
  const brief=container.querySelector('.asset-brief')!;
  expect(brief.textContent).toContain('Sem análise validada');
  expect(brief.textContent).not.toContain(article.title);
  expect(brief.querySelector('.snapshot-citation')).toBeNull();
});

it('aponta para a lacuna quando a revisão citada está ausente, mesmo com uma notícia atual', async () => {
  await render({...asset,evidence:[]});
  const link=container.querySelector<HTMLAnchorElement>('.asset-brief a.snapshot-citation')!;
  expect(link.textContent).toBe('Registro indisponível');
  await act(async () => link.click());
  expect(document.activeElement?.closest('.notice')?.textContent).toContain('Evidência citada não disponível');
});

it('permite conferir a revisão de cada afirmação no lugar e preserva a âncora de auditoria', async () => {
  const reviewed = {...old, url:'https://example.invalid/edicao-original'};
  const other = {...old, evidence_id:'outra-revisao', url:'javascript:alert(1)', title:'Outra fonte',
    content:'<script>conteúdo citado como texto</script>'};
  const records = [reviewed, other];
  const analysis = {...asset.analysis!, evidence_ids:records.map(record => record.evidence_id), claims:[
    {kind:'fact', statement:'Afirmação observada.', evidence_ids:[reviewed.evidence_id]},
    {kind:'inference', statement:'Interpretação condicional.', evidence_ids:[other.evidence_id]},
  ]};
  await act(async () => root.render(<SnapshotCitations evidence={records}><AnalysisView analysis={analysis}/>
    <AssetEvidenceSources evidence={records} articles={[article]} hasAnalysis referencedIds={analysis.evidence_ids}/>
  </SnapshotCitations>));
  const [fact, inference] = container.querySelectorAll<HTMLElement>('.claim');
  async function toggle(claim: HTMLElement) {
    const summary = claim.querySelector('summary')!;
    summary.focus();
    await act(async () => { summary.click(); await new Promise(resolve => setTimeout(resolve, 0)); });
    return summary;
  }
  const summary = await toggle(fact);
  expect(fact.textContent).toContain('Fato observado');
  expect(fact.textContent).toContain('InfoMoney · 20 de out.');
  expect(fact.textContent).toContain('Trecho disponível');
  expect(fact.textContent).toContain(old.content);
  expect(fact.textContent).not.toContain(article.title);
  expect(fact.textContent).not.toContain(other.title);
  expect(fact.querySelector('a[target="_blank"]')?.getAttribute('href')).toBe(reviewed.url);
  expect(location.hash).toBe('');
  await toggle(fact);
  expect(document.activeElement).toBe(summary);
  expect(fact.querySelector('.citation-preview')).toBeNull();
  await toggle(inference);
  expect(inference.textContent).toContain('Interpretação');
  expect(inference.textContent).toContain(other.content);
  expect(inference.querySelector('script,a[target="_blank"]')).toBeNull();
  expect(inference.textContent).toContain('Sem link original disponível.');
  const audit = inference.querySelector<HTMLAnchorElement>('.citation-record')!;
  await act(async () => audit.click());
  expect(location.hash).toBe('#evidence-outra-revisao');
  expect(document.activeElement?.closest('li')?.id).toBe('evidence-outra-revisao');
  expect(container.querySelectorAll('#evidence-outra-revisao')).toHaveLength(1);
});

it('separa o estado inconclusivo do sinal informativo e preserva a hora de corte em Brasília', async () => {
  const analysis=asset.analysis!;
  const signal={action:'wait' as const, as_of:analysis.as_of, horizon_days:30, rationale:'Aguardar divulgação.',
    evidence_ids:[], risks:[], counterpoints:[], invalidation_conditions:[]};
  await render({...asset,analysis:{...analysis,status:'inconclusive',signal}});
  const brief=container.querySelector('.asset-brief')!;
  expect(brief.querySelector('.brief-conclusion')?.textContent).toBe('Inconclusiva');
  expect(brief.textContent).toContain('Sinal informativo · Aguardar');
  expect(brief.querySelector('time')?.textContent).toContain('15:00 · Brasília');
  await render({...asset,analysis:{...analysis,as_of:'2026-10-21',signal:{...signal,action:'insufficient_data'}}});
  expect(brief.textContent?.match(/Dados insuficientes/g)).toHaveLength(1);
  expect(brief.querySelector('time')?.textContent).not.toContain('Brasília');
});
