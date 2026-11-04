import { useEffect, useRef, useState, type ReactNode } from 'react';
import { useData } from './api';
import { ActiveFilterChips } from './ActiveFilterChips';
import { Badge, Empty, Icon, State } from './components';
import { date, label, money, number, priceDate, query } from './format';
import type { Asset, List, Overview } from './types';
import './market-review.css';

type Params = Record<string,string>;
type Change = (updates:Record<string,string|undefined>)=>void;
type Props = {
  params:Params; change:Change; revision:number; reload:()=>void; overview?:Overview;
  watchlist:boolean; context?:ReactNode; renderDetail:(close:()=>void)=>ReactNode;
};

const PAGE_SIZE = 20;
const SORTS:Record<string,string> = {'-event_count':'Mais eventos vinculados',ticker:'Ticker A–Z',name:'Nome da empresa','-price':'Maior preço informado'};
const QUICK_FILTERS = [
  {text:'Todos', quality:undefined, state:undefined},
  {text:'Com evento', quality:'low', state:undefined},
  {text:'Com preço', quality:undefined, state:'priced'},
  {text:'Com análise', quality:undefined, state:'analysed'},
] as const;
const FILTERS = ['search','sector','type','quality','state'] as const;

function exportPage(path:string, data:List<Asset>, page:number) {
  const report = {exported_at:new Date().toISOString(),as_of:data.as_of??null,source:'Radar B3 /api/v1/assets',
    scope:'Somente a página atual; não representa todo o universo',
    filters:Object.fromEntries(new URLSearchParams(path.split('?')[1])),items:data.items};
  const url = URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json'}));
  const link = document.createElement('a');
  link.href=url; link.download='radar-b3-pagina-'+page+'.json'; link.click();
  URL.revokeObjectURL(url);
}

function MarketTable({items, selected, open, sort, full}:{items:Asset[];selected?:string;open:(id:string)=>void;sort:string;full:boolean}) {
  return <div className={'table-scroll market-table-wrap'+(full?' show-comparison':'')} tabIndex={0} aria-label="Comparação de ativos">
    <table className="asset-table market-table">
      <caption className="sr-only">Ativos do recorte, vínculos na janela, último preço coletado e análise disponível. Selecione um ticker para investigar.</caption>
      <thead><tr>
        <th scope="col" aria-sort={sort==='ticker'||sort==='name'?'ascending':undefined}>Ativo / empresa</th>
        <th scope="col" aria-sort={sort==='-event_count'?'descending':undefined}>Eventos vinculados</th>
        <th scope="col" className="market-compare-col" aria-sort={sort==='-price'?'descending':undefined}>Preço / sessão</th>
        <th scope="col" className="market-compare-col">Análise</th>
      </tr></thead>
      <tbody>{items.map(asset=><tr key={asset.id} className={asset.id===selected?'selected-row':''}>
        <td><button className="asset-link" id={'asset-row-'+asset.id} aria-pressed={asset.id===selected}
          aria-label={'Investigar '+asset.ticker} onClick={()=>open(asset.id)}>{asset.ticker}{asset.watchlisted&&<Icon name="star" size={14}/>}</button>
          <span className="company-name">{asset.name}</span>
          <small className="market-mobile-only market-mobile-price">
            {asset.price == null ? 'Preço não coletado' : <>Preço: <strong className="numeric">{money(asset.price)}</strong><br/>
              {asset.price_session_date || asset.price_at ? 'Sessão · ' + priceDate(asset) : 'Sessão não informada'}
              {asset.stale && <><br/><Badge tone="warning">Defasado</Badge></>}</>}
          </small>
          <small className="market-compare-col">{asset.type==='unknown'?'Tipo não classificado':label(asset.type)} · {asset.sector||'Setor não classificado'}</small>
        </td>
        <td><strong className="market-count numeric">{asset.event_count==null?'Indisponível':asset.event_count===0?'Sem vínculo':asset.event_count===1?'1 evento':number(asset.event_count)+' eventos'}</strong>
          {asset.last_event&&<small className="market-event-title">{asset.last_event}</small>}
          {asset.last_event_seen_date?<small>Descoberto · {date(asset.last_event_seen_date)}</small>
            :asset.last_event_disclosed_date?<small>Divulgação · {date(asset.last_event_disclosed_date)}</small>
            :!!asset.event_count&&<small>Data não informada</small>}
        </td>
        <td className="market-compare-col"><strong className="numeric">{money(asset.price)}</strong>
          <small>{asset.price_session_date||asset.price_at?priceDate(asset):'Preço não coletado'}</small>
          {asset.stale&&asset.price!=null&&<Badge tone="warning">Defasado</Badge>}</td>
        <td className="market-compare-col">{asset.analysis_state&&asset.analysis_state!=='indisponível'?label(asset.analysis_state):'Sem análise'}</td>
      </tr>)}</tbody>
    </table>
  </div>;
}

