import type { AnalysisJob } from './types';

export const isAnalysisActive = (status?: string) =>
  status === 'queued' || status === 'running' || status === 'retry_wait';

export const isOutsideManualScope = (snapshot?: AnalysisJob) =>
  snapshot?.manual_scope === 'outside' || snapshot?.queue_position_reason === 'manual_scope_inactive';

export const isCurrentAnalysisActive = (snapshot?: AnalysisJob) =>
  !isOutsideManualScope(snapshot) && isAnalysisActive(snapshot?.job?.status);

export type TrackingState = {
  snapshot?: AnalysisJob;
  phase: 'loading' | 'watching' | 'idle' | 'stopped';
  error: string;
  stopReason?: 'duration' | 'errors';
};

/** Only reads. Each request finishes before another starts; cleanup also aborts it. */
export function watchAnalysis(
  assetId: string,
  read: (signal: AbortSignal) => Promise<AnalysisJob>,
  update: (state: Partial<TrackingState>) => void,
  options: { intervalMs?: number; durationMs?: number; requestTimeoutMs?: number; isVisible?: () => boolean } = {},
) {
  const { intervalMs = 5000, durationMs = 600_000, requestTimeoutMs = 10_000, isVisible = () => true } = options;
  let stopped = false;
  let failures = 0;
  let next: ReturnType<typeof setTimeout> | undefined;
  let controller: AbortController | undefined;
  let requestTimer: ReturnType<typeof setTimeout> | undefined;
  const cleanup = () => {
    stopped = true;
    clearTimeout(next);
    clearTimeout(deadline);
    clearTimeout(requestTimer);
    controller?.abort();
  };
  const deadline = setTimeout(() => {
    cleanup();
    update({ phase: 'stopped', stopReason: 'duration' });
  }, durationMs);

  const poll = async () => {
    if (!isVisible()) { next = setTimeout(poll, intervalMs); return; }
    controller = new AbortController();
    requestTimer = setTimeout(() => controller?.abort(), requestTimeoutMs);
    try {
      const snapshot = await read(controller.signal);
      if (stopped) return;
      if (snapshot.asset_id !== assetId) throw new Error('A consulta retornou outro ativo.');
      failures = 0;
      const active = isCurrentAnalysisActive(snapshot);
      update({ snapshot, phase: active ? 'watching' : 'idle', error: '', stopReason: undefined });
      if (!active) { cleanup(); return; }
    } catch (error) {
      if (stopped) return;
      failures += 1;
      update({
        error: controller.signal.aborted ? 'A consulta demorou demais.' : error instanceof Error ? error.message : 'Falha ao consultar a execução.',
        phase: failures >= 3 ? 'stopped' : 'watching',
        stopReason: failures >= 3 ? 'errors' : undefined,
      });
      if (failures >= 3) { cleanup(); return; }
    } finally {
      clearTimeout(requestTimer);
    }
    if (!stopped) next = setTimeout(poll, intervalMs);
  };
  void poll();
  return cleanup;
}

export function analysisJobMessage(snapshot?: AnalysisJob): string {
  if (!snapshot) return 'Consultando acompanhamento da análise…';
  const job = snapshot.job;
  if (!job) return 'Nenhuma solicitação de análise registrada para este ativo.';
  if (isOutsideManualScope(snapshot)) return 'Solicitação histórica fora do escopo manual atual. Não integra esta sessão.';
  if (job.cancel_requested && isAnalysisActive(job.status)) return 'Cancelamento solicitado; aguardando confirmação.';
  switch (job.status) {
    case 'queued': return snapshot.queue_position == null ? 'Análise aguardando liberação para a fila.' : `Análise na fila · posição ${snapshot.queue_position}.`;
    case 'running': return 'Análise em andamento.';
    case 'retry_wait': return 'Análise aguardando nova tentativa programada.';
    case 'succeeded': {
      const result = job.result;
      const reused = result && typeof result === 'object' && 'cache_reused' in result && result.cache_reused === true && 'calls' in result && result.calls === 0;
      return snapshot.result_analysis ? reused ? 'Análise reutilizada · resultado validado disponível. Nenhuma nova chamada de IA.' : 'Execução concluída · resultado validado disponível.' : 'Execução concluída, mas sem resultado validado associado.';
    }
    case 'failed': return 'Esta solicitação falhou. Confira os detalhes em Execuções.';
    case 'blocked': return 'Esta solicitação foi bloqueada. Verifique o impedimento em Execuções.';
    case 'paused': return 'Análise pausada.';
    case 'cancelled': return 'Análise cancelada.';
    case 'partial': return 'Execução parcial. Confira os detalhes em Execuções.';
    default: return 'Estado da análise não reconhecido. Consulte Execuções.';
  }
}
