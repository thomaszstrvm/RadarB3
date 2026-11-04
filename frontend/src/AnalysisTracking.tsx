import { useEffect, useRef, useState } from 'react';
import { request } from './api';
import { Icon } from './components';
import { date, label } from './format';
import { analysisJobMessage, isAnalysisActive, isCurrentAnalysisActive, isOutsideManualScope, watchAnalysis, type TrackingState } from './pollAnalysis';
import type { AI, AnalysisExecutionDiagnostic, AnalysisJob, AnalysisValidationIssue, Job, NumericValidationReason } from './types';

type Props = {
  assetId: string;
  ai?: AI;
  busy: boolean;
  revision: number;
  onResult: () => void;
  onAvailabilityChange?: () => void;
  openJobs: () => void;
  currentAnalysisId?: string;
  currentAnalysisAt?: string;
  resultLoading: boolean;
  resultError: boolean;
};

export function AnalysisTracking(props: Props) {
  const { assetId, ai, busy, revision, openJobs } = props;
  const [state, setState] = useState<TrackingState>({ phase: 'loading', error: '' });
  const [cycle, setCycle] = useState(0);
  const [submitting, setSubmitting] = useState(false);
  const [submissionError, setSubmissionError] = useState('');
  const submittingRef = useRef(false);
  const mounted = useRef(false);
  const onResult = useRef(props.onResult);
  const onAvailabilityChange = useRef(props.onAvailabilityChange);
  const notified = useRef('');
  const path = '/assets/' + encodeURIComponent(assetId);
  onResult.current = props.onResult;
  onAvailabilityChange.current = props.onAvailabilityChange;

  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    setState(previous => ({ ...previous, phase: 'loading', error: '', stopReason: undefined }));
    return watchAnalysis(assetId,
      signal => request<AnalysisJob>(path + '/analysis-job', 'GET', undefined, signal),
      update => setState(previous => ({ ...previous, ...update })),
      { isVisible: () => document.visibilityState === 'visible' },
    );
  }, [assetId, path, cycle, revision]);

  const snapshot = state.snapshot?.asset_id === assetId ? state.snapshot : undefined;
  const job = snapshot?.job;
  const result = snapshot?.result_analysis;
  const active = isCurrentAnalysisActive(snapshot);
  const outside = isOutsideManualScope(snapshot);
  const reanalyze = ai?.execution_mode === 'manual-analysis' && !!props.currentAnalysisId;
  useEffect(() => {
    const key = job && !outside && !isAnalysisActive(job.status) ? job.id + ':' + job.status + ':' + (job.updated_at || '') : '';
    if (key && key !== notified.current) {
      notified.current = key;
      onResult.current();
      onAvailabilityChange.current?.();
    }
  }, [job?.id, job?.status, job?.updated_at, outside]);

  async function submit() {
    if (busy || submittingRef.current || active || !ai?.enabled || state.phase !== 'idle') return;
    submittingRef.current = true;
    setSubmitting(true);
    setSubmissionError('');
    try {
      const queued = await request<Job>(path + '/analyse', 'POST', reanalyze ? { reanalyze: true } : {});
      if (!mounted.current) return;
      setState({ phase: 'loading', error: '', snapshot: {
        asset_id: assetId, job: queued, queue_position: null, queue_position_reason: 'not_ready',
        result_analysis: null, as_of: queued.updated_at || queued.created_at,
        ...(ai?.execution_mode === 'manual-analysis' ? { manual_scope: 'current' as const } : {}),
      } });
    } catch (error) {
      if (!mounted.current) return;
      setSubmissionError((error instanceof Error ? error.message : 'Não foi possível confirmar o envio.') + ' O acompanhamento será consultado; a análise não será reenviada automaticamente.');
    } finally {
      submittingRef.current = false;
      if (mounted.current) { setSubmitting(false); setCycle(value => value + 1); onAvailabilityChange.current?.(); }
    }
  }

  const refresh = () => setCycle(value => value + 1);
  return <>
    <div className="section-heading">
      <h3>Análise e cenários</h3>
      <button className="micro-control" aria-disabled={busy || submitting || !ai?.enabled || active || state.phase !== 'idle'} aria-describedby={reanalyze ? 'manual-reanalysis-help' : undefined} onClick={submit}>
        {submitting ? 'Solicitando análise…' : active ? 'Análise solicitada' : reanalyze ? 'Reanalisar ativo' : 'Analisar ativo'}
      </button>
    </div>
    {reanalyze && <p className="meta" id="manual-reanalysis-help">Nova chamada de IA, sujeita à cota local. Usa as evidências salvas; o corte dos dados pode permanecer igual.</p>}
    {!ai?.enabled && <p className="notice">{ai?.reason || 'IA indisponível. Consulte Configurações para verificar o diagnóstico.'}</p>}
    {props.currentAnalysisId && <p className="meta evidence-refs">Resultado exibido: <code>{props.currentAnalysisId}</code>.
      {' '}Dados até {date(props.currentAnalysisAt, true)} (corte dos dados).
      {job && (job.status === 'failed' || job.status === 'blocked') && !result ? ' O resultado validado exibido continua disponível. O impedimento abaixo se refere à solicitação acompanhada.' : ''}
      {active || state.error || state.phase === 'stopped' ? ' A última análise validada permanece disponível durante o acompanhamento.' : ''}
    </p>}
    <AnalysisTrackingStatus state={state} submissionError={submissionError} onRefresh={refresh} openJobs={openJobs}/>
    {result && job?.status === 'succeeded' && result.analysis_id !== props.currentAnalysisId && <p className="notice">
      {props.resultLoading ? 'Atualizando o resultado exibido…' : props.resultError ? 'Não foi possível carregar o resultado. A versão anterior foi preservada.' : 'O resultado desta execução difere da versão mais recente exibida. Consulte Execuções para identificar a execução.'}
      {props.resultError && <button className="text-button" onClick={() => onResult.current()}>Recarregar resultado</button>}
    </p>}
  </>;
}

