import { useEffect, useRef, useState } from 'react';
import { useData } from './api';
import { Empty, EventList, Icon, State } from './components';
import { number, query } from './format';
import type { List, MarketEvent } from './types';

type Props={params:Record<string,string>;change:(updates:Record<string,string|undefined>)=>void;revision:number;reload:()=>void};

export function Events({params,change,revision,reload}:Props) {
  const [search,setSearch]=useState(params.event_search||'');
  useEffect(()=>setSearch(params.event_search||''),[params.event_search]);
  const page=Math.max(1,Number(params.event_page)||1);
  const response=useData<List<MarketEvent>>('/events?'+query({search:params.event_search,page,page_size:30}),revision);
  const list=response.current?response.data:undefined;
  const focused=useRef<string|undefined>(undefined);
  const origin=params.return_event;
  useEffect(()=>{
    if(!origin){focused.current=undefined;return;}
    if(!list || focused.current===origin) return;
    const target=document.getElementById('event-'+origin);
    if(target){target.focus();focused.current=origin;}
  },[list,origin]);
  const setPage=(p:number)=>change({event_page:String(p),return_event:undefined});
  return <section><form className="filters" onSubmit={e=>{e.preventDefault();change({event_search:search.trim(),event_page:'1',return_event:undefined});}}>
    <div className="search-field"><Icon name="search"/><input aria-label="Buscar eventos pelo título" placeholder="Buscar no título do evento" value={search} onChange={e=>setSearch(e.target.value)}/><button>Buscar eventos</button></div>
    {params.event_search&&<button type="button" onClick={()=>{setSearch('');change({event_search:undefined,event_page:'1',return_event:undefined});}}>Limpar busca</button>}
  </form><State loading={response.loading||(!response.error&&!list)} error={response.error} retry={reload}/>
  <div className="surface">{list&&<>
    {origin&&!list.items.some(e=>(e.id||e.event_id)===origin)&&<p role="status">O evento de origem já não aparece nesta página da consulta.</p>}
    {list.items.length?<><p className="meta">{number(list.total??list.items.length)} eventos observados · Republicações não equivalem a confirmações independentes.</p>
      <EventList items={list.items} onAsset={(asset,id)=>change({view:'market',asset,origin_event:id,origin_asset:asset,return_event:undefined})}/>
      <div className="pagination"><span>Página {page}</span><div><button disabled={page===1} onClick={()=>setPage(page-1)}>Anterior</button>
      <button disabled={page*30>=(list.total??list.items.length)} onClick={()=>setPage(page+1)}>Próxima</button></div></div>
    </>:<Empty title="Nenhum evento encontrado">A coleta e os vínculos ainda não trouxeram eventos para esta consulta.</Empty>}
  </>}</div></section>;
}
