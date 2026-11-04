import { date, label } from './format';
import type { ExecutionMode, Job, Operations } from './types';

export function canSubmitJob(kind:string, control?:Operations['job_control']) {
  return !!control && control.reason === 'ready' && control.allowed_kinds.includes(kind)
    && Date.parse(control.expires_at || '') > Date.now();
}

export function collectionControlMessage(control?:Operations['job_control']) {
  if (!control) return 'Aguardando confirmação de um executor para pedidos manuais.';
  if (canSubmitJob('refresh', control)) return 'Pedidos manuais de coleta disponíveis.';
  return label(control.reason === 'ready' ? 'manual_consumer_expired' : control.reason);
}

export function canRetryJob(job: Pick<Job, 'kind'>, analysisAllowed: boolean, mode?: ExecutionMode, control?:Operations['job_control']) {
  return mode !== 'manual-analysis' && canSubmitJob(job.kind,control)
    && (!['analyse', 'analysis'].includes(job.kind) || analysisAllowed);
}

const workerLabels = {
  active: 'Worker ativo',
  absent: 'Sem coleta em andamento',
  stale: 'Coleta sem confirmação',
};

export function OperationalStatus({data, error = ''}: {data?:Operations; error?:string}) {
  if (!data) return <p className="notice">Estado operacional {error ? 'indisponível. ' + error : 'em consulta…'} A conexão com a API não confirma que a coleta esteja ativa.</p>;
  const collectionOnly = data.execution_mode === 'collection-only';
  const manual = data.execution_mode === 'manual-analysis';
  const manualState = data.manual_analysis;
  const manualStateLabel = manualState?.state === 'ready' ? 'Pronta para uma solicitação' : manualState?.state === 'busy' ? 'Uma solicitação em andamento' : 'Indisponível';
  const collected = (value:string|null) => value ? date(value, true) : 'Nenhuma coleta registrada';
  const received = (value?:string|null) => value ? date(value, true) : 'Nenhum dado recebido';
  return <>{data.mode_mismatch && <p className="notice danger" role="alert">API e worker estão em modos diferentes. Um worker com IA pode executar análises já na fila; este painel não altera esse processo.</p>}<details className="surface">
    <summary className="operation-summary"><strong>Operação: {manual ? `Análise manual · ${manualStateLabel}` : collectionOnly ? 'Coleta sem IA' : 'Modo com IA'} · {manual && data.worker.status === 'absent' ? 'Worker manual ausente' : workerLabels[data.worker.status]}</strong><span className="diagnostic-label">Ver estado operacional</span>
      {collectionOnly&&<small className="data-clocks">Último dado recebido · {[
        ['Preços',data.collection.latest_price_data_at],['Notícias',data.collection.latest_news_data_at],['Macro',data.collection.latest_macro_data_at],
      ].map(([name,at])=>name+' '+(at?date(at):'sem registro')).join(' · ')}</small>}
    </summary>
    {data.worker.status==='stale'&&<p>O último sinal do worker venceu. Ainda não há confirmação de que ele continua executando.</p>}
    {error && <p className="notice">Não foi possível renovar o estado: {error}. Abaixo está a última consulta recebida.</p>}
    <p>{collectionControlMessage(error?undefined:data.job_control)}</p>
    <p>{manual ? 'Uma análise por clique, sem reparo automático. Coleta e repetição de execuções estão indisponíveis neste modo.' : collectionOnly ? (data.mode_mismatch ? 'Esta API está em coleta sem IA. O modo do worker deve ser conferido separadamente.' : 'Inferência desativada neste processo. Análises pendentes ficam preservadas, aguardando autorização.') : 'O modo permite IA, sujeito à homologação, à quota e ao worker ativo.'}</p>
    {manual && <><p>{data.ai.reason || 'A disponibilidade depende da homologação, do worker manual e da cota local.'}</p>{manualState?.quota ? <p>Cota local: {manualState.quota.used} de {manualState.quota.limit} · {manualState.quota.remaining} restantes. Período: {date(manualState.quota.period)}.</p> : <p>Cota local indisponível.</p>}<p className="meta">Reservas em andamento ou sem confirmação também contam. Este teto local não representa a franquia oficial da assinatura. O histórico fora do escopo manual permanece preservado.</p></>}
    <dl className="data-list">
      <div><dt>IA homologada</dt><dd>{data.ai.homologated ? 'Sim' : 'Não'}</dd></div>
      <div><dt>Inferência disponível neste modo</dt><dd>{data.ai.enabled ? 'Sim' : 'Não'}</dd></div>
      <div><dt>Último heartbeat do worker</dt><dd>{date(data.worker.heartbeat_at, true)}</dd></div>
      <div><dt>Modo do worker</dt><dd>{data.worker.execution_mode === 'manual-analysis' ? 'Análise manual' : data.worker.execution_mode === 'collection-only' ? 'Coleta sem IA' : data.worker.execution_mode === 'analysis-enabled' ? 'Modo com IA' : 'Não informado'}</dd></div>
      <div><dt>Última coleta bem-sucedida de notícias</dt><dd>{collected(data.collection.latest_news_fetch)}</dd></div>
      <div><dt>Último dado de notícias recebido</dt><dd>{received(data.collection.latest_news_data_at)}</dd></div>
      <div><dt>Última coleta bem-sucedida de preços</dt><dd>{collected(data.collection.latest_price_fetch)}</dd></div>
      <div><dt>Último dado de preços recebido</dt><dd>{received(data.collection.latest_price_data_at)}</dd></div>
      <div><dt>Última coleta bem-sucedida macro</dt><dd>{collected(data.collection.latest_macro_fetch)}</dd></div>
      <div><dt>Último dado macro recebido</dt><dd>{received(data.collection.latest_macro_data_at)}</dd></div>
      <div><dt>Snapshot da análise mais recente</dt><dd>{date(data.latest_analysis_snapshot, true)}</dd></div>
    </dl>
    <p className="meta">Estado consultado em {date(data.queried_at, true)}. Este horário não é a atualização dos dados nem da análise. Uma coleta bem-sucedida pode confirmar dados já recebidos, sem criar nova versão.</p>
    <details><summary>Última coleta por fonte/conjunto</summary>
      {data.collection.sources.length ? <ul>{data.collection.sources.map(source => <li key={source.provider_id}>
        <strong>{source.provider_id}</strong> · Último sucesso: {date(source.last_success_at, true)} · Última tentativa: {date(source.last_attempt_at, true)}
        {source.error_code && <span> · Falha: {source.error_code}</span>}
        {source.next_attempt_at && <span> · Aguardar até {date(source.next_attempt_at, true)}</span>}
      </li>)}</ul> : <p>Nenhuma tentativa registrada.</p>}
    </details>
  </details></>;
}
