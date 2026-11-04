import { useEffect, useRef } from 'react';
import { Icon } from './components';
import { label } from './format';

const filterNames = {search: 'Busca', sector: 'Setor', type: 'Tipo', quality: 'Vínculo', state: 'Cobertura'};
export type FilterKey = keyof typeof filterNames;

function valueLabel(key:FilterKey, value:string) {
  if (key==='search'||key==='sector') return value;
  if (key==='type' && value==='unknown') return 'Não classificados';
  if (key==='type' && value==='all') return 'Elegíveis e não classificados';
  if (key==='type' && value==='eligible') return 'Ações e units elegíveis';
  if (key==='quality') {
    if (value==='low') return 'Com eventos vinculados';
    if (value==='unknown') return 'Sem eventos vinculados';
    return 'Filtro anterior · '+label(value);
  }
  return label(value);
}

export function activeFilterEntries(params: Record<string, string>) {
  return (Object.keys(filterNames) as FilterKey[]).filter(key => !!params[key]).map(key => ({
    key, text: `${filterNames[key]}: ${valueLabel(key,params[key])}`,
  }));
}

/** Keep this component mounted when empty so the last removal can restore focus. */
export function ActiveFilterChips({params, onRemove, onClear}: {
  params: Record<string, string>; onRemove: (key: FilterKey) => void; onClear: () => void;
}) {
  const entries = activeFilterEntries(params);
  const controls = useRef(new Map<FilterKey, HTMLButtonElement>());
  const focusAfterRemoval = useRef<{key?: FilterKey} | null>(null);
  const signature = entries.map(entry => entry.key).join('|');

  useEffect(() => {
    const target = focusAfterRemoval.current;
    if (!target) return;
    focusAfterRemoval.current = null;
    const next = target.key ? controls.current.get(target.key) : undefined;
    (next ?? document.getElementById('market-filter-toggle'))?.focus();
  }, [signature]);

  function remove(key: FilterKey) {
    const index = entries.findIndex(entry => entry.key === key);
    focusAfterRemoval.current = {key: (entries[index + 1] ?? entries[index - 1])?.key};
    onRemove(key);
  }

  return entries.length ? <div className="active-filters filter-chips" role="group" aria-label="Filtros ativos">
    <span>Filtros ativos</span>
    {entries.map(entry => <button key={entry.key} type="button" className="filter-chip micro-control"
      ref={button => {if (button) controls.current.set(entry.key, button); else controls.current.delete(entry.key);}}
      aria-label={`Remover filtro ${entry.text}`} onClick={() => remove(entry.key)}>
      <span>{entry.text}</span><Icon name="close" size={14}/>
    </button>)}
    <button type="button" className="text-button" onClick={() => {focusAfterRemoval.current = {}; onClear();}}>Limpar filtros</button>
  </div> : null;
}
