import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { canRetryJob, OperationalStatus } from './OperationalStatus';
import type { Operations } from './types';
import { date } from './format';

const fixture = (status: Operations['worker']['status'] = 'active'): Operations => ({
  execution_mode: 'collection-only',
  worker: {status, heartbeat_at: status === 'absent' ? null : '2026-10-29T18:00:00Z', expires_at: null, execution_mode: status === 'absent' ? null : 'collection-only'},
  ai: {enabled: false, homologated: true, execution_mode: 'collection-only'},
  collection: {sources: [{provider_id: 'br_g1', last_attempt_at: '2026-10-29T17:00:00Z', last_success_at: '2026-10-29T16:00:00Z', error_code: 'http_429', next_attempt_at: '2026-10-29T18:30:00Z'}], latest_news_fetch: '2026-10-29T16:00:00Z', latest_price_fetch: null, latest_macro_fetch: null},
  latest_analysis_snapshot: '2026-10-27T12:00:00Z', queried_at: '2026-10-29T18:01:00Z',
});

describe('Estado operacional com relógios separados', () => {
  it('não confunde homologação com inferência nem a consulta com coleta', () => {
    const html = renderToStaticMarkup(<OperationalStatus data={fixture()} />);
    expect(html).toContain('Coleta sem IA');
    expect(html).toContain('Worker ativo');
    expect(html).toContain('IA homologada</dt><dd>Sim');
    expect(html).toContain('Inferência disponível neste modo</dt><dd>Não');
    expect(html).toContain('Última coleta bem-sucedida de notícias');
    expect(html).toContain('Snapshot da análise mais recente');
    expect(html).toContain('não é a atualização dos dados nem da análise');
    expect(html).toContain('Nenhuma coleta registrada');
    expect(html).toContain('br_g1');
    expect(html).toContain('http_429');
    expect(html).toContain('aguardando autorização');
  });

  it.each([
    ['absent', 'Sem coleta em andamento'],
    ['stale', 'Coleta sem confirmação'],
  ] as const)('exibe worker %s sem alegar automação', (status, message) => {
    const html = renderToStaticMarkup(<OperationalStatus data={fixture(status)} />);
    expect(html).toContain(message);
    expect(html).not.toContain('Worker ativo');
  });

  it('modo com IA não remove as condições de homologação, quota e worker', () => {
    const data: Operations = {...fixture(), execution_mode: 'analysis-enabled', ai: {enabled: true, homologated: true}};
    const html = renderToStaticMarkup(<OperationalStatus data={data} />);
    expect(html).toContain('Modo com IA');
    expect(html).toContain('sujeito à homologação, à quota e ao worker ativo');
    expect(html).toContain('Inferência disponível neste modo</dt><dd>Sim');
  });

  it('falha de consulta não apresenta estado anterior como recém-confirmado', () => {
    expect(renderToStaticMarkup(<OperationalStatus error="Servidor offline" />)).toContain('não confirma que a coleta esteja ativa');
    expect(renderToStaticMarkup(<OperationalStatus data={fixture()} error="Servidor offline" />)).toContain('última consulta recebida');
  });

  it('distingue sucesso de coleta recente de dados recebidos anteriormente', () => {
    const data = fixture();
    data.collection.latest_news_data_at = '2026-10-28T12:00:00Z';
    const html = renderToStaticMarkup(<OperationalStatus data={data} />);
    expect(html).toContain('Última coleta bem-sucedida de notícias</dt><dd>' + date(data.collection.latest_news_fetch, true));
    expect(html).toContain('Último dado de notícias recebido</dt><dd>' + date(data.collection.latest_news_data_at, true));
    expect(html).toContain('sem criar nova versão');
    expect(html).toContain('Nenhum dado recebido');
  });

  it('avisa sobre modo divergente sem prometer bloqueio do worker com IA', () => {
    const data = fixture();
    data.mode_mismatch = true;
    data.worker.execution_mode = 'analysis-enabled';
    const html = renderToStaticMarkup(<OperationalStatus data={data} />);
    expect(html).toContain('role="alert"');
    expect(html).toContain('API e worker estão em modos diferentes');
    expect(html).toContain('Um worker com IA pode executar análises já na fila');
    expect(html).toContain('este painel não altera esse processo');
    expect(html).not.toContain('Análises pendentes ficam preservadas, aguardando autorização');
  });

  it('prioriza motivo de espera sobre erro histórico sem rotular espera como falha', () => {
    const data = fixture();
    data.collection.sources = [{provider_id:'market_calendar',last_attempt_at:null,last_success_at:null,
      reason:'Aguardando o fechamento da sessão documentada.',error_code:'verified_session_calendar_required',
      details:{automatic_prices:'waiting_for_close',reason_code:'correction_period_not_ended'},
      next_attempt_at:'2026-11-04T22:00:01Z'}];
    const html=renderToStaticMarkup(<OperationalStatus data={data}/>);
    expect(html).toContain('Estado: Aguardando o fechamento da sessão documentada.');
    expect(html).toContain('Aguardar até');
    expect(html).not.toContain('Falha:');
    expect(html).not.toContain('verified_session_calendar_required');
  });

  it.each(['analyse', 'analysis'])('bloqueia retry %s sem bloquear coleta', kind => {
    const control={allowed_kinds:[kind,'news'],reason:'ready',expires_at:new Date(Date.now()+90_000).toISOString()};
    expect(canRetryJob({kind}, false,'analysis-enabled',control)).toBe(false);
    expect(canRetryJob({kind}, true,'analysis-enabled',control)).toBe(true);
    expect(canRetryJob({kind: 'news'}, false,'collection-only',control)).toBe(true);
    expect(canRetryJob({kind: 'news'}, false,'collection-only')).toBe(false);
  });
});
