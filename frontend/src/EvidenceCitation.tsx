import { createContext, useContext, useMemo, useState, type MouseEvent, type ReactNode } from 'react';
import newsSources from '../../config/news-sources.json';
import { date, safeUrl } from './format';
import type { SnapshotEvidence } from './types';

const sources = new Map<string, string>(newsSources.sources.map(source => [source.id, source.name]));
sources.set('deterministic_metrics_v1', 'Cálculo do Radar B3');
sources.set('brapi', 'brapi');
export const recordLabels = {
  article: 'Publicação preservada', filing: 'Documento preservado', price: 'Registro de preço',
  macro: 'Registro macroeconômico', computed_metric: 'Métrica calculada pelo sistema', exposure: 'Registro de exposição',
};
export const accessLabels = {headline_only:'Somente título',excerpt:'Trecho disponível',full_text:'Texto integral',structured:'Dados estruturados'};

export function sourceName(id?: string | null): string {
  return id ? sources.get(id) || id.replaceAll('_', ' ') : 'Fonte não informada';
}

export function evidenceCaption(item: SnapshotEvidence) {
  const publisher = item.original_publisher || sourceName(item.source_id);
  const stamp = item.published_at ? date(item.published_at)
    : item.market_timestamp ? 'Mercado em ' + date(item.market_timestamp)
    : item.record_type === 'article' || item.record_type === 'filing' ? 'Publicação sem data'
    : 'Coleta em ' + date(item.fetched_at);
  return publisher + ' · ' + stamp;
}

export function evidenceTitle(item: SnapshotEvidence) {
  return item.title || recordLabels[item.record_type];
}

const EvidenceIndex = createContext<ReadonlyMap<string, SnapshotEvidence>>(new Map());
export function SnapshotCitations({evidence, children}: {evidence: SnapshotEvidence[]; children: ReactNode}) {
  const byId = useMemo(() => new Map(evidence.map(item => [item.evidence_id, item])), [evidence]);
  return <EvidenceIndex.Provider value={byId}>{children}</EvidenceIndex.Provider>;
}

export function EvidenceLink({id, number, audit = false}: {id: string; number?: number; audit?: boolean}) {
  const item = useContext(EvidenceIndex).get(id);
  function openRevision(event: MouseEvent<HTMLAnchorElement>) {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return;
    const target = document.getElementById('evidence-' + id);
    if (!target) return;
    event.preventDefault();
    const disclosure = target.querySelector('details');
    if (disclosure) disclosure.open = true;
    target.scrollIntoView?.({block: 'start'});
    (disclosure?.querySelector('summary') || target).focus({preventScroll: true});
    window.history.replaceState(window.history.state, '', event.currentTarget.hash);
  }
  return <a className={audit ? 'citation-record' : 'snapshot-citation'} href={'#evidence-' + id} onClick={openRevision}>
    {audit ? 'Ver registro de auditoria' : item ? <>{evidenceTitle(item)}<small>{evidenceCaption(item)}</small></> : 'Registro indisponível' + (number ? ' (' + number + ')' : '')}
  </a>;
}

export function SourceOriginal({url}: {url: string | null}) {
  const href = safeUrl(url);
  return href ? <a href={href} target="_blank" rel="noopener noreferrer">Abrir fonte original<span className="sr-only"> (abre em nova aba)</span></a>
    : <span className="meta">Sem link original disponível.</span>;
}

export function PreservedContent({item, preview = false}: {item: SnapshotEvidence; preview?: boolean}) {
  if (!item.content) return <p>Conteúdo indisponível nesta revisão.</p>;
  if (item.access_level === 'structured') {
    if (preview && item.content.length > 4000) return <p>Consulte o registro de auditoria para ver a série ou o documento estruturado completo.</p>;
    let content = item.content;
    try { content = JSON.stringify(JSON.parse(content), null, 2); } catch { /* Keep a non-JSON record verbatim. */ }
    return <><p className="meta">Campos e valores preservados</p><pre>{content}</pre></>;
  }
  const shorten = preview && item.content.length > 600;
  return <>{(!preview || shorten) && <p className="meta">{shorten ? 'Início do conteúdo preservado' : 'Conteúdo preservado da fonte'}</p>}
    <p className="citation-text">{shorten ? item.content.slice(0, 600) + '…' : item.content}</p></>;
}

export function InlineCitation({id, number}: {id: string; number?: number}) {
  const item = useContext(EvidenceIndex).get(id);
  const [open, setOpen] = useState(false);
  if (!item) return <EvidenceLink id={id} number={number}/>;
  return <details className="citation-inline" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>Conferir fonte · {evidenceTitle(item)}<small>{evidenceCaption(item)}</small></summary>
    {open && <div className="citation-preview">
      <p className="meta">{recordLabels[item.record_type]} · {accessLabels[item.access_level]}</p>
      <PreservedContent item={item} preview/>
      <div className="citation-actions"><SourceOriginal url={item.url}/><EvidenceLink id={id} audit/></div>
    </div>}
  </details>;
}
