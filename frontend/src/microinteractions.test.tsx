import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { FavoriteButton } from './FavoriteButton';
import { AnalysisTrackingStatus } from './AnalysisTracking';
import type { AnalysisJob } from './types';

describe('Feedback depende de estado confirmado', () => {
  it('resultado incerto do favorito não é anunciado como falha definitiva', () => {
    const html = renderToStaticMarkup(<FavoriteButton saved={false} busy uncertain onChange={async () => false}/>);
    expect(html).toContain('A confirmar');
    expect(html).toContain('recuperação segura');
    expect(html).not.toContain('Falhou');
    expect(html).toContain('aria-pressed="false"');
  });
  it('mantém nome e estado acessíveis do favorito, sem confirmação inicial falsa', () => {
    const unsaved = renderToStaticMarkup(<FavoriteButton saved={false} busy={false} onChange={async () => true}/>);
    const saved = renderToStaticMarkup(<FavoriteButton saved busy={false} onChange={async () => true}/>);
    expect(unsaved).toContain('aria-label="Favorito"');
    expect(saved).toContain('aria-label="Favorito"');
    expect(unsaved).toContain('aria-pressed="false"');
    expect(saved).toContain('aria-pressed="true"');
    expect(unsaved).not.toContain('>Salvo<');
    expect(saved).toContain('>Salvo<');
  });
  it('indisponibilidade preserva controle no foco sem confirmar ação', () => {
    const html = renderToStaticMarkup(<FavoriteButton saved={false} busy onChange={async () => true}/>);
    expect(html).toContain('aria-disabled="true"');
    expect(html).not.toContain(' disabled=""');
    expect(html).toContain('aria-pressed="false"');
  });
  it('check de conclusão exige resultado persistido e consulta sem erro', () => {
    const data: AnalysisJob = {asset_id:'qa:ASSET',as_of:'2026-10-26T20:00:00Z',queue_position:null,queue_position_reason:null,result_analysis:null,
      job:{id:'qa-job',kind:'analyse',status:'succeeded',created_at:'2026-10-26T19:00:00Z'}};
    const render = (error='') => renderToStaticMarkup(<AnalysisTrackingStatus state={{snapshot:data,phase:'idle',error}} onRefresh={()=>{}} openJobs={()=>{}}/>);
    expect(render()).toContain('data-kind="attention"');
    expect(render()).not.toContain('data-kind="complete"');
    data.result_analysis={analysis_id:'qa-result',status:'insufficient_data',as_of:data.as_of};
    expect(render()).toContain('data-kind="complete"');
    expect(render('offline')).not.toContain('data-kind="complete"');
  });
});
