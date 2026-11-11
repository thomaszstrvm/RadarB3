export function number(value:number|null|undefined):string { return value == null || !Number.isFinite(value) ? 'Indisponível' : value.toLocaleString('pt-BR'); }
export function money(value:number|null|undefined):string { return value == null || !Number.isFinite(value) ? 'Indisponível' : value.toLocaleString('pt-BR',{style:'currency',currency:'BRL'}); }
export function percent(value:number|null|undefined):string { return value == null || !Number.isFinite(value) ? 'Indisponível' : value.toLocaleString('pt-BR',{style:'percent',minimumFractionDigits:2,maximumFractionDigits:2,signDisplay:'always'}); }
export function date(value:string|null|undefined, time=false):string { if(!value) return 'Não informada'; const d=new Date(value.length===10?value+'T12:00:00Z':value); return Number.isNaN(d.valueOf())?'Data inválida':new Intl.DateTimeFormat('pt-BR',{timeZone:'America/Sao_Paulo',day:'2-digit',month:'short',year:'numeric',...(time?{hour:'2-digit',minute:'2-digit'}:{})}).format(d)+(time?' · Brasília':''); }
export function safeUrl(value:string|null|undefined):string|undefined { try { const u=new URL(value||''); return ['https:','http:'].includes(u.protocol)?u.href:undefined; } catch {return undefined;} }
export function priceDate(value:{price_at?:string|null;price_timestamp_precision?:'date'|'instant';price_session_date?:string|null}):string {return value.price_timestamp_precision==='instant'?date(value.price_at,true):date(value.price_session_date||value.price_at);}
const labels:Record<string,string>={supported_hypothesis:'Hipótese sustentada',inconclusive:'Inconclusiva',insufficient_data:'Dados insuficientes',investigate:'Investigar',watch:'Acompanhar',avoid_for_now:'Evitar por enquanto',high:'Alta',medium:'Média',low:'Baixa',partial:'Parcial',unknown:'Desconhecida',available:'Disponível',unavailable:'Indisponível',blocked:'Bloqueada',pending:'Pendente',queued:'Na fila',running:'Em execução',completed:'Concluída',succeeded:'Concluída',failed:'Falhou',cancelled:'Cancelada',canceled:'Cancelada',stale:'Defasada',refresh:'Atualização',backfill:'Histórico',probe:'Diagnóstico',analyse:'Análise',analysis:'Análise',new_fact:'Fato novo',material_revision:'Revisão material',republication:'Republicação',direct:'Vínculo direto',indirect:'Vínculo indireto',candidate:'Vínculo candidato',title_only:'Somente título',snippet:'Trecho disponível',full_text:'Texto integral',summary:'Resumo',unadjusted:'Sem ajuste',adjusted:'Ajustado',macro:'Macroeconomia',earnings:'Resultados',corporate_action:'Evento corporativo',regulation:'Regulação',geopolitics:'Geopolítica',commodity:'Commodities',climate:'Clima',technology:'Tecnologia',other:'Outros',conditional_case:'Cenário condicional',upside_case:'Cenário favorável',downside_case:'Cenário contrário'};
Object.assign(labels,{stock:'Ação',unit:'Unit',not_tested:'Não testada',retry_wait:'Aguardando nova tentativa',paused:'Pausada',blocked:'Bloqueada',new_event:'Novo evento',analysis_change:'Análise revisada',source_failure:'Falha de fonte',eligible_assets:'Ativos elegíveis',priced:'Com preço',unpriced:'Sem preço',analysed:'Com análise',absent:'Ausente',headline:'Somente título',metadata_only:'Somente metadados',snippet_only:'Somente trecho',excerpt:'Trecho disponível',success:'Disponível',ok:'Disponível',prices:'Preços',news:'Notícias',catalog:'Catálogo',official_link_not_found_in_inspected_page:'Feed oficial não encontrado na página consultada',http_200_invalid_xml:'Resposta recebida em formato incompatível com o feed',discovery_http_429:'Limite de requisições na descoberta (HTTP 429)',http_403:'Acesso recusado pela fonte (HTTP 403)',http_429:'Limite de requisições (HTTP 429)',timeout:'Tempo limite de resposta excedido'});
Object.assign(labels,{price_return_1:'Retorno · 1 pregão',price_return_5:'Retorno · 5 pregões',price_return_21:'Retorno · 21 pregões',price_return_63:'Retorno · 63 pregões',realized_volatility_20:'Volatilidade realizada · 20 pregões',relative_volume_20:'Volume relativo · 20 pregões',adjustment_unknown:'Base de ajuste desconhecida',insufficient_observations:'Observações insuficientes'});
export const label=(v:string|number|null|undefined)=>v==null?'Não informada':labels[String(v)]||String(v);
Object.assign(labels,{
  manual_consumer_absent:'Coleta manual indisponível. Não há executor atendendo estes pedidos.',
  manual_consumer_expired:'Coleta manual indisponível. A confirmação do executor venceu.',
  manual_consumer_restricted:'O executor atual atende apenas tarefas delimitadas. A coleta diária não consome pedidos manuais.',
  manual_consumer_unverified:'A capacidade de atender pedidos manuais ainda não foi confirmada.',
  manual_consumer_incompatible:'O executor atual não atende este tipo de pedido.',
  manual_operation_not_allowed:'Coleta e repetição de execuções estão indisponíveis no modo de análise manual.',
});
Object.assign(labels,{adjusted_close_unavailable:'Fechamento ajustado ausente',zero_daily_volatility:'Desvio diário zero: alinhamento indisponível',non_finite_or_invalid_calculation:'Cálculo não finito ou inválido',session_completeness_unverified:'Período encerrado não verificado',calendar_unverified:'Calendário fora da cobertura verificada',missing_market_session:'Falta sessão na série',missing_latest_market_session:'Falta a última sessão esperada',provider_adjusted_not_investor_total_return:'Ajuste do provedor não comprova retorno total do investidor',technical_heuristic_experimental:'Heurística técnica experimental',relative_volume_comparability_unverified:'Comparabilidade do volume relativo não verificada'});
export function query(params:Record<string,string|number|boolean|undefined|null>) {const q=new URLSearchParams(); Object.entries(params).forEach(([k,v])=>{if(v!==undefined&&v!==null&&v!=='')q.set(k,String(v));});return q.toString();}
import type { AnalysisSignal } from './types';

export const signalActionLabels = {
  buy:'Hipótese de compra', sell:'Hipótese de venda', wait:'Aguardar', insufficient_data:'Dados insuficientes',
};
export function isExperimentalSignal(signal?: AnalysisSignal): boolean {
  return signal?.method?.policy_id === 'technical_alignment_v1' && signal.method.experimental === true;
}


Object.assign(labels,{rss_reuse_terms_review_required:'Coleta suspensa: termos de reutilização do RSS aguardam revisão.'});
Object.assign(labels,{
  calendar_inconsistent:'Calendário documentado inconsistente; cobertura não confirmada.',
  calendar_window_unverified:'Calendário sem verificação para toda a janela solicitada.',
  missing_market_sessions:'Faltam sessões esperadas nesta captura.',
});

Object.assign(labels,{provider_state_clock_invalid:'Registro operacional com horário inválido ou futuro; diagnóstico não confirmado nesse corte.'});

Object.assign(labels,{price_capture_invalid:'Captura de preços inválida; dados indisponíveis até uma nova captura válida.'});
