import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { AnalysisView } from './components';
import { AssetEvidenceSources } from './SnapshotEvidence';
import type { Analysis, SnapshotEvidence } from './types';

// Isolated synthetic contract fixtures; never loaded into the real application or database.
const preserved=(id:string,record_type:SnapshotEvidence['record_type'],content:string):SnapshotEvidence=>({
  evidence_id:id,record_type,source_id:'fixture-source',url:null,title:`Registro ${id}`,content,
  content_sha256:'fixture-hash',access_level:record_type==='article'?'excerpt':'structured',
  published_at:'2026-08-10',modified_at:null,first_seen_at:'2026-08-10T15:00:00Z',fetched_at:'2026-08-10T15:01:00Z',
  available_at:'2026-08-10T15:01:00Z',market_timestamp:'2026-08-10',timestamp_precision:'date',
  publisher_group:'Grupo preservado',original_publisher:'Publicador preservado',entity_ids:['fixture:ASSET'],quality_flags:['adjustment_unknown'],
});
const analysis:Analysis={analysis_id:'fixture-analysis',asset_id:'fixture:ASSET',as_of:'2026-08-11T15:00:00Z',status:'inconclusive',synthesis:'Hipótese inconclusiva de teste',claims:[{kind:'inference',statement:'Possível relação, não comprovada.',evidence_ids:['price-old','metric-old','article-old']}],scenarios:[],opportunity:{classification:'watch',rationale:'Acompanhar evidências.',evidence_ids:['article-old']},risks:[],missing_evidence:[],data_quality_flags:[],evidence_ids:['price-old','metric-old','article-old']};

describe('Referências ao snapshot imutável da análise',()=>{
 it('resolve cada referência de preço, métrica e artigo antigo exatamente uma vez, sem usar a notícia atual',()=>{
   const evidence=[preserved('price-old','price','{"close":12.34,"currency":"BRL","adjustment":"unknown"}'),preserved('metric-old','computed_metric','{"value":0.12,"unit":"fraction","formula_version":"v1","evidence_ids":["price-old"]}'),preserved('article-old','article','Trecho da revisão original.')];
   const html=renderToStaticMarkup(<><AnalysisView analysis={analysis}/><AssetEvidenceSources evidence={evidence} referencedIds={analysis.evidence_ids} hasAnalysis articles={[{id:'article-1',revision_id:'article-new',title:'Título atualizado',excerpt:'Conteúdo atual, posterior.'},{id:'article-2',revision_id:'article-old',title:'Mesmo ID também na consulta atual'}]}/></>);
   for(const id of analysis.evidence_ids){expect(html).toContain(`href="#evidence-${id}"`);expect(html.match(new RegExp(`id="evidence-${id}"`,'g'))).toHaveLength(1);}
   expect(html).not.toContain('id="evidence-article-new"');
   expect(html).toContain('Trecho da revisão original.');expect(html).toContain('12.34');expect(html).toContain('0.12');expect(html).toContain('formula_version');
   expect(html).toContain('fixture-source');expect(html).toContain('fixture-hash');expect(html).toContain('Publicador preservado');expect(html).toContain('Base de ajuste desconhecida');
   expect(html).toContain('Evidências do snapshot da análise');expect(html).toContain('Notícias atuais associadas');expect(html).toContain('Interpretação');expect(html).not.toContain('Fato observado');
 });
 it('expõe referência ausente sem substituí-la por artigo atual e preserva dados remotos como texto',()=>{
   const html=renderToStaticMarkup(<AssetEvidenceSources evidence={[preserved('article-present','article','<script>executar()</script>')]} referencedIds={['missing','missing']} hasAnalysis articles={[{id:'missing',title:'Notícia atual não comprova o snapshot'}]}/>);
   expect(html.match(/id="evidence-missing"/g)).toHaveLength(1);expect(html).toContain('Evidência citada não disponível');expect(html).toContain('Não foi substituída');expect(html).not.toContain('<script>');expect(html).toContain('&lt;script&gt;');
 });
 it('deduplica IDs retornados e mantém horário ausente para precisão de data',()=>{
   const item=preserved('price-old','price','{"close":null,"missing_reason":"unknown"}');
   const html=renderToStaticMarkup(<AssetEvidenceSources evidence={[item,item]} articles={[]}/>);
   expect(html.match(/id="evidence-price-old"/g)).toHaveLength(1);expect(html).toContain('&quot;close&quot;: null');expect(html).not.toContain('00:00');expect(html).toContain('Evidências do bundle disponível');
 });
});