const executionCategoryLabels:Record<AnalysisExecutionDiagnostic['diagnostic']['category'],string> = {
  nonzero_exit:'CLI encerrado com erro',
  output_schema_rejected:'Contrato de saída recusado pelo CLI',
  authentication:'Falha de autenticação do CLI',
  rate_limit:'Limite de uso do CLI',
  model_unavailable:'Modelo indisponível no CLI',
};
function ExecutionDiagnosticDetails({item}:{item:AnalysisExecutionDiagnostic}) {
  const diagnostic=item.diagnostic;
  const category=Object.hasOwn(executionCategoryLabels,diagnostic.category)
    ? executionCategoryLabels[diagnostic.category] : 'Categoria não reconhecida';
  const events=diagnostic.event_types.filter(event=>event==='error'||event==='turn.failed');
  return <details>
    <summary>Diagnóstico da execução · tentativa {item.attempt}</summary>
    <p>Etapa: Execução do CLI</p>
    <p>Categoria: {category}.</p>
    <p>Código de saída: {diagnostic.exit_code ?? 'Não informado'}.</p>
    <p className="meta">{events.length?`Eventos do CLI: ${events.join(' · ')}.`:'Sem evento estruturado disponível.'}</p>
    <p className="meta">Registrado em <time dateTime={item.recorded_at}>{date(item.recorded_at,true)}</time>.</p>
  </details>;
}

const numericReasonLabels:Record<NumericValidationReason,string> = {
  unsupported_format:'Formato numérico não suportado',
  unsupported_date:'Data sem suporte no conteúdo citado',
  unit_mismatch:'Unidade, escala ou moeda divergente',
  quantity_not_found:'Quantidade não encontrada na evidência citada',
  filing_account_unrecognized:'Conta do demonstrativo não reconhecida na afirmação',
  filing_account_ambiguous:'Mais de uma conta do demonstrativo identificada na afirmação',
  filing_period_unrecognized:'Período do demonstrativo não reconhecido na afirmação',
  filing_period_ambiguous:'Mais de um período do demonstrativo identificado na afirmação',
  filing_period_incompatible:'Período incompatível com a observação citada do demonstrativo',
};
function numericReasonText(issue:AnalysisValidationIssue) {
  const reason=issue.numeric_reason;
  return issue.code==='factual_number_not_in_cited_evidence' && typeof reason==='string' && Object.hasOwn(numericReasonLabels,reason)
    ? ` Causa: ${numericReasonLabels[reason]}.` : '';
}