type ResultProps = Pick<Props,'revision'|'reload'|'watchlist'|'change'> & {
  path:string; page:number; selected?:string; sort:string; active:boolean;
  open:(id:string)=>void; clear:()=>void; full:boolean;
};

// The caller keys this component by the complete API query. A refresh of that
// query may retain labeled rows, a change of query mounts a fresh result set.
function MarketResults({path,revision,reload,watchlist,change,page,selected,sort,active,open,clear,full}:ResultProps) {
  const result = useData<List<Asset>>(path,revision);
  const items = result.data?.items||[];
  const total = result.data?.total;
  const fresh = !!result.current;
  return <div className="market-results" aria-label="Resultados do mercado">
    <div className="list-heading"><p aria-live="polite">{total==null?'Consultando o recorte…':number(total)+(total===1?' ativo':' ativos')+(watchlist?' nos favoritos':' no recorte')}</p>
      <button className="text-button" disabled={!fresh||!items.length} onClick={()=>{if(fresh&&result.data&&items.length)exportPage(path,result.data,page);}}>
        <Icon name="download" size={16}/>Exportar página</button></div>
    <State loading={result.loading} error={result.error} retry={reload}/>
    {result.data&&!fresh&&<p className="meta" role="status">Último resultado deste recorte{result.data.as_of?' · '+date(result.data.as_of,true):''}.</p>}
    {!result.error&&fresh&&!items.length&&<Empty title={active?'Nenhum ativo corresponde aos filtros':watchlist?'Você ainda não tem favoritos':'Nenhum ativo neste recorte'}
      action={active?<button onClick={clear}>Limpar filtros</button>:watchlist?<button onClick={()=>change({view:'market'})}>Explorar mercado</button>:undefined}>
      {active?'Altere a busca ou remova os filtros.':watchlist?'Abra um ativo no Mercado para adicioná-lo aos favoritos.':'Consulte Fontes e cobertura para verificar o cadastro.'}</Empty>}
    {!!items.length&&<MarketTable items={items} selected={selected} open={open} sort={sort} full={full}/>}
    {(!!items.length||page>1)&&<div className="pagination"><span>{items.length?`${(page-1)*PAGE_SIZE+1}–${(page-1)*PAGE_SIZE+items.length} de ${number(total)}`:'Sem linhas para exibir'}</span>
      <div><button disabled={page===1||result.loading} onClick={()=>change({page:String(page-1)})}>Anterior</button>
        <span aria-label={'Página '+page}>{page}</span>
        <button disabled={!fresh||(total!=null?page*PAGE_SIZE>=total:items.length<PAGE_SIZE)} onClick={()=>change({page:String(page+1)})}>Próxima</button></div></div>}
    {!!items.length&&<p className="meta">A contagem inclui menções candidatas ao ticker nos últimos três meses. A ficha permite conferir cada vínculo.</p>}
  </div>;
}

