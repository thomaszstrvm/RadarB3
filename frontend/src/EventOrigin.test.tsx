// @vitest-environment jsdom
import { afterEach,expect,it,vi } from 'vitest';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { EventOrigin } from './EventOrigin';
import type { AssetDetail } from './types';

const asset:AssetDetail={id:'brapi:TEST3',ticker:'TEST3',name:'Teste',price:null,watchlisted:false,return_21:null,
  prices:[],metrics:[],analysis:null,articles:[],events:[{id:'other',title:'Outro assunto'},
    {id:'origin',title:'Evento escolhido',first_seen_at:'2026-11-02T19:00:00Z',
    articles:[{id:'art',source:'Veículo de teste',title:'Evento escolhido',url:'https://example.org/edicao'}]}]};
const container=document.createElement('div');
let root:ReturnType<typeof createRoot>;
async function render(id:string,selectedAsset:string) {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT',true);
  history.replaceState(null,'','?'+new URLSearchParams({view:'market',asset:asset.id,origin_event:id,origin_asset:selectedAsset,event_search:'por título',event_page:'3'}));
  document.body.append(container); root=createRoot(container);
  await act(async()=>root.render(<EventOrigin asset={asset}/>));
}
afterEach(async()=>{await act(async()=>root.unmount());container.remove();vi.unstubAllGlobals();});

it('recupera o evento escolhido, data de descoberta, publicação e página de volta',async()=>{
  vi.stubGlobal('fetch',vi.fn(()=>{throw Error('Sem acesso externo em fixture');}));
  await render('origin',asset.id);
  expect(container.querySelector('h3')?.textContent).toBe('Evento escolhido');
  expect(container.textContent).toContain('Descoberto em');
  expect(container.querySelector('a')?.getAttribute('href')).toBe('https://example.org/edicao');
  const back=container.querySelector<HTMLAnchorElement>('a[href^="?"]')!;
  await act(async()=>back.click());
  expect(Object.fromEntries(new URLSearchParams(location.search))).toEqual({view:'events',return_event:'origin',event_search:'por título',event_page:'3'});
  expect(fetch).not.toHaveBeenCalled();
});
it('omite o contexto pertencente a outro ativo',async()=>{
  await render('origin','brapi:OUTR4');
  expect(container.textContent).toBe('');
});
it('recusa atribuir um evento avulso quando a API não confirma o vínculo',async()=>{
  vi.stubGlobal('fetch',vi.fn(async()=>new Response(JSON.stringify({id:'missing',title:'Incompatível',asset_ids:['brapi:OUTR4']}))));
  await render('missing',asset.id);
  expect(container.textContent).toContain('não está disponível para este ativo');
  expect(container.textContent).not.toContain('Incompatível');
});
