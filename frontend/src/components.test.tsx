import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { Articles, External, PriceChart } from './components';
describe('Evidências e séries sem fabricação',()=>{
 it('não desenha gráfico quando todos os preços são ausentes',()=>{const html=renderToStaticMarkup(<PriceChart prices={[{date:'2026-10-01',close:null}]} events={[]} onSelect={()=>{}}/>);expect(html).toContain('Histórico de preços indisponível');expect(html).not.toContain('<svg');});
 it('mostra somente pontos observados e evento acessível',()=>{const html=renderToStaticMarkup(<PriceChart prices={[{date:'2026-10-01',close:10},{date:'2026-10-02',close:null},{date:'2026-10-03',close:12}]} events={[{id:'event-1',title:'Evento verificado',event_date:'2026-10-02'}]} onSelect={()=>{}}/>);expect(html).toContain('2 preços observados');expect(html).not.toContain('<path');expect(html).toContain('role="button"');expect(html).toContain('Evento: Evento verificado');expect(html).toContain('tabela de preços (3)');});
 it('conecta revisão citada ao artigo e preserva acesso parcial',()=>{const html=renderToStaticMarkup(<Articles items={[{id:'article-1',revision_id:'revision-2',title:'Fonte',access_level:'snippet_only',excerpt:'Trecho observado',url:'https://example.com/article'}]}/>);expect(html).toContain('id="evidence-revision-2"');expect(html).toContain('Somente trecho');expect(html).toContain('Trecho observado');expect(html).toContain('noopener noreferrer');});
 it('renderiza links inseguros e HTML de fonte como texto',()=>{const html=renderToStaticMarkup(<External href="javascript:alert(1)">{'<script>executar()</script>'}</External>);expect(html).not.toContain('<a');expect(html).not.toContain('<script>');expect(html).toContain('&lt;script&gt;');});
});
