import { useData } from './api';
import { External } from './components';
import { sourceName } from './EvidenceCitation';
import { date } from './format';
import type { AssetDetail, MarketEvent } from './types';

function OriginStory({event}:{event:MarketEvent}) {
  return <><h3>{event.title}</h3><p className="meta">{event.first_disclosed_at?'Primeira divulgação · '+date(event.first_disclosed_at)
    :event.first_seen_at?'Divulgação sem data · Descoberto em '+date(event.first_seen_at):'Data de divulgação não informada'}</p>
    {event.articles?.map(article=>{
      const publisher=article.source||sourceName(article.source_id);
      return <p key={article.revision_id||article.id}><External href={article.url||article.canonical_url}>
        {article.title===event.title?'Publicação de '+publisher:article.title}</External>
        <small className="origin-byline">{article.title!==event.title&&publisher+' · '}{article.published_at?date(article.published_at):'Publicação sem data informada'}</small></p>;
    })}</>;
}

function LookupOrigin({id,asset}:{id:string;asset:string}) {
  const result=useData<MarketEvent>('/events/'+encodeURIComponent(id));
  const event=result.current?result.data:undefined;
  if(result.loading) return <p role="status">Localizando o evento…</p>;
  if(!event || (event.id||event.event_id)!==id || !(event.asset_ids||[event.asset_id]).includes(asset))
    return <p>O evento de origem não está disponível para este ativo na consulta atual.</p>;
  return <OriginStory event={event}/>;
}

export function EventOrigin({asset}:{asset:AssetDetail}) {
  const params=new URLSearchParams(window.location.search);
  const id=params.get('origin_event');
  if(!id || params.get('origin_asset')!==asset.id) return null;
  const event=asset.events.find(item=>(item.id||item.event_id)===id);
  const back=new URLSearchParams({view:'events',return_event:id});
  for(const key of ['event_search','event_page']) if(params.get(key)) back.set(key,params.get(key)!);
  const href='?'+back.toString();
  return <section aria-label="Evento de origem" className="event-origin"><strong>Evento de origem</strong>
    {event?<OriginStory event={event}/>:<LookupOrigin key={id} id={id} asset={asset.id}/>}
    <a href={href} onClick={e=>{
      if(e.button!==0||e.metaKey||e.ctrlKey||e.shiftKey||e.altKey) return;
      e.preventDefault();
      window.history.pushState(null,'',href);
      window.dispatchEvent(new PopStateEvent('popstate'));
    }}>Voltar ao evento na lista</a>
  </section>;
}