export function AnalysisTrackingStatus({state, submissionError = '', onRefresh, openJobs}: {
  state: TrackingState; submissionError?: string; onRefresh: () => void; openJobs: () => void;
}) {
  const { snapshot, phase, error, stopReason } = state;
  const job = snapshot?.job;
  const outside = isOutsideManualScope(snapshot);
  const active = isCurrentAnalysisActive(snapshot);
  const indicator = outside ? 'neutral' : error || phase === 'stopped' || (job && !active && (job.status !== 'succeeded' || !snapshot?.result_analysis))
    ? 'attention' : job?.status === 'succeeded' && snapshot?.result_analysis ? 'complete' : active ? 'waiting' : 'neutral';
  const symbolKey = `${job?.id || 'none'}:${job?.status || 'none'}:${indicator}`;
  return <div className="analysis-tracking" aria-label="Acompanhamento da análise">
    {job && <p className="meta">Solicitação acompanhada: <code>{job.id}</code>.</p>}
    <p className="tracking-status" role="status" aria-live="polite" aria-atomic="true"><span key={symbolKey} className="tracking-symbol" data-kind={indicator} aria-hidden="true"><Icon name={indicator === 'complete' ? 'check' : indicator === 'attention' ? 'close' : 'jobs'}/></span><span>{!snapshot && (error || phase === 'stopped') ? 'Acompanhamento indisponível.' : analysisJobMessage(snapshot)}</span></p>
    {active && <p>Você pode sair e voltar ao ativo. O andamento é recuperado da fila salva.</p>}
    {outside && job && <p className="meta">Estado histórico registrado: {label(job.status)}.</p>}
    {!outside && snapshot?.queue_position != null && <p className="meta">{snapshot.manual_scope === 'current' ? 'Fila manual: uma solicitação por vez.' : 'Posição na fila pronta, incluindo coletas. Pode mudar; não é uma estimativa de tempo.'}</p>}
    {!outside && job?.status === 'running' && <p className="meta">Processamento e validação da resposta. O executor não informa percentual de conclusão.</p>}
    {!outside && (job?.status === 'retry_wait' || snapshot?.queue_position_reason === 'not_ready') && job?.not_before && <p className="meta">Liberada a partir de {date(job.not_before, true)}.</p>}
    {job?.error && <p className="notice">{job.error === 'local_request_budget_exhausted' ? 'Esta solicitação atingiu o limite diário de análises. Nenhuma chamada adicional foi iniciada por ela.' : job.error === 'cli_analysis_validation_rejected' ? 'A resposta desta solicitação foi recusada na validação.' : 'Esta solicitação encontrou um impedimento. Consulte os detalhes em Execuções.'}</p>}
    {snapshot?.execution_diagnostics?.map(item => <ExecutionDiagnosticDetails key={item.run_id} item={item}/>)}
    {snapshot?.validation_diagnostics?.map(item => <details key={item.run_id}><summary>Diagnóstico da validação · tentativa {item.attempt}</summary>
      <p>Etapa: {item.diagnostic.stage === 'schema' ? 'Estrutura da resposta' : 'Consistência com as evidências'}</p>
      <ul>{item.diagnostic.issues.map((issue, index) => <li key={index}>Regra: {issue.code}. Campo: {issue.path}{issue.rule ? `. Contrato: ${issue.rule}` : ''}.{numericReasonText(issue)}</li>)}</ul>
    </details>)}
    {submissionError && <p className="notice danger" role="alert">{submissionError}</p>}
    {error && <p className="notice danger" role="status">Consulta indisponível: {error} {snapshot ? 'O último estado confirmado pode estar desatualizado.' : 'Nenhum estado foi confirmado.'}</p>}
    {phase === 'stopped' && <p role="status">{stopReason === 'duration' ? 'A consulta automática parou após 10 minutos.' : 'A consulta automática parou após três falhas consecutivas.'} Isso não cancela a execução.</p>}
    {snapshot && <p className="meta">Última consulta confirmada: {date(snapshot.as_of, true)}.{job?.updated_at && <> Estado atualizado: {date(job.updated_at, true)}.</>}</p>}
    {phase === 'loading' && snapshot && <p className="meta">Atualizando acompanhamento…</p>}
    <div className="row wrap">
      <button className="micro-control" onClick={() => { if (phase !== 'loading' && phase !== 'watching') onRefresh(); }} aria-disabled={phase === 'loading' || phase === 'watching'}>{phase === 'stopped' ? 'Retomar acompanhamento' : 'Consultar acompanhamento'}</button>
      {job && <button className="micro-control" onClick={openJobs}>Ver execuções</button>}
    </div>
    <p className="meta">Consultar o andamento não solicita outra análise.</p>
  </div>;
}
