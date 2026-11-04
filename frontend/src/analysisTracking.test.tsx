import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { AnalysisTrackingStatus } from './AnalysisTracking';
import { watchAnalysis, type TrackingState } from './pollAnalysis';
import type { AnalysisJob } from './types';

const snapshot = (status: string | null = 'queued'): AnalysisJob => ({
  asset_id: 'qa:DEMO3', as_of: '2026-10-26T20:00:00Z', queue_position: status === 'queued' ? 2 : null,
  queue_position_reason: null, result_analysis: null,
  job: status ? { id: 'job-1', kind: 'analyse', status, created_at: '2026-10-26T19:00:00Z' } : null,
});

describe('Acompanhamento limitado, exclusivamente por consultas', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('consulta fila, execução e conclusão; para definitivamente no terminal', async () => {
    const read = vi.fn().mockResolvedValueOnce(snapshot()).mockResolvedValueOnce(snapshot('running')).mockResolvedValue(snapshot('succeeded'));
    const update = vi.fn();
    watchAnalysis('qa:DEMO3', read, update);
    await vi.advanceTimersByTimeAsync(20_000);
    expect(read).toHaveBeenCalledTimes(3);
    expect(update.mock.calls.map(([x]) => x.snapshot?.job.status)).toEqual(['queued', 'running', 'succeeded']);
    expect(vi.getTimerCount()).toBe(0);
  });

  it.each([null, 'failed', 'blocked', 'paused', 'cancelled', 'partial'])('não continua consultando estado %s', async status => {
    const read = vi.fn().mockResolvedValue(snapshot(status));
    watchAnalysis('qa:DEMO3', read, vi.fn());
    await vi.advanceTimersByTimeAsync(60_000);
    expect(read).toHaveBeenCalledTimes(1);
  });

  it('preserva o último snapshot durante três falhas, sem nova solicitação', async () => {
    let state: Partial<TrackingState> = {};
    const read = vi.fn().mockResolvedValueOnce(snapshot('running')).mockRejectedValue(new Error('Servidor offline'));
    watchAnalysis('qa:DEMO3', read, change => { state = { ...state, ...change }; });
    await vi.advanceTimersByTimeAsync(60_000);
    expect(read).toHaveBeenCalledTimes(4);
    expect(state.snapshot?.job?.status).toBe('running');
    expect(state).toMatchObject({ phase: 'stopped', stopReason: 'errors', error: 'Servidor offline' });
  });

  it('zera apenas a sequência de erros após uma consulta bem-sucedida', async () => {
    const read = vi.fn().mockRejectedValueOnce(Error('offline')).mockRejectedValueOnce(Error('offline')).mockResolvedValueOnce(snapshot('running')).mockRejectedValue(Error('offline'));
    const update = vi.fn();
    watchAnalysis('qa:DEMO3', read, update);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(read).toHaveBeenCalledTimes(6);
    expect(update).toHaveBeenLastCalledWith(expect.objectContaining({ stopReason: 'errors' }));
  });

  it('aborta a consulta travada após 10 s e nunca sobrepõe requisições', async () => {
    let concurrent = 0, peak = 0;
    const read = vi.fn((signal: AbortSignal) => new Promise<AnalysisJob>((_resolve, reject) => {
      peak = Math.max(peak, ++concurrent);
      signal.addEventListener('abort', () => { concurrent--; reject(new Error('aborted')); });
    }));
    const update = vi.fn();
    watchAnalysis('qa:DEMO3', read, update);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(read).toHaveBeenCalledTimes(3);
    expect(peak).toBe(1);
    expect(concurrent).toBe(0);
    expect(update).toHaveBeenLastCalledWith(expect.objectContaining({ phase: 'stopped', error: 'A consulta demorou demais.' }));
  });

  it('encerra após 10 minutos sem cancelar o job', async () => {
    const read = vi.fn().mockResolvedValue(snapshot('running'));
    const update = vi.fn();
    watchAnalysis('qa:DEMO3', read, update);
    await vi.advanceTimersByTimeAsync(660_000);
    expect(read).toHaveBeenCalledTimes(120);
    expect(update).toHaveBeenLastCalledWith({ phase: 'stopped', stopReason: 'duration' });
    expect(vi.getTimerCount()).toBe(0);
  });

  it('não consulta com a página oculta e mantém o teto de tempo', async () => {
    let visible = false;
    const read = vi.fn().mockResolvedValue(snapshot('running'));
    const update = vi.fn();
    watchAnalysis('qa:DEMO3', read, update, { isVisible: () => visible });
    await vi.advanceTimersByTimeAsync(12_000);
    expect(read).not.toHaveBeenCalled();
    visible = true;
    await vi.advanceTimersByTimeAsync(4_000);
    expect(read).toHaveBeenCalledTimes(1);
    visible = false;
    await vi.advanceTimersByTimeAsync(600_000);
    expect(read).toHaveBeenCalledTimes(1);
    expect(update).toHaveBeenLastCalledWith({ phase: 'stopped', stopReason: 'duration' });
    expect(vi.getTimerCount()).toBe(0);
  });

  it('troca de ativo/unmount aborta e ignora respostas atrasadas', async () => {
    let resolve!: (value: AnalysisJob) => void;
    let signal!: AbortSignal;
    const read = vi.fn((s: AbortSignal) => { signal = s; return new Promise<AnalysisJob>(r => { resolve = r; }); });
    const update = vi.fn();
    const stop = watchAnalysis('qa:DEMO3', read, update);
    stop();
    resolve(snapshot('succeeded'));
    await vi.advanceTimersByTimeAsync(60_000);
    expect(signal.aborted).toBe(true);
    expect(update).not.toHaveBeenCalled();
    expect(read).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('não aceita uma resposta atribuída a outro ativo', async () => {
    const update = vi.fn();
    watchAnalysis('qa:DEMO4', vi.fn().mockResolvedValue(snapshot('succeeded')), update);
    await vi.advanceTimersByTimeAsync(30_000);
    expect(update.mock.calls.every(([x]) => !x.snapshot)).toBe(true);
    expect(update).toHaveBeenLastCalledWith(expect.objectContaining({ phase: 'stopped', error: 'A consulta retornou outro ativo.' }));
  });
});

describe('Estados acessíveis sem resultados inventados', () => {
  const html = (state: TrackingState) => renderToStaticMarkup(<AnalysisTrackingStatus state={state} onRefresh={() => {}} openJobs={() => {}}/>);
  it('distingue resultado ausente de resultado persistido', () => {
    const data = snapshot('succeeded');
    expect(html({ snapshot: data, phase: 'idle', error: '' })).toContain('sem resultado validado associado');
    data.result_analysis = { analysis_id: 'analysis-1', status: 'insufficient_data', as_of: data.as_of };
    expect(html({ snapshot: data, phase: 'idle', error: '' })).toContain('resultado validado disponível');
  });
  it('mostra posição global, sem percentual nem estimativa inventada', () => {
    const markup = html({ snapshot: snapshot(), phase: 'watching', error: '' });
    expect(markup).toContain('posição 2');
    expect(markup).toContain('incluindo coletas');
    expect(markup).not.toContain('progressbar');
    expect(markup).toContain('aria-live="polite"');
  });
  it('identifica estado desatualizado e recuperação sem reenviar análise', () => {
    const markup = html({ snapshot: snapshot('running'), phase: 'stopped', stopReason: 'errors', error: 'offline' });
    expect(markup).toContain('pode estar desatualizado');
    expect(markup).toContain('Retomar acompanhamento');
    expect(markup).toContain('Isso não cancela a execução');
    expect(markup).toContain('não solicita outra análise');
    expect(markup).not.toContain('Analisar ativo');
  });
});
