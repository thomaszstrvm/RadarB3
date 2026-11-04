import { useRef, useState, type MouseEvent } from 'react';
import { mutations, useMutationState } from './api';

export function MutationRecovery({onResolved}: {onResolved: (message: string, success: boolean) => void}) {
  const state = useMutationState();
  const [checking, setChecking] = useState(false);
  const guard = useRef(false);
  async function recover(replay: boolean, event: MouseEvent<HTMLButtonElement>) {
    if (guard.current) return;
    const source = event.currentTarget;
    let focusMoved = false;
    const noteFocus = (event: FocusEvent) => { if (event.target !== source) focusMoved = true; };
    document.addEventListener('focusin', noteFocus);
    guard.current = true; setChecking(true);
    try {
      await mutations.recover(replay);
      onResolved('A ação foi confirmada.', true);
    } catch (error) {
      if (!mutations.getSnapshot().pending) onResolved(error instanceof Error ? error.message : 'A ação foi rejeitada pelo servidor.', false);
    } finally {
      document.removeEventListener('focusin', noteFocus);
      guard.current = false; setChecking(false);
      // Restore only our own removed control; don't steal focus after navigation.
      if (!mutations.getSnapshot().pending && !focusMoved) requestAnimationFrame(() => {
        const active = document.activeElement;
        if (active === source || active === document.body) (document.getElementById('action-error') ?? document.getElementById('mutation-result'))?.focus();
      });
    }
  }
  if (state.phase === 'idle' || state.phase === 'sending') return null;
  return <section className="notice mutation-recovery" role="status" aria-live="polite" aria-labelledby="mutation-recovery-title">
    <h2 id="mutation-recovery-title">{state.phase === 'checking' ? 'Verificando a gravação…' : 'Resultado da gravação a confirmar'}</h2>
    <p>{state.problem || 'Consultando o recibo do servidor. Nenhuma ação será repetida automaticamente.'}</p>
    {state.pending && <>
      <p className="meta">Solicitada em {new Date(state.pending.createdAt).toLocaleString('pt-BR')}. Você pode navegar e consultar dados enquanto verifica.</p>
      <div className="row wrap">
        <button disabled={checking || state.phase === 'checking'} onClick={event => recover(false, event)}>Consultar resultado</button>
        <button disabled={checking || state.phase === 'checking'} onClick={event => recover(true, event)}>Reenviar a mesma ação</button>
      </div>
      <p className="meta">O reenvio preserva a identificação original. Se já foi salva, o servidor devolve o recibo sem executar novamente.</p>
    </>}
  </section>;
}
