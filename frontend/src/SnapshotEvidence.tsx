import { Articles, Badge } from './components';
import { accessLabels, evidenceCaption, evidenceTitle, PreservedContent, recordLabels, SourceOriginal, sourceName } from './EvidenceCitation';
import { date, label } from './format';
import type { Article, SnapshotEvidence } from './types';

export function AssetEvidenceSources({evidence,articles,referencedIds=[],hasAnalysis=false,selected}:{evidence:SnapshotEvidence[];articles:Article[];referencedIds?:string[];hasAnalysis?:boolean;selected?:string[]}) {
  const unique=[...new Map(evidence.map(item=>[item.evidence_id,item])).values()];
  const ids=new Set(unique.map(item=>item.evidence_id));
  const missing=[...new Set(referencedIds)].filter(id=>!ids.has(id));
  return <>
    <section className="detail-section" aria-label="Evidências preservadas do bundle">
      <h3>{hasAnalysis?'Evidências do snapshot da análise':'Evidências do bundle disponível'}</h3>
      <p className="meta">Registros preservados com seus IDs e metadados. Publicações atuais aparecem em uma seção separada. Um registro de exposição não transforma uma hipótese em fato.</p>
      {unique.length?<ul className="evidence-list">{unique.map(item=><li key={item.evidence_id} id={`evidence-${item.evidence_id}`} className={selected?.includes(item.evidence_id)?'selected-evidence':undefined}>
        <details className="evidence-disclosure">
          <summary><strong>{evidenceTitle(item)}</strong><small>{recordLabels[item.record_type]} · {evidenceCaption(item)}</small></summary>
          <div className="evidence-content">
          <Badge tone="info">{accessLabels[item.access_level]}</Badge>
          <p className="meta">Fonte · {sourceName(item.source_id)} · <SourceOriginal url={item.url}/></p>
          <PreservedContent item={item}/>
          <details className="citation-provenance"><summary>Proveniência técnica desta revisão</summary><dl className="data-list">
            <div><dt>ID da revisão preservada</dt><dd><code>{item.evidence_id}</code></dd></div>
            <div><dt>ID da fonte</dt><dd><code>{item.source_id}</code></dd></div>
            <div><dt>Publicação</dt><dd>{date(item.published_at,item.timestamp_precision==='instant')}</dd></div>
            <div><dt>Modificação</dt><dd>{date(item.modified_at,item.timestamp_precision==='instant')}</dd></div>
            <div><dt>Primeira descoberta</dt><dd>{date(item.first_seen_at,true)}</dd></div>
            <div><dt>Coleta desta versão</dt><dd>{date(item.fetched_at,true)}</dd></div>
            <div><dt>Disponível para o sistema</dt><dd>{date(item.available_at,true)}</dd></div>
            <div><dt>Data de mercado</dt><dd>{date(item.market_timestamp,item.timestamp_precision==='instant')}</dd></div>
            <div><dt>Precisão temporal</dt><dd>{item.timestamp_precision==='instant'?'Instante':item.timestamp_precision==='date'?'Data':'Desconhecida'}</dd></div>
            <div><dt>Grupo editorial</dt><dd>{item.publisher_group||'Não informado'}</dd></div>
            <div><dt>Publicador original</dt><dd>{item.original_publisher||'Não informado'}</dd></div>
            <div><dt>Entidades</dt><dd>{item.entity_ids.length?item.entity_ids.join(' · '):'Não informadas'}</dd></div>
            <div><dt>Limitações registradas</dt><dd>{item.quality_flags.length?item.quality_flags.map(label).join(' · '):'Nenhuma registrada; isso não comprova qualidade ou causalidade.'}</dd></div>
            <div><dt>Hash do conteúdo</dt><dd><code>{item.content_sha256}</code></dd></div>
          </dl></details>
          </div>
        </details>
      </li>)}</ul>:<p className="muted">Nenhuma evidência preservada foi retornada para este bundle.</p>}
      {missing.map(id=><div id={`evidence-${id}`} key={id} className="notice" tabIndex={-1}><strong>Evidência citada não disponível no snapshot retornado.</strong><details><summary>Referência de auditoria</summary><p>ID {id}. Não foi substituída por notícia atual ou dado estimado.</p></details></div>)}
    </section>
    <section className="detail-section" aria-label="Notícias atuais do ativo">
      <h3>Notícias atuais associadas</h3>
      <p className="meta">Esta lista pode conter revisões posteriores às usadas na análise. As referências da análise apontam exclusivamente para os registros preservados acima.</p>
      <Articles items={articles} selected={selected} anchors={false}/>
    </section>
  </>;
}
