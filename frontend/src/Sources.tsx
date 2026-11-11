import { useData } from './api';
import { Badge, Empty, State } from './components';
import { date, label, number } from './format';
import type { List, Source } from './types';

export function sourceLimitations(source: Source): string[] {
  const reasons: string[] = [];
  if (source.details?.historical_coverage === 'feed_window_only') {
    reasons.push('RSS recente; cobertura de três meses não garantida.');
  }
  for (const code of [source.reason, source.error]) {
    if (!code) continue;
    const message = label(code);
    if (!reasons.includes(message)) reasons.push(message);
  }
  return reasons.length ? reasons : ['Cobertura histórica não confirmada. Consulte os detalhes da fonte.'];
}

function SourceLimitations({source}: {source: Source}) {
  return <div className="source-limitation">{sourceLimitations(source).map(text => <p key={text}>{text}</p>)}
    {source.next_attempt_at && <p>Aguardar até {date(source.next_attempt_at,true)}</p>}
  </div>;
}
function SourceState({source}: {source: Source}) {
  return <><Badge tone={source.status === 'available' ? 'info' : 'warning'}>{label(source.status)}</Badge>
    {(source.access_level || source.capability) && <small>{label(source.access_level || source.capability)}</small>}</>;
}
function SourceInterval({source}: {source: Source}) {
  return <>{source.available_start || source.available_end
    ? `${date(source.available_start)} — ${date(source.available_end)}` : 'Intervalo não informado'}</>;
}
function SourceDetails({source}: {source: Source}) {
  return <details className="source-diagnostics"><summary>Histórico e quotas</summary>
    <pre>{JSON.stringify({reason: source.reason, error: source.error, attempts: source.attempts, quota: source.quota, details: source.details},null,2)}</pre>
  </details>;
}
export function SourceDiagnostics({items}: {items: Source[]}) {
  return <>
    <div className="table-scroll source-desktop"><table><caption>Diagnóstico das fontes</caption>
      <thead><tr><th scope="col">Fonte</th><th scope="col">Estado / capacidade</th><th scope="col">Último sucesso</th><th scope="col">Itens / intervalo obtido</th><th scope="col">Limitação / tentativa</th></tr></thead>
      <tbody>{items.map(source => <tr key={source.id}>
        <td><strong>{source.name}</strong><small>{source.id}</small></td>
        <td><SourceState source={source}/></td><td>{date(source.last_success_at,true)}</td>
        <td>{number(source.item_count ?? source.count)}<small><SourceInterval source={source}/></small></td>
        <td><SourceLimitations source={source}/><small>Última tentativa: {date(source.last_attempt_at,true)}</small><SourceDetails source={source}/></td>
      </tr>)}</tbody></table></div>
    <section className="source-mobile" aria-label="Diagnóstico das fontes">
      {items.map(source => <article key={source.id}>
        <h3>{source.name}</h3><SourceState source={source}/><SourceLimitations source={source}/>
        <dl><dt>Último sucesso da coleta</dt><dd>{date(source.last_success_at,true)}</dd>
          <dt>Itens registrados</dt><dd>{number(source.item_count ?? source.count)}</dd>
          <dt>Intervalo obtido</dt><dd><SourceInterval source={source}/></dd>
          <dt>Última tentativa</dt><dd>{date(source.last_attempt_at,true)}</dd></dl>
        <SourceDetails source={source}/>
      </article>)}
    </section>
  </>;
}
export function Sources({revision,reload,job,busy}: {revision:number;reload:()=>void;job:(kind:string,scope?:object)=>Promise<boolean>;busy:boolean}) {
  const sources = useData<List<Source>>('/sources',revision);
  const coverage = useData<Record<string,unknown>>('/coverage',revision);
  return <><div className="section-heading sources-heading">
    <p className="muted">Oito recortes editoriais selecionados. Disponibilidade da coleta não comprova cobertura histórica.</p>
    <div className="row wrap"><button disabled={busy} onClick={()=>job('backfill')}>Buscar histórico</button><button disabled={busy} onClick={()=>job('probe')}>Executar diagnóstico</button></div>
    </div><State loading={sources.loading} error={sources.error} retry={reload}/>
    <SourceDiagnostics items={sources.data?.items || []}/>
    {sources.data && !sources.data.items.length && <Empty title="Catálogo de fontes indisponível">Execute o diagnóstico para verificar a configuração.</Empty>}
    <section className="surface coverage"><h2>Cobertura e lacunas</h2><State loading={coverage.loading} error={coverage.error} retry={reload}/>{coverage.data && <StructuredData data={coverage.data}/>}</section>
  </>;
}
const fieldNames:Record<string,string>={catalog_assets:'Ativos no catálogo completo',eligible_assets:'Ações e units elegíveis',unclassified_assets:'Tipos ainda não classificados',assets_with_prices:'Ativos com preço',assets_with_news:'Ativos com vínculo de notícia',eligible_assets_with_price_vintages:'Elegíveis com captura de preços',eligible_assets_with_legacy_prices:'Elegíveis com preços legados',eligible_assets_with_news:'Elegíveis com vínculo de notícia',eligible_assets_with_fundamentals:'Elegíveis com registro de fundamentos',assets_analyzed:'Ativos analisados',known_gaps:'Lacunas conhecidas',truncated_queries_count:'Consultas truncadas',profile:'Perfil de dados',sqlite_version:'Versão do armazenamento',journal_mode:'Modo do armazenamento',assets:'Ativos cadastrados',priced:'Ativos com preço',analysed:'Ativos analisados',articles:'Publicações',events:'Eventos',pending:'Pendentes',as_of:'Corte',window_start:'Início da janela',eligible:'Elegíveis',unclassified:'Sem classificação',with_news:'Com notícias',with_prices:'Com preço',with_analysis:'Com análise',missing_prices:'Sem preço',limitations:'Limitações',sources:'Fontes',counts:'Contagens observadas',universe:'Universo',last_updated:'Última atualização',status:'Estado',reason:'Motivo',total:'Total observado',mode:'Modo',partitions:'Partições do histórico'};
function StructuredData({data}:{data:Record<string,unknown>}){return <dl className="data-list">{Object.entries(data).map(([k,v])=><div key={k}><dt>{fieldNames[k]||k.replaceAll('_',' ')}</dt><dd>{v===null?'Indisponível':typeof v==='object'?<pre>{JSON.stringify(v,null,2)}</pre>:typeof v==='number'?number(v):typeof v==='boolean'?(v?'Sim':'Não'):String(v)}</dd></div>)}</dl>}
