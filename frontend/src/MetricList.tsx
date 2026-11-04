import { date, label, number } from './format';
import type { Metric } from './types';

export function metricValue(value: Metric['value'], unit?: string): string {
  if (value == null || !Number.isFinite(value)) return 'Indisponível';
  if (unit === 'fraction' || unit === 'annualized_fraction') {
    const percentage = value.toLocaleString('pt-BR', {style:'percent', minimumFractionDigits:2, maximumFractionDigits:2});
    return percentage + (unit === 'annualized_fraction' ? ' anualizados' : '');
  }
  const units: Record<string,string> = {multiple:'vezes', dimensionless:'sem unidade'};
  return [number(value), unit ? (Object.hasOwn(units,unit) ? units[unit] : unit) : ''].filter(Boolean).join(' ');
}

export function MetricList({metrics}:{metrics:Metric[]}) {
  return <dl className="data-list">{metrics.map((metric,index)=>{
    const canonicalPeriod = 'start_at' in metric || 'end_at' in metric;
    const start = canonicalPeriod ? metric.start_at : metric.start_date;
    const end = canonicalPeriod ? metric.end_at : metric.end_date;
    const formula = metric.formula_version ?? metric.formula;
    return <div key={metric.metric_id || metric.name || index}>
      <dt>{metric.label || label(metric.name || metric.metric_id)}</dt>
      <dd>{metricValue(metric.value,metric.unit)}
        {metric.missing_reason && <small>{label(metric.missing_reason)}</small>}
        <small>{formula}
          {start || end ? ` · ${date(start)} — ${date(end)}` : ''}</small>
      </dd>
    </div>;
  })}</dl>;
}
