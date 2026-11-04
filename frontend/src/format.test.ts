import { describe, expect, it } from 'vitest';
import { date, money, number, percent, priceDate, query, safeUrl } from './format';
describe('Dados financeiros e proveniência',()=>{
 it('preserva ausência em vez de fabricar zero',()=>{expect(money(null)).toBe('Indisponível');expect(percent(undefined)).toBe('Indisponível');expect(number(Number.NaN)).toBe('Indisponível');expect(percent(0)).toContain('0,00');});
 it('converte retorno fracionário em percentual com sinal',()=>{expect(percent(.024)).toBe('+2,40%');expect(percent(-.011)).toBe('-1,10%');});
 it('mantém data de sessão na zona de São Paulo',()=>{expect(date('2026-10-26')).toContain('26');expect(date('2026-10-26T01:00:00Z')).toContain('25');});
 it('não atribui horário a preço com precisão de data ou desconhecida',()=>{expect(priceDate({price_at:'2026-10-26T03:00:00Z',price_timestamp_precision:'date',price_session_date:'2026-10-26'})).not.toContain('00:00');expect(priceDate({price_at:'2026-10-26T03:00:00Z'})).not.toContain('Brasília');expect(priceDate({price_at:'2026-10-26T15:30:00Z',price_timestamp_precision:'instant'})).toContain('12:30');});
 it('recusa esquemas executáveis em URLs externas',()=>{expect(safeUrl('javascript:alert(1)')).toBeUndefined();expect(safeUrl('data:text/html,test')).toBeUndefined();expect(safeUrl('https://example.com/evidence')).toBe('https://example.com/evidence');});
 it('mantém valores de filtros escapados e não descarta zero',()=>{expect(query({search:'A&B',page:0,empty:'',watchlist:true})).toBe('search=A%26B&page=0&watchlist=true');});
});
