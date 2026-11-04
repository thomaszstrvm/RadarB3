export const HTTP_TIMEOUT_MS = 10_000;
export class HTTPError extends Error {
  constructor(public status: number, public payload: unknown) {
    const detail = payload && typeof payload === 'object' ? (payload as {detail?: unknown}).detail : null;
    super(typeof detail === 'string' ? detail : `Falha na consulta (${status}).`);
  }
}
export class HTTPTimeoutError extends Error {
  constructor() { super('A resposta excedeu o prazo de 10 segundos.'); }
}
export class SessionError extends Error {}

/** The deadline covers headers AND body, even if a transport ignores abort. */
export function createTransport(fetcher: typeof fetch = fetch, timeoutMs = HTTP_TIMEOUT_MS) {
  let tokenPromise: Promise<string> | undefined;
  async function bounded<T>(path: string, init: RequestInit = {}, signal?: AbortSignal): Promise<T> {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let onAbort: () => void;
    const stopped = new Promise<never>((_, reject) => {
      onAbort = () => { controller.abort(); reject(new DOMException('Consulta cancelada.', 'AbortError')); };
      signal?.addEventListener('abort', onAbort, {once: true});
      timer = setTimeout(() => { controller.abort(); reject(new HTTPTimeoutError()); }, timeoutMs);
      if (signal?.aborted) onAbort();
    });
    const operation = (async () => {
      if (signal?.aborted) throw new DOMException('Consulta cancelada.', 'AbortError');
      const response = await fetcher('/api/v1' + path, {...init, credentials: 'same-origin', signal: controller.signal});
      if (response.status === 204) return undefined as T;
      const payload = await response.json();
      if (!response.ok) throw new HTTPError(response.status, payload);
      return payload as T;
    })();
    try { return await Promise.race([operation, stopped]); }
    finally { clearTimeout(timer!); signal?.removeEventListener('abort', onAbort!); }
  }
  async function session(): Promise<string> {
    if (!tokenPromise) {
      const pending = bounded<{token: string}>('/session').then(value => {
        if (typeof value.token !== 'string' || !value.token) throw new Error('Sessão local inválida.');
        return value.token;
      });
      tokenPromise = pending;
      pending.catch(() => { if (tokenPromise === pending) tokenPromise = undefined; });
    }
    try { return await tokenPromise; }
    catch { throw new SessionError('Não foi possível iniciar a sessão local no prazo. Nenhuma nova gravação foi enviada. Tente novamente.'); }
  }
  return {
    read: <T>(path: string, signal?: AbortSignal) => bounded<T>(path, {headers: {Accept: 'application/json'}}, signal),
    async write<T>(path: string, method: string, body: unknown, key: string): Promise<T> {
      const token = await session();
      try {
        return await bounded<T>(path, {method, headers: {
          Accept: 'application/json', 'Content-Type': 'application/json', 'X-Radar-Token': token, 'Idempotency-Key': key,
        }, body: body === undefined ? undefined : JSON.stringify(body)});
      } catch (error) {
        if (error instanceof HTTPError && error.status === 403) tokenPromise = undefined;
        throw error;
      }
    },
  };
}
