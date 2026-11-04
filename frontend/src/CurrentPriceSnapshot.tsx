import { date } from './format';
import { MetricList } from './MetricList';
import type { Metric, PriceSnapshot } from './types';

export function CurrentPriceSnapshot({snapshot,metrics}:{snapshot?:PriceSnapshot|null;metrics:Metric[]}) {
  if (!snapshot) return null;
  return <section className="detail-section" aria-label="Captura diária de preços">
    <h3>Captura diária preservada</h3>
    <p className="meta">Captura sujeita a revisão pelo provedor. Somente períodos encerrados elegíveis entram nos cálculos. A data da sessão não é o horário do último negócio.</p>
    <p>O gráfico mostra o close informado. As métricas abaixo usam adjustedClose, que incorpora proventos e desdobramentos. Não representam retorno total do investidor ou preço ajustado somente por splits.</p>
    <p className="meta">Coleta do representante: {date(snapshot.fetched_at,true)} · Última aquisição: {date(snapshot.latest_capture_at,true)} · Disponível: {date(snapshot.available_at,true)}</p>
    <p className="meta">Estatísticas determinísticas da captura atual; separadas do snapshot da análise IA preservada.</p>
    <MetricList metrics={metrics}/>
    <p className="meta">Alinhamentos são uma heurística técnica experimental, sem calibração preditiva. Esta seção não produz sinal de compra ou venda. R63 e volume ausentes não anulam outras janelas válidas.</p>
    <details style={{minWidth:0,overflowWrap:'anywhere'}}><summary>Proveniência da captura</summary><p>ID: {snapshot.vintage_id}</p><p>SHA256 público: {snapshot.response_sha256}</p><p>Política: {snapshot.policy_sha256}</p></details>
  </section>;
}
