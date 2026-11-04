import { useEffect, useState, useSyncExternalStore } from 'react';
import { createTransport } from './httpTransport';
import { MutationCoordinator } from './mutations';

const transport = createTransport();
export const mutations = new MutationCoordinator({
  storage: () => window.sessionStorage, send: transport.write, read: transport.read,
});
export const useMutationState = () => useSyncExternalStore(mutations.subscribe, mutations.getSnapshot, mutations.getSnapshot);

export function request<T>(path: string, method = 'GET', body?: unknown, signal?: AbortSignal): Promise<T> {
  return method === 'GET' ? transport.read<T>(path, signal) : mutations.execute<T>(path, method, body);
}
export function useData<T>(path: string, revision = 0, pollMs = 0) {
  const [response, setResponse] = useState<{data: T; path: string; revision: number}>();
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let alive = true;
    let controller: AbortController | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const clearNext = () => { clearTimeout(timer); timer = undefined; };
    const visible = () => !pollMs || document.visibilityState === 'visible';
    const read = async () => {
      if (!alive || controller || !visible()) return;
      clearNext();
      controller = new AbortController();
      setLoading(true); setError('');
      try {
        const data = await request<T>(path, 'GET', undefined, controller.signal);
        if (alive) setResponse({data, path, revision});
      } catch (error) {
        if (alive) setError(error instanceof Error ? error.message : 'Falha de conexão com a aplicação local.');
      } finally {
        controller = undefined;
        if (alive) {
          setLoading(false);
          if (pollMs > 0 && visible()) timer = setTimeout(read, pollMs);
        }
      }
    };
    const visibilityChanged = () => { if (visible()) void read(); else clearNext(); };
    if (pollMs > 0) document.addEventListener('visibilitychange', visibilityChanged);
    void read();
    return () => {
      alive = false; clearNext(); controller?.abort();
      if (pollMs > 0) document.removeEventListener('visibilitychange', visibilityChanged);
    };
  }, [path, revision, pollMs]);
  // The retained data can still be displayed, but belongs to its original query.
  const current = response?.path === path && response?.revision === revision && !loading && !error;
  return {data: response?.data, error, loading, current};
}
