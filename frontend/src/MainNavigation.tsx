import { useEffect, useRef, useState } from 'react';
import { Icon } from './components';
import { query } from './format';

export function MainNavigation({items, view, params, navigate}: {
  items: string[][]; view: string; params: Record<string,string>; navigate: (view:string)=>void;
}) {
  const [open, setOpen] = useState(false);
  const toggle = useRef<HTMLButtonElement>(null);
  useEffect(() => setOpen(false), [view]);
  function close() {
    setOpen(false);
    toggle.current?.focus();
  }
  return <div className="main-navigation" onKeyDown={event => {
    if (event.key === 'Escape' && open) { event.preventDefault(); close(); }
  }}>
    <button ref={toggle} className="mobile-menu-toggle" aria-expanded={open} aria-controls="main-navigation-links"
      onClick={() => setOpen(value => !value)}>Menu · {items.find(item => item[0] === view)?.[1]}</button>
    <nav id="main-navigation-links" className={open ? 'navigation-links is-open' : 'navigation-links'} aria-label="Seções">
      {items.map(([key,title,icon]) => <a key={key} href={'?' + query({...params,view:key})}
        className={view === key ? 'nav-item current' : 'nav-item'} aria-current={view === key ? 'page' : undefined}
        onClick={event => {
          if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
          event.preventDefault();
          if (open) close();
          navigate(key);
        }}><Icon name={icon}/><span>{title}</span></a>)}
    </nav>
  </div>;
}