export function MarketExplorer({params,change,revision,reload,overview,watchlist,context,renderDetail}:Props) {
  const requestedPage = Number(params.page);
  const page = Number.isSafeInteger(requestedPage)&&requestedPage>0?requestedPage:1;
  const sort = Object.hasOwn(SORTS,params.sort)?params.sort:'-event_count';
  const type = params.type||(watchlist?'all':'eligible');
  const path = '/assets?'+query({search:params.search,sector:params.sector,type:type==='all'?undefined:type,quality:params.quality,state:params.state,
    sort,page,page_size:PAGE_SIZE,watchlist:watchlist?'true':undefined});
  const [draft,setDraft] = useState(params.search||'');
  const [filters,setFilters] = useState(false);
  const [full,setFull] = useState(false);
  const opener = useRef<string|undefined>(undefined);
  const searchRef = useRef<HTMLInputElement>(null);
  useEffect(()=>setDraft(params.search||''),[params.search]);
  const active = FILTERS.some(key=>!!params[key]);
  const updateList:Change = updates=>change({asset:undefined,origin_event:undefined,origin_asset:undefined,page:'1',...updates});
  const clear = ()=>{setDraft('');updateList({search:undefined,sector:undefined,type:undefined,quality:undefined,state:undefined});};
  const filter = (key:string,value:string)=>updateList({[key]:value||undefined});
  const open = (id:string)=>{
    opener.current=id;
    if(params.asset===id) {document.getElementById('asset-detail-heading')?.focus();return;}
    change({asset:id,origin_event:undefined,origin_asset:undefined});
  };
  const close = ()=>{
    const id=opener.current||params.asset;
    change({asset:undefined,origin_event:undefined,origin_asset:undefined});
    requestAnimationFrame(()=>(document.getElementById('asset-row-'+id)||searchRef.current)?.focus());
  };
  const coverage = overview?.coverage;
  return <section className="market-explorer" aria-label="Exploração do mercado">
    <form className="filters" onSubmit={e=>{e.preventDefault();filter('search',draft.trim());}}>
      <div className="search-field"><Icon name="search"/><input ref={searchRef} aria-label="Buscar ativo ou empresa" placeholder="Buscar ticker ou empresa…" value={draft} onChange={e=>setDraft(e.target.value)}/><button type="submit">Buscar</button></div>
      <label className="inline-label"><span className="sr-only">Ordenar ativos</span><select value={sort} onChange={e=>filter('sort',e.target.value)}>
        {Object.entries(SORTS).map(([key,text])=><option key={key} value={key}>{text}</option>)}</select></label>
      <button id="market-filter-toggle" type="button" aria-expanded={filters} aria-controls={filters?'market-filters':undefined} onClick={()=>setFilters(v=>!v)}>Filtros</button>
    </form>
    {params.sort&&params.sort!==sort&&<p className="meta">A ordenação deste link está indisponível. Exibindo os ativos por número de eventos vinculados.</p>}
    {filters&&<div id="market-filters" className="expanded-filters">
      <label>Setor<input value={params.sector||''} placeholder="Nome do setor" onChange={e=>filter('sector',e.target.value)}/></label>
      <label>Tipo<select value={type} onChange={e=>filter('type',e.target.value===(watchlist?'all':'eligible')?'':e.target.value)}>
        <option value="all">{watchlist?'Todos os favoritos':'Elegíveis e não classificados'}</option>
        <option value="eligible">Ações e units elegíveis</option><option value="stock">Ações</option><option value="unit">Units</option><option value="unknown">Não classificados</option>
      </select></label>
      <label>Vínculo com eventos<select value={params.quality||''} onChange={e=>filter('quality',e.target.value)}>
        <option value="">Todos</option><option value="low">Com eventos vinculados</option><option value="unknown">Sem eventos vinculados</option>
        {params.quality&&!['low','unknown'].includes(params.quality)&&<option value={params.quality}>Filtro anterior · {label(params.quality)}</option>}
      </select></label>
      <label>Cobertura<select value={params.state||''} onChange={e=>filter('state',e.target.value)}>
        <option value="">Todas</option><option value="priced">Com preço</option><option value="unpriced">Sem preço</option><option value="analysed">Com análise</option>
        {params.state&&!['priced','unpriced','analysed'].includes(params.state)&&<option value={params.state}>{label(params.state)}</option>}
      </select></label>
    </div>}
    <div className="market-shortcuts" role="group" aria-label="Recortes rápidos">
      {QUICK_FILTERS.map(({text,quality,state})=><button key={text} type="button"
        aria-pressed={params.quality===quality&&params.state===state}
        onClick={()=>updateList({quality,state})}>{text}</button>)}
    </div>
    <ActiveFilterChips params={params} onRemove={key=>{if(key==='search')setDraft('');filter(key,'');}} onClear={clear}/>
    {context}
    {coverage&&<p className="market-universe">{number(coverage.catalog_assets)} no cadastro · {number(coverage.eligible_assets)} elegíveis · {number(coverage.unclassified_assets)} sem classificação</p>}
    <button className="market-mobile-only text-button" type="button" aria-pressed={full} onClick={()=>setFull(v=>!v)}>{full?'Lista compacta':'Mostrar preço e análise'}</button>
    <div className={'market-layout'+(params.asset?' has-selection':' market-wide')}>
      <MarketResults key={path} path={path} revision={revision} reload={reload} watchlist={watchlist} change={updateList} page={page}
        selected={params.asset} sort={sort} active={active} open={open} clear={clear} full={full}/>
      {params.asset&&<aside className="detail-panel" aria-label="Detalhe do ativo">{renderDetail(close)}</aside>}
    </div>
  </section>;
}
