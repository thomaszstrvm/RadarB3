// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MainNavigation } from './MainNavigation';
let container:HTMLDivElement, root:Root;
const items=[['market','Mercado','chart'],['sources','Fontes e cobertura','sources']];
beforeEach(()=>{vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT',true);container=document.createElement('div');document.body.append(container);root=createRoot(container);});
afterEach(async()=>{await act(async()=>root.unmount());container.remove();vi.unstubAllGlobals();});
it('announces current screen, toggles menu, closes on Escape and restores focus',async()=>{
  await act(async()=>root.render(<MainNavigation items={items} view="sources" params={{search:'TEST3',asset:'fixture'}} navigate={vi.fn()}/>));
  const toggle=container.querySelector('button')!;
  expect(toggle.textContent).toBe('Menu · Fontes e cobertura');
  expect(toggle.getAttribute('aria-expanded')).toBe('false');
  expect(container.querySelector('[aria-current="page"]')?.textContent).toBe('Fontes e cobertura');
  await act(async()=>toggle.click());
  expect(toggle.getAttribute('aria-expanded')).toBe('true');
  const link=container.querySelector('a')!;link.focus();
  await act(async()=>link.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true})));
  expect(toggle.getAttribute('aria-expanded')).toBe('false');expect(document.activeElement).toBe(toggle);
});
it('preserves URL context, honors modified clicks and closes after ordinary navigation',async()=>{
  const navigate=vi.fn();
  await act(async()=>root.render(<MainNavigation items={items} view="market" params={{search:'TEST3',asset:'fixture',page:'2'}} navigate={navigate}/>));
  const link=container.querySelectorAll('a')[1];
  const params=new URL(link.href).searchParams;
  expect(params.get('search')).toBe('TEST3');expect(params.get('asset')).toBe('fixture');expect(params.get('page')).toBe('2');
  // Cancel native navigation in the test while checking the component preserves modifier semantics.
  link.addEventListener('click',event=>event.preventDefault());
  await act(async()=>link.dispatchEvent(new MouseEvent('click',{bubbles:true,ctrlKey:true,cancelable:true})));
  expect(navigate).not.toHaveBeenCalled();
  await act(async()=>container.querySelector('button')!.click());
  await act(async()=>link.click());expect(navigate).toHaveBeenCalledWith('sources');
  expect(container.querySelector('button')?.getAttribute('aria-expanded')).toBe('false');
});
