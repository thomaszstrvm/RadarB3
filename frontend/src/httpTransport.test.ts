import { afterEach, describe, expect, it, vi } from 'vitest';
import { createTransport, HTTPError, HTTPTimeoutError, SessionError } from './httpTransport';

afterEach(() => vi.useRealTimers());
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), {status});

describe('HTTP com espera limitada', () => {
  it('encerra sessão pendente sem enviar gravação e permite sessão nova', async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn<typeof fetch>().mockImplementationOnce(() => new Promise(() => {}))
      .mockResolvedValueOnce(response({token: 'test-session'})).mockResolvedValueOnce(response({saved: true}));
    const transport = createTransport(fetcher, 100);
    const result = transport.write('/watchlist', 'POST', {}, 'radar-one').catch(error => error);
    await vi.advanceTimersByTimeAsync(101);
    expect(await result).toBeInstanceOf(SessionError);
    expect(fetcher).toHaveBeenCalledTimes(1);
    await expect(transport.write('/watchlist', 'POST', {}, 'radar-two')).resolves.toEqual({saved: true});
    expect(fetcher).toHaveBeenCalledTimes(3);
    expect(fetcher.mock.calls[2][1]?.headers).toMatchObject({'Idempotency-Key': 'radar-two'});
  });
  it('limita também corpo JSON que nunca termina e aborta a conexão', async () => {
    vi.useFakeTimers();
    let signal: AbortSignal | null | undefined;
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (_, init) => {
      signal = init?.signal;
      return {ok: true, status: 200, json: () => new Promise(() => {})} as Response;
    });
    const result = createTransport(fetcher, 100).read('/jobs').catch(error => error);
    await vi.advanceTimersByTimeAsync(101);
    expect(await result).toBeInstanceOf(HTTPTimeoutError);
    expect(signal?.aborted).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
  });
  it('sinal externo já cancelado não envia GET e limpa timer', async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn<typeof fetch>();
    const abort = new AbortController(); abort.abort();
    await expect(createTransport(fetcher).read('/jobs', abort.signal)).rejects.toMatchObject({name: 'AbortError'});
    expect(fetcher).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });
  it('403 invalida sessão sem repetir a gravação automaticamente', async () => {
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(response({token: 'test-1'})).mockResolvedValueOnce(response({detail: 'expirada'}, 403))
      .mockResolvedValueOnce(response({token: 'test-2'})).mockResolvedValueOnce(response({saved: true}));
    const transport = createTransport(fetcher);
    await expect(transport.write('/settings', 'PATCH', {}, 'radar-one')).rejects.toBeInstanceOf(HTTPError);
    expect(fetcher).toHaveBeenCalledTimes(2);
    await transport.write('/settings', 'PATCH', {}, 'radar-two');
    expect(fetcher.mock.calls[2][0]).toBe('/api/v1/session');
    expect(fetcher.mock.calls[3][1]?.headers).toMatchObject({'X-Radar-Token': 'test-2'});
  });
  it('limita gravação cujo transporte ignora abort sem reenvio oculto', async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(response({token: 'test'}))
      .mockImplementationOnce(() => new Promise(() => {}));
    const result = createTransport(fetcher, 100).write('/jobs', 'POST', {}, 'radar-one').catch(error => error);
    await vi.advanceTimersByTimeAsync(101);
    expect(await result).toBeInstanceOf(HTTPTimeoutError);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
});
