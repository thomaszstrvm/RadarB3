import { describe, expect, it, vi } from 'vitest';
import { MutationCoordinator, MutationUncertainError, MUTATION_JOURNAL } from './mutations';
import { HTTPError, HTTPTimeoutError, SessionError } from './httpTransport';

function fixture() {
  const values = new Map<string, string>();
  const storage = {getItem: (key: string) => values.get(key) ?? null,
    setItem: vi.fn((key: string, value: string) => { values.set(key, value); }),
    removeItem: vi.fn((key: string) => { values.delete(key); })};
  const send = vi.fn<(...args: unknown[]) => Promise<unknown>>();
  const read = vi.fn<(...args: unknown[]) => Promise<unknown>>();
  const deps = {storage: () => storage, send: send as <T>(...args: unknown[]) => Promise<T>,
    read: read as <T>(...args: unknown[]) => Promise<T>, newKey: () => 'radar-test-key'};
  return {values, storage, send, read, deps, coordinator: new MutationCoordinator(deps)};
}
const receipt = (extra = {}) => ({key: 'radar-test-key', method: 'POST', path: '/api/v1/alert-rules',
  state: 'completed', status_code: 200, response: {id: 'one-rule'}, ...extra});

describe('Recuperação de gravação sem duplicação', () => {
  it('persiste identificação antes de enviar e impede dois cliques concorrentes', async () => {
    const f = fixture(); let resolve!: (value: unknown) => void;
    f.send.mockImplementation(() => { expect(f.values.has(MUTATION_JOURNAL)).toBe(true); return new Promise(done => {resolve = done;}); });
    const first = f.coordinator.execute('/alert-rules', 'POST', {name: 'Teste'});
    await expect(f.coordinator.execute('/alert-rules', 'POST', {name: 'Teste'})).rejects.toBeInstanceOf(MutationUncertainError);
    expect(f.send).toHaveBeenCalledTimes(1);
    resolve({id: 'one-rule'}); await first;
    expect(f.coordinator.getSnapshot().phase).toBe('idle');
    expect(f.values.size).toBe(0);
  });
  it('resposta perdida depois do commit é recuperada com um GET e nenhum segundo POST', async () => {
    const f = fixture(); f.send.mockRejectedValue(new HTTPTimeoutError()); f.read.mockResolvedValue(receipt());
    await expect(f.coordinator.execute('/alert-rules', 'POST', {name: 'Teste'})).resolves.toEqual({id: 'one-rule'});
    expect(f.send).toHaveBeenCalledTimes(1); expect(f.read).toHaveBeenCalledTimes(1);
    expect(f.values.size).toBe(0);
    expect(f.coordinator.getSnapshot().resolution).toMatchObject({success: true, command: {key: 'radar-test-key', body: {name: 'Teste'}}, response: {id: 'one-rule'}});
  });
  it('404 de recibo mantém resultado incerto e bloqueia nova ação após reload', async () => {
    const f = fixture(); f.send.mockRejectedValue(new TypeError('offline')); f.read.mockRejectedValue(new HTTPError(404, {}));
    await expect(f.coordinator.execute('/alert-rules', 'POST', {name: 'Teste'})).rejects.toBeInstanceOf(MutationUncertainError);
    const reloaded = new MutationCoordinator(f.deps);
    expect(reloaded.getSnapshot().pending?.body).toEqual({name: 'Teste'});
    await expect(reloaded.execute('/jobs', 'POST', {kind: 'refresh'})).rejects.toBeInstanceOf(MutationUncertainError);
    await expect(reloaded.recover()).rejects.toBeInstanceOf(MutationUncertainError);
    expect(f.send).toHaveBeenCalledTimes(1);
  });
  it('reenvio explícito conserva a chave e o payload originais imutáveis', async () => {
    const f = fixture(); f.send.mockRejectedValueOnce(new HTTPTimeoutError()).mockResolvedValueOnce({id: 'one-rule'});
    f.read.mockRejectedValue(new HTTPError(404, {}));
    const body = {name: 'Original'};
    await expect(f.coordinator.execute('/alert-rules', 'POST', body)).rejects.toBeInstanceOf(MutationUncertainError);
    body.name = 'Editado depois';
    const reloaded = new MutationCoordinator(f.deps);
    await expect(reloaded.recover(true)).resolves.toEqual({id: 'one-rule'});
    expect(f.send.mock.calls[1]).toEqual(['/alert-rules', 'POST', {name: 'Original'}, 'radar-test-key']);
    expect(f.values.size).toBe(0);
  });
  it('reenvio consulta primeiro e retorna recibo já existente sem POST', async () => {
    const f = fixture(); f.send.mockRejectedValue(new HTTPTimeoutError()); f.read.mockRejectedValueOnce(new HTTPError(404, {}));
    await expect(f.coordinator.execute('/alert-rules', 'POST', {})).rejects.toBeInstanceOf(MutationUncertainError);
    f.read.mockResolvedValue(receipt());
    await expect(f.coordinator.recover(true)).resolves.toEqual({id: 'one-rule'});
    expect(f.send).toHaveBeenCalledTimes(1);
  });
  it('consulta indisponível não permite replay nem apaga identificação', async () => {
    const f = fixture(); f.send.mockRejectedValue(new HTTPTimeoutError()); f.read.mockRejectedValue(new HTTPError(503, {}));
    await expect(f.coordinator.execute('/alert-rules', 'POST', {})).rejects.toBeInstanceOf(MutationUncertainError);
    await expect(f.coordinator.recover(true)).rejects.toBeInstanceOf(MutationUncertainError);
    expect(f.send).toHaveBeenCalledTimes(1); expect(f.values.size).toBe(1);
  });
  it('sessão que falha antes do envio libera tentativa e não consulta recibo', async () => {
    const f = fixture(); f.send.mockRejectedValue(new SessionError('nenhum envio'));
    await expect(f.coordinator.execute('/alert-rules', 'POST', {})).rejects.toBeInstanceOf(SessionError);
    expect(f.coordinator.getSnapshot().phase).toBe('idle'); expect(f.read).not.toHaveBeenCalled();
  });
  it('recibo de rejeição encerra a incerteza sem declarar sucesso', async () => {
    const f = fixture(); f.send.mockRejectedValue(new HTTPTimeoutError());
    f.read.mockResolvedValue(receipt({status_code: 422, response: {detail: 'Regra inválida'}}));
    await expect(f.coordinator.execute('/alert-rules', 'POST', {})).rejects.toMatchObject({status: 422});
    expect(f.values.size).toBe(0); expect(f.coordinator.getSnapshot().phase).toBe('idle');
    expect(f.coordinator.getSnapshot().resolution?.success).toBe(false);
  });
  it('recibo incorreto não confirma resultado de outra ação', async () => {
    const f = fixture(); f.send.mockRejectedValue(new HTTPTimeoutError());
    f.read.mockResolvedValue(receipt({key: 'outra-chave'}));
    await expect(f.coordinator.execute('/alert-rules', 'POST', {})).rejects.toBeInstanceOf(MutationUncertainError);
    expect(f.values.size).toBe(1);
  });
  it('compara caminho decodificado da API para ativo com dois pontos', async () => {
    const f = fixture(); f.send.mockRejectedValue(new HTTPTimeoutError());
    f.read.mockResolvedValue(receipt({path: '/api/v1/assets/qa:DEMO3/analyse'}));
    await expect(f.coordinator.execute('/assets/qa%3ADEMO3/analyse', 'POST', {})).resolves.toEqual({id: 'one-rule'});
  });
  it('falha de armazenamento impede despacho antes do primeiro efeito', async () => {
    const f = fixture(); f.storage.setItem.mockImplementation(() => {throw new Error('storage');});
    await expect(f.coordinator.execute('/alert-rules', 'POST', {})).rejects.toThrow('Nenhuma gravação foi enviada');
    expect(f.send).not.toHaveBeenCalled(); expect(f.coordinator.getSnapshot().phase).toBe('idle');
  });
  it('journal corrompido não é apagado nem permite nova gravação', async () => {
    const f = fixture(); f.values.set(MUTATION_JOURNAL, '{quebrado');
    const coordinator = new MutationCoordinator(f.deps);
    await expect(coordinator.execute('/alert-rules', 'POST', {})).rejects.toBeInstanceOf(MutationUncertainError);
    expect(f.values.get(MUTATION_JOURNAL)).toBe('{quebrado'); expect(f.send).not.toHaveBeenCalled();
  });
  it('falha ao limpar journal conserva proteção para um reload posterior', async () => {
    const f = fixture(); f.send.mockResolvedValue({id: 'one-rule'});
    f.storage.removeItem.mockImplementationOnce(() => {throw new Error('storage');});
    await expect(f.coordinator.execute('/alert-rules', 'POST', {})).rejects.toBeInstanceOf(MutationUncertainError);
    expect(f.values.size).toBe(1);
    f.read.mockResolvedValue(receipt());
    await new MutationCoordinator(f.deps).recover();
    expect(f.send).toHaveBeenCalledTimes(1); expect(f.values.size).toBe(0);
  });
});
