import { useState } from 'react';
import { applyMotionPreference, readAppliedMotionPreference, saveMotionPreference, type MotionPreference } from './motionPreference';

const sessionNotice = 'Preferência aplicada nesta página. Não foi possível salvar no navegador; ela poderá ser perdida ao recarregar.';

export function MotionPreferenceControl() {
  const page = typeof document === 'undefined' ? undefined : document.documentElement;
  const [preference, setPreference] = useState(() => readAppliedMotionPreference(page));
  const [notice, setNotice] = useState(() => page?.dataset.motionPersistence === 'session' ? sessionNotice : '');

  function change(preference: MotionPreference) {
    applyMotionPreference(preference, document.documentElement);
    setPreference(preference);
    const saved = saveMotionPreference(preference);
    document.documentElement.dataset.motionPersistence = saved ? 'saved' : 'session';
    setNotice(saved ? 'Preferência aplicada e salva neste navegador.' : sessionNotice);
  }

  return <section className="surface motion-preferences" aria-labelledby="motion-title">
    <h2 id="motion-title">Conforto visual</h2>
    <label>Movimento da interface
      <select value={preference} onChange={e => change(e.target.value as MotionPreference)} aria-describedby="motion-help">
        <option value="system">Automático — seguir o sistema</option>
        <option value="reduce">Reduzido</option>
      </select>
    </label>
    <p id="motion-help" className="meta">A mudança é imediata e vale apenas neste navegador. No modo reduzido, os estados e confirmações continuam visíveis, sem animações.</p>
    <p role="status" className="meta">{notice}</p>
  </section>;
}
