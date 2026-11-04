import { HTTPError, SessionError } from './httpTransport';

export const MUTATION_JOURNAL = 'radar-pending-mutation-v1';
export type PendingMutation = {key: string; path: string; method: string; body?: unknown; createdAt: string};
export type MutationResolution = {command: PendingMutation; success: boolean; response: unknown};
export type MutationSnapshot = {
  pending: PendingMutation | null; phase: 'idle' | 'sending' | 'checking' | 'uncertain'; problem: string;
  resolution?: MutationResolution;
};
type Receipt = {key: string; method: string; path: string; state: string; status_code: number; response: unknown};
type Dependencies = {
  storage: () => Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;
  send: <T>(path: string, method: string, body: unknown, key: string) => Promise<T>;
  read: <T>(path: string) => Promise<T>;
  newKey?: () => string;
};
export class MutationUncertainError extends Error {
  constructor() { super('O resultado da gravação ainda não foi confirmado. Use a recuperação segura antes de iniciar outra ação.'); }
}
const unknownMessage = 'A conexão terminou sem confirmar o resultado. A ação pode ter sido salva. Novas gravações estão pausadas até a verificação.';

/** One outstanding command per tab, persisted BEFORE dispatch; never auto-replays. */
export class MutationCoordinator {
  private snapshot: MutationSnapshot = {pending: null, phase: 'idle', problem: ''};
  private listeners = new Set<() => void>();
  private working = false;
  constructor(private deps: Dependencies) {
    try {
      const text = deps.storage().getItem(MUTATION_JOURNAL);
      if (text) {
        const command = JSON.parse(text);
        if (!command || !/^radar-[a-zA-Z0-9_-]+$/.test(command.key) ||
            !['POST', 'PATCH', 'PUT', 'DELETE'].includes(command.method) ||
            typeof command.path !== 'string' || !command.path.startsWith('/') || command.path.startsWith('//') ||
            typeof command.createdAt !== 'string') throw new Error('Invalid journal');
        this.snapshot = {pending: command, phase: 'uncertain', problem: unknownMessage};
      }
    } catch {
      this.snapshot = {pending: null, phase: 'uncertain', problem: 'Não foi possível ler o registro local de recuperação. As gravações estão pausadas; restaure o armazenamento deste navegador e recarregue a página.'};
    }
  }
  getSnapshot = () => this.snapshot;
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  private update(snapshot: MutationSnapshot) {
    this.snapshot = snapshot;
    for (const listener of this.listeners) listener();
  }
  private clear(resolution?: MutationResolution) {
    try { this.deps.storage().removeItem(MUTATION_JOURNAL); }
    catch {
      this.update({...this.snapshot, phase: 'uncertain', problem: 'O resultado foi confirmado, mas o registro local não pôde ser atualizado. Restaure o armazenamento e consulte o resultado novamente.'});
      throw new MutationUncertainError();
    }
    this.update({pending: null, phase: 'idle', problem: '', resolution});
  }
  private async receipt(command: PendingMutation): Promise<Receipt | null> {
    let receipt: Receipt;
    try { receipt = await this.deps.read<Receipt>('/mutations/' + encodeURIComponent(command.key)); }
    catch (error) { if (error instanceof HTTPError && error.status === 404) return null; throw error; }
    if (receipt.key !== command.key || receipt.method !== command.method || receipt.path !== decodeURIComponent('/api/v1' + command.path) ||
        receipt.state !== 'completed' || !Number.isInteger(receipt.status_code) ||
        receipt.status_code < 200 || receipt.status_code >= 500 || !('response' in receipt)) {
      throw new Error('O recibo não corresponde ao comando pendente.');
    }
    return receipt;
  }
  private finish<T>(receipt: Receipt): T {
    this.clear({command: this.snapshot.pending!, success: receipt.status_code < 400, response: receipt.response});
    if (receipt.status_code >= 400) throw new HTTPError(receipt.status_code, receipt.response);
    return receipt.response as T;
  }
  private async reconcile<T>(command: PendingMutation): Promise<T> {
    this.update({...this.snapshot, phase: 'checking'});
    let receipt: Receipt | null;
    try { receipt = await this.receipt(command); }
    catch {
      this.update({...this.snapshot, phase: 'uncertain', problem: unknownMessage + ' A consulta do recibo também falhou.'});
      throw new MutationUncertainError();
    }
    if (receipt) return this.finish<T>(receipt);
    this.update({...this.snapshot, phase: 'uncertain', problem: unknownMessage + ' O recibo ainda não está disponível.'});
    throw new MutationUncertainError();
  }
  async execute<T>(path: string, method: string, body?: unknown): Promise<T> {
    if (this.working || this.snapshot.phase !== 'idle') throw new MutationUncertainError();
    this.working = true;
    try {
      const command: PendingMutation = JSON.parse(JSON.stringify({
        key: this.deps.newKey?.() ?? 'radar-' + crypto.randomUUID(), path, method, body, createdAt: new Date().toISOString(),
      }));
      try { this.deps.storage().setItem(MUTATION_JOURNAL, JSON.stringify(command)); }
      catch { throw new Error('Não foi possível guardar a identificação da ação neste navegador. Nenhuma gravação foi enviada.'); }
      this.update({pending: command, phase: 'sending', problem: ''});
      let result: T;
      try { result = await this.deps.send<T>(path, method, command.body, command.key); }
      catch (error) {
        if (error instanceof SessionError || (error instanceof HTTPError && error.status >= 400 && error.status < 500)) {
          this.clear(error instanceof HTTPError ? {command, success: false, response: error.payload} : undefined);
          throw error;
        }
        return await this.reconcile<T>(command);
      }
      this.clear({command, success: true, response: result});
      return result;
    } finally { this.working = false; }
  }
  async recover<T>(replay = false): Promise<T> {
    const command = this.snapshot.pending;
    if (!command || this.working) throw new MutationUncertainError();
    this.working = true;
    this.update({...this.snapshot, phase: 'checking', problem: ''});
    try {
      // A failed lookup never authorizes a replay; 404 means unknown, not cancelled.
      const receipt = await this.receipt(command);
      if (receipt) return this.finish<T>(receipt);
      if (replay) {
        // Explicit user retry uses the ORIGINAL key and immutable payload.
        let result: T;
        try { result = await this.deps.send<T>(command.path, command.method, command.body, command.key); }
        catch { return await this.reconcile<T>(command); }
        this.clear({command, success: true, response: result});
        return result;
      }
      throw new MutationUncertainError();
    } catch (error) {
      if (!this.snapshot.pending) throw error; // A rejected receipt is a confirmed outcome.
      this.update({...this.snapshot, phase: 'uncertain', problem: unknownMessage + ' Consulte novamente ou reenvie a mesma ação com segurança.'});
      throw new MutationUncertainError();
    } finally { this.working = false; }
  }
}
