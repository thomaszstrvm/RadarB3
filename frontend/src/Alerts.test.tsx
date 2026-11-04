import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import App from './App';
import type { Alert } from './types';

const fixture = vi.hoisted(() => ({ body: 'Descrição pública do alerta de teste.' }));
vi.mock('./api', () => ({
  useMutationState: () => ({pending: null, phase: 'idle', problem: ''}),
  request: vi.fn(() => { throw new Error('Network forbidden in this regression'); }),
  useData: (path: string) => ({
    loading: false, error: '',
    data: path === '/alerts' ? { items: [{ id: 'qa-alert', title: 'Alerta sintético', body: fixture.body, read: false, archived: false } satisfies Alert] }
      : path === '/alert-rules' ? { items: [] } : undefined,
  }),
}));

afterEach(() => vi.unstubAllGlobals());

describe('Contrato do texto de alertas', () => {
  it('renderiza body recebido da API e preserva as ações do alerta', () => {
    vi.stubGlobal('window', { location: { search: '?view=alerts' } });
    fixture.body = 'Descrição pública do alerta de teste.';
    const html = renderToStaticMarkup(<App />);
    expect(html).toContain('<p>Descrição pública do alerta de teste.</p>');
    expect(html).toContain('Marcar como lido');
    expect(html).toContain('Arquivar');
  });
  it('mantém HTML recebido como texto, sem executá-lo', () => {
    vi.stubGlobal('window', { location: { search: '?view=alerts' } });
    fixture.body = '<script>fonteNaoConfiavel()</script>';
    const html = renderToStaticMarkup(<App />);
    expect(html).toContain('&lt;script&gt;fonteNaoConfiavel()&lt;/script&gt;');
    expect(html).not.toContain('<script>');
  });
});
