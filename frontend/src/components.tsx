import { useState, type ReactNode } from 'react';
import { date, label, money, safeUrl, signalActionLabels, isExperimentalSignal } from './format';
import { InlineCitation } from './EvidenceCitation';
import type { Analysis, AnalysisSignal, Article, MarketEvent, Price } from './types';
export function Icon({name,size=20}:{name:string;size?:number}){const paths:Record<string,ReactNode>={grid:<><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></>,chart:<><path d="M3 3v18h18M6 15l4-5 4 3 6-8"/></>,events:<><path d="M4 5h16v15H4zM8 2v6M16 2v6M4 10h16M8 14h3M8 17h7"/></>,star:<path d="m12 3 2.8 5.8 6.4.9-4.6 4.5 1.1 6.3L12 17.5l-5.7 3 1.1-6.3-4.6-4.5 6.4-.9Z"/>,sources:<><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c5 6 5 12 0 18-5-6-5-12 0-18Z"/></>,settings:<><path d="M4 7h16M4 17h16"/><circle cx="8" cy="7" r="3"/><circle cx="16" cy="17" r="3"/></>,jobs:<><circle cx="12" cy="12" r="9"/><path d="M12 7v5l4 2"/></>,bell:<><path d="M5 17h14l-2-4V9a5 5 0 0 0-10 0v4ZM10 21h4"/></>,refresh:<><path d="M20 7v5h-5M4 17v-5h5M5 8a8 8 0 0 1 13-3l2 3M4 16l2 3a8 8 0 0 0 13-3"/></>,search:<><circle cx="10" cy="10" r="6"/><path d="m15 15 6 6"/></>,close:<path d="m6 6 12 12M18 6 6 18"/>,arrow:<path d="M5 12h14m-5-5 5 5-5 5"/>,download:<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>,check:<path d="m5 12 4 4L19 6"/>};return <svg aria-hidden="true" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">{paths[name]||paths.grid}</svg>}
export function Badge({children,tone='neutral'}:{children:ReactNode;tone?:string}){return <span className={`badge ${tone}`}>{children}</span>}
export function Empty({title,children,action}:{title:string;children?:ReactNode;action?:ReactNode}){return <div className="empty"><span className="empty-symbol"><Icon name="chart" size={28}/></span><h3>{title}</h3><div className="muted text-reading">{children}</div>{action&&<div className="empty-action">{action}</div>}</div>}
export function State({loading,error,retry}:{loading:boolean;error:string;retry?:()=>void}){return <>{loading&&<p className="loading" role="status">Carregando dados locais…</p>}{error&&<div className="notice danger" role="alert"><strong>Não foi possível obter os dados.</strong><p>{error}</p>{retry&&<button onClick={retry}>Tentar novamente</button>}</div>}</>}
export function External({href,children}:{href?:string;children:ReactNode}){const url=safeUrl(href);return url?<a href={url} target="_blank" rel="noopener noreferrer">{children}<span className="sr-only"> (abre em nova aba)</span></a>:<span>{children}</span>}
export function Articles({items,selected,anchors=true}:{items:Article[];selected?:string[];anchors?:boolean}){return items.length?<ul className="evidence-list">{items.map(a=><li id={anchors?`evidence-${a.revision_id||a.id}`:undefined} key={a.id} className={selected?.includes(a.revision_id||a.id)?'selected-evidence':''}><External href={a.url||a.canonical_url}>{a.title}</External><p className="meta">{a.source||a.source_id||'Fonte não informada'} · {date(a.published_at)} · {label(a.access_level||'unknown')}</p>{(a.summary||a.excerpt)&&<p>{a.summary||a.excerpt}</p>}<small>ID {a.revision_id||a.id}{a.version?` · versão ${a.version}`:''} · Coleta {date(a.collected_at||a.fetched_at,true)}</small></li>)}</ul>:<p className="muted">Nenhuma evidência coletada para este item.</p>}
export function EvidenceRefs({ids}: {ids?: string[]}) {
  const unique = [...new Set(ids)];
  if (!unique.length) return <p className="meta">Sem evidência referenciada.</p>;
  const links = unique.map((id, index) => <li key={id}><InlineCitation id={id} number={index + 1}/></li>);
  return <div className="evidence-refs"><ul className="citation-list" aria-label="Fontes citadas">{links.slice(0, 2)}</ul>
    {links.length > 2 && <details className="more-citations"><summary>Outras fontes citadas ({links.length - 2})</summary>
      <ul className="citation-list">{links.slice(2)}</ul></details>}
  </div>;
}
function InformativeSignal({signal}:{signal?:AnalysisSignal}) {
  if (!signal) return <p className="signal-unavailable muted">Análise histórica sem sinal informativo</p>;
  if (!Object.hasOwn(signalActionLabels,signal.action)) return <p className="signal-unavailable muted">Sinal informativo indisponível</p>;
  const actionLabel = signalActionLabels[signal.action];
  const experimental = isExperimentalSignal(signal);
  return <section className="informative-signal" aria-label="Sinal informativo">
    <div className="section-heading"><h3>Sinal informativo</h3><Badge>{actionLabel}</Badge></div>
    <p className="meta">Não personalizado · {experimental?'Reavaliação da hipótese atual em':'Horizonte:'} {signal.horizon_days} dias de calendário</p>
    {experimental&&<div className="notice"><strong>Heurística técnica experimental</strong><p>Janelas de cinco e vinte e um pregões, para ambos os horizontes. Sem calibração preditiva, probabilidade ou previsão de retorno. Fundamentos e notícias fornecem contexto e contrapontos; não são votos.</p><p>Elegibilidade técnica: {signalActionLabels[signal.method!.eligible_action]||'Indisponível'}. A interpretação pode reduzir a conclusão.</p></div>}
    <p className="meta">Dados até <time dateTime={signal.as_of}>{date(signal.as_of,true)}</time></p>
    <h4>Justificativa</h4><p>{signal.rationale}</p>
    <EvidenceRefs ids={signal.evidence_ids}/>
    <h4>Riscos do sinal</h4><ul>{signal.risks.map((risk,i)=><li key={i}>{risk}</li>)}</ul>
    <h4>Contrapontos</h4><ul>{signal.counterpoints.map((counterpoint,i)=><li key={i}>{counterpoint}</li>)}</ul>
    <h4>O que invalida o sinal</h4><ul>{signal.invalidation_conditions.map((condition,i)=><li key={i}>{condition}</li>)}</ul>
  </section>;
}
export function AnalysisView({analysis}: {analysis: Analysis}) {
  return <div className="analysis">
    <div className="section-heading"><h3>Interpretação da IA</h3><Badge tone="info">{label(analysis.status)}</Badge></div>
    <p className="meta">Corte da análise · {date(analysis.as_of, analysis.as_of.includes('T'))}</p>
    <p>{analysis.synthesis}</p>
    <h4>{label(analysis.opportunity?.classification)}</h4><p>{analysis.opportunity?.rationale}</p>
    <EvidenceRefs ids={analysis.opportunity?.evidence_ids}/>
    {!!analysis.claims?.length && <section aria-label="Afirmações e fontes">
      <h3>Afirmações e fontes</h3>
      {analysis.claims.map((claim, index) => <div key={index} className="claim">
        <Badge>{claim.kind === 'fact' ? 'Fato observado' : 'Interpretação'}</Badge>
        <p>{claim.statement}</p><EvidenceRefs ids={claim.evidence_ids}/>
      </div>)}
    </section>}
    <InformativeSignal signal={analysis.signal}/>
    {!!analysis.scenarios?.length && <section><h3>Cenários e contrapontos</h3>
      {analysis.scenarios.map((scenario, index) => <section className="scenario" key={index}>
        <h4>{label(scenario.case)} · {scenario.horizon_days} dias</h4>
        <p>{scenario.description}</p>
        <strong>Condições de confirmação</strong><ul>{scenario.conditions.map((condition, i) => <li key={i}>{condition}</li>)}</ul>
        <strong>O que invalida a hipótese</strong><ul>{scenario.invalidation_conditions.map((condition, i) => <li key={i}>{condition}</li>)}</ul>
        <EvidenceRefs ids={scenario.evidence_ids}/>
      </section>)}
    </section>}
    {!!analysis.risks?.length && <><h3>Riscos</h3><ul>{analysis.risks.map((risk, i) => <li key={i}>{risk}</li>)}</ul></>}
    {!!analysis.missing_evidence?.length && <div className="notice"><strong>Evidências necessárias</strong>
      <ul>{analysis.missing_evidence.map((missing, i) => <li key={i}>{missing}</li>)}</ul></div>}
    {!!analysis.data_quality_flags?.length && <p className="meta">Limitações: {analysis.data_quality_flags.map(label).join(' · ')}</p>}
    <p className="meta">Hipóteses condicionais. A associação entre evento e preço não comprova causalidade.</p>
    <details className="citation-provenance"><summary>Identificação da análise</summary><code>{analysis.analysis_id}</code></details>
  </div>;
}
export function EventList({items,onAsset,selected,onSelect}:{items:MarketEvent[];onAsset?:(assetId:string,eventId:string)=>void;selected?:string;onSelect?:(id:string)=>void}){return <ul className="event-list">{items.map((e,i)=>{const id=e.id||e.event_id||String(i);return <li key={id} id={onAsset?'event-'+id:undefined} tabIndex={onAsset?-1:undefined} className={selected===id?'event-selected':''}><div className="event-header">{onSelect?<button className="text-button event-title" onClick={()=>onSelect(id)} aria-pressed={selected===id}>{e.title}</button>:<h3>{e.title}</h3>}<Badge tone="info">{label(e.event_type||e.category)}</Badge></div><p className="meta">Acontecimento: {date(e.event_date||e.date)} · Primeira divulgação: {date(e.first_disclosed_at)} · Descoberto: {date(e.first_seen_at)}</p>{e.summary&&<p>{e.summary}</p>}<div className="row wrap"><Badge>{label(e.novelty)}</Badge>{e.event_version&&<small>Versão {e.event_version}</small>}{onAsset&&(e.asset_ids||[e.asset_id].filter(Boolean)).map(a=><button className="text-button" key={a} onClick={()=>onAsset(a!,id)}>Investigar {a!.split(':').pop()}</button>)}</div>{e.links?.map((l,i)=><p className="meta" key={i}>{label(l.link_type)} · {l.entity_id}: {l.mechanism}</p>)}{e.articles&&<Articles items={e.articles} anchors={false}/>}</li>})}</ul>}
export function PriceChart({prices,events,selected,onSelect}:{prices:Price[];events:MarketEvent[];selected?:string;onSelect:(id:string)=>void}){const [table,setTable]=useState(false);const valid=prices.filter(p=>p.close!=null&&Number.isFinite(p.close)&&!Number.isNaN(Date.parse(p.date)));if(!valid.length)return <div className="chart-empty"><p>Histórico de preços indisponível</p><small>O gráfico aparecerá quando houver observações válidas.</small></div>;const values=valid.map(p=>p.close!);const min=Math.min(...values),max=Math.max(...values);const lo=min-(max-min||min*.01||1)*.1,hi=max+(max-min||max*.01||1)*.1;const first=Math.min(...valid.map(p=>Date.parse(p.date))),last=Math.max(...valid.map(p=>Date.parse(p.date)));const x=(d:string)=>54+(Date.parse(d)-first)/(last-first||1)*440;const y=(n:number)=>170-(n-lo)/(hi-lo)*140;return <figure className="price-chart"><figcaption><strong>Preço informado · R$</strong><span className="meta">{date(valid[0].date)} — {date(valid[valid.length-1].date)}</span></figcaption><svg viewBox="0 0 520 210" role="img" aria-label={`${valid.length} preços observados. Mínimo ${money(min)}. Máximo ${money(max)}. Pontos sem interpolação; tabela disponível abaixo.`}>{[lo,(hi+lo)/2,hi].map(n=><g key={n}><line x1="54" x2="494" y1={y(n)} y2={y(n)} className="grid-line"/><text x="48" y={y(n)+4} textAnchor="end">{n.toLocaleString('pt-BR',{maximumFractionDigits:2})}</text></g>)}{valid.map((p,i)=><circle key={p.date+i} cx={x(p.date)} cy={y(p.close!)} r="2.5" className="price-point"><title>{`${date(p.date)}: ${money(p.close)}`}</title></circle>)}{events.map((e,i)=>{const d=e.event_date||e.date||e.first_disclosed_at;const id=e.id||e.event_id||String(i);if(!d||Date.parse(d)<first||Date.parse(d)>last)return null;return <g key={id}><line x1={x(d)} x2={x(d)} y1="22" y2="174" className={selected===id?'event-line active':'event-line'}/><circle role="button" tabIndex={0} aria-label={`Evento: ${e.title}`} cx={x(d)} cy="20" r={selected===id?7:5} className="event-point" onClick={()=>onSelect(id)} onKeyDown={k=>{if(k.key==='Enter'||k.key===' '){k.preventDefault();onSelect(id);}}}><title>{e.title}</title></circle></g>})}<text x="54" y="199">{date(valid[0].date)}</text><text x="494" y="199" textAnchor="end">{date(valid[valid.length-1].date)}</text></svg><p className="meta">Pontos: preços observados. Marcadores: eventos, sem inferir causalidade. Lacunas não são interpoladas.</p><button className="text-button" onClick={()=>setTable(!table)} aria-expanded={table}>{table?'Ocultar':'Ver'} tabela de preços ({prices.length})</button>{table&&<div className="table-scroll chart-table" tabIndex={0}><table><caption className="sr-only">Observações do preço informado</caption><thead><tr><th scope="col">Data</th><th scope="col">Preço informado</th><th scope="col">Fonte / ajuste</th>{prices.some(p=>p.vintage_id)&&<th scope="col">Fechamento ajustado pelo provedor</th>}</tr></thead><tbody>{prices.map((p,i)=><tr key={p.date+i}><td>{date(p.date)}</td><td>{money(p.close)}</td><td>{p.source||p.provider||'Não informada'} · {label(p.adjustment)}</td>{prices.some(p=>p.vintage_id)&&<td>{money(p.adjustedClose)}</td>}</tr>)}</tbody></table></div>}</figure>}
