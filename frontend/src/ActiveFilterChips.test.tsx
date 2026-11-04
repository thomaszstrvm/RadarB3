import { expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { ActiveFilterChips, activeFilterEntries } from './ActiveFilterChips';

it('represents only active filters, preserving free text and readable categories', () => {
  expect(activeFilterEntries({search:'DEMO', sector:'Saúde', type:'stock', quality:'', page:'2', asset:'qa:DEMO3'})).toEqual([
    {key:'search', text:'Busca: DEMO'}, {key:'sector', text:'Setor: Saúde'}, {key:'type', text:'Tipo: Ação'},
  ]);
});

it('names each removal with its filter and escapes user-provided text', () => {
  const html = renderToStaticMarkup(<ActiveFilterChips params={{search:'<script>'}} onRemove={() => {}} onClear={() => {}}/>);
  expect(html).toContain('aria-label="Filtros ativos"');
  expect(html).toContain('aria-label="Remover filtro Busca: &lt;script&gt;"');
  expect(html).not.toContain('<script>');
});

it('does not leave empty controls when there are no filters', () => {
  expect(renderToStaticMarkup(<ActiveFilterChips params={{}} onRemove={() => {}} onClear={() => {}}/>)).toBe('');
});

it('describes linked events and the classification scope separately', () => {
  expect(activeFilterEntries({quality:'unknown',type:'unknown',state:'priced'})).toEqual([
    {key:'type',text:'Tipo: Não classificados'}, {key:'quality',text:'Vínculo: Sem eventos vinculados'},
    {key:'state',text:'Cobertura: Com preço'},
  ]);
});
