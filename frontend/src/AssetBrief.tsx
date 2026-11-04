import { External } from './components';
import { InlineCitation, sourceName } from './EvidenceCitation';
import { date, label, signalActionLabels, isExperimentalSignal } from './format';
import type { AssetDetail, MarketEvent } from './types';
import './detail-review.css';

function CurrentEvent({event}: {event?: MarketEvent}) {
  if (!event) return <div><h3>Evento associado</h3><p>Nenhum evento vinculado nesta janela.</p></div>;
  const article = event.articles?.[0];
  const when = event.event_date ? 'Acontecimento em ' + date(event.event_date)
    : event.first_disclosed_at ? 'Primeira divulgação em ' + date(event.first_disclosed_at)
    : event.first_seen_at ? 'Descoberto em ' + date(event.first_seen_at) : 'Data do evento não informada';
  return <div><h3>Evento associado na janela atual</h3>
    <p className="brief-event-title">{event.title}</p><p className="brief-caption">{when}</p>
    {article ? <p className="brief-source">Publicação atual · <External href={article.url || article.canonical_url}>
      {article.title === event.title ? article.source || sourceName(article.source_id) : article.title}
    </External>{article.title !== event.title && ' · ' + (article.source || sourceName(article.source_id))}
      {' · '}{article.published_at ? date(article.published_at) : 'Publicação sem data'}</p>
      : <p className="brief-caption">Publicação vinculada indisponível.</p>}
  </div>;
}

export function AssetBrief({asset, showCurrentEvent = true}: {asset: AssetDetail; showCurrentEvent?: boolean}) {
  const analysis = asset.analysis;
  const signal = analysis?.signal;
  const cited = signal?.evidence_ids?.length ? signal.evidence_ids : analysis?.opportunity?.evidence_ids?.length
    ? analysis.opportunity.evidence_ids : analysis?.evidence_ids || [];
  const citedRecords = (asset.evidence || []).filter(item => cited.includes(item.evidence_id));
  const source = citedRecords.find(item => item.record_type === 'article' || item.record_type === 'filing') || citedRecords[0];
  const conclusion = analysis ? label(analysis.status) : 'Sem análise validada';
  const action = signal && (Object.hasOwn(signalActionLabels, signal.action) ? signalActionLabels[signal.action] : 'Indisponível');
  const experimental = isExperimentalSignal(signal);
  const limit = analysis?.missing_evidence?.[0] || signal?.counterpoints?.[0] || signal?.risks?.[0] || analysis?.risks?.[0]
    || (analysis?.data_quality_flags?.[0] && label(analysis.data_quality_flags[0]));
  return <section className="asset-brief" aria-label="Resumo da investigação">
    {showCurrentEvent && <CurrentEvent event={asset.events?.[0]}/>}
    <div><h3>Conclusão da análise</h3><p className="brief-conclusion">{conclusion}</p>
      {action && action !== conclusion && <p>Sinal informativo · {action}</p>}
      {experimental && <p className="brief-caption">Hipótese experimental · não personalizada</p>}
      {analysis && <p className="brief-caption">Dados até <time dateTime={analysis.as_of}>{date(analysis.as_of, analysis.as_of.includes('T'))}</time>{signal && (experimental ? ' · Reavaliação em ' : ' · Horizonte de ') + signal.horizon_days + ' dias'}</p>}
      {analysis?.status === 'supported_hypothesis' && <p className="brief-interpretation">{signal?.rationale || analysis.opportunity?.rationale}</p>}
      <dl className="brief-facts">
        <dt>Principal limitação</dt><dd>{limit || (analysis ? 'Nenhuma limitação específica registrada.' : 'Ainda não há interpretação validada para este ativo.')}</dd>
        {analysis && <><dt>Fonte citada na análise</dt><dd>{source || cited[0] ? <InlineCitation id={source?.evidence_id || cited[0]}/> : 'Nenhuma referência registrada.'}</dd></>}
      </dl>
    </div>
  </section>;
}
