import { useEffect, useRef, useState } from 'react';
import { Icon } from './components';

/** The star reflects a confirmed write, never a hover or an optimistic request. */
export function FavoriteButton({saved, busy, uncertain = false, resolutionKey, onChange}: {
  saved: boolean; busy: boolean; uncertain?: boolean; resolutionKey?: string; onChange: (next: boolean) => Promise<boolean>;
}) {
  const [confirmed, setConfirmed] = useState(saved);
  const [pending, setPending] = useState(false);
  const [failed, setFailed] = useState(false);
  const pendingRef = useRef(false);
  const mounted = useRef(false);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => { setConfirmed(saved); setFailed(false); }, [saved]);
  useEffect(() => { if (resolutionKey) setFailed(false); }, [resolutionKey]);

  async function toggle() {
    if (busy || pendingRef.current) return;
    pendingRef.current = true;
    setPending(true);
    setFailed(false);
    const next = !confirmed;
    try {
      const persisted = await onChange(next);
      if (!mounted.current) return;
      if (persisted) setConfirmed(next);
      else setFailed(true);
    } catch {
      if (mounted.current) setFailed(true);
    } finally {
      pendingRef.current = false;
      if (mounted.current) setPending(false);
    }
  }

  return <span className="favorite-control">
    <button className="text-button micro-control favorite-button" aria-pressed={confirmed}
      aria-disabled={busy || pending} aria-label="Favorito" onClick={toggle}>
      <span className="favorite-symbol" data-saved={confirmed} aria-hidden="true"><Icon name="star" size={18}/></span>
      <span>Favorito</span>
      <span className="favorite-feedback" aria-hidden="true">{uncertain ? 'A confirmar' : pending ? 'Salvando…' : failed ? 'Falhou' : confirmed ? 'Salvo' : 'Adicionar'}</span>
    </button>
    <span className="sr-only" role="status">{uncertain ? 'Resultado do favorito a confirmar. Use a recuperação segura acima.' : pending ? 'Salvando favorito.' : failed ? 'Não foi possível alterar o favorito. Tente novamente.' : ''}</span>
  </span>;
}
