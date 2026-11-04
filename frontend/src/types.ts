export type List<T> = {items:T[];as_of?:string;total?:number;page?:number;page_size?:number};
export type ExecutionMode = 'collection-only'|'analysis-enabled'|'manual-analysis';
export type AI = {enabled:boolean;reason?:string|null;error_code?:string|null;homologated?:boolean;execution_mode?:ExecutionMode};
export type Operations = {
  job_control?:{allowed_kinds:string[];reason:string;expires_at:string|null};
  execution_mode:ExecutionMode;
  manual_analysis?:{state:'ready'|'busy'|'unavailable';pending_limit:1;repair_attempts:0;quota:{used:number;limit:number;remaining:number;period:string}};
  mode_mismatch?:boolean;
  worker:{status:'active'|'absent'|'stale';heartbeat_at:string|null;expires_at:string|null;execution_mode:string|null};
  ai:AI;
  collection:{sources:{reason?:string|null;details?:Record<string,unknown>;provider_id:string;last_attempt_at:string|null;last_success_at:string|null;error_code:string|null;next_attempt_at:string|null}[];latest_news_fetch:string|null;latest_price_fetch:string|null;latest_macro_fetch:string|null;latest_news_data_at?:string|null;latest_price_data_at?:string|null;latest_macro_data_at?:string|null};
  latest_analysis_snapshot:string|null;
  queried_at:string;
};
export type Overview = {as_of:string;window_start:string;mode:string;market_counts?:{priced:number;analysed:number};coverage?:{catalog_assets:number;eligible_assets:number;unclassified_assets:number;eligible_assets_with_price_vintages?:number|null;eligible_assets_with_legacy_prices?:number|null;eligible_assets_with_news?:number|null;eligible_assets_with_fundamentals?:number|null};counts:{assets:number|null;priced:number|null;articles:number|null;events:number|null;analysed:number|null;pending:number|null};ai:AI;last_run:Job|null};
export type Asset = {id:string;ticker:string;name:string;type?:string;sector?:string|null;price:number|null;price_at?:string|null;price_timestamp_precision?:'date'|'instant';price_session_date?:string|null;adjustment?:string|null;return_21:number|null;missing_reason?:string|null;event_count?:number|null;evidence_quality?:string|null;priority?:string|number|null;watchlisted:boolean;analysis_state?:'indisponível'|'insufficient_data'|'inconclusive'|'supported_hypothesis';stale?:boolean;price_source?:string|null;price_provider?:string|null;last_event?:string|null;last_event_disclosed_date?:string|null;last_event_seen_date?:string|null};
export type Price = {date:string;close:number|null;adjustedClose?:number|null;vintage_id?:string;adjustment?:string|null;source?:string|null;provider?:string|null};
export type Article = {id:string;revision_id?:string;title:string;published_at?:string|null;source?:string;source_id?:string;url?:string;canonical_url?:string;access_level?:string;summary?:string;excerpt?:string;collected_at?:string;fetched_at?:string;version?:number};
export type MarketEvent = {id?:string;event_id?:string;title:string;date?:string;event_date?:string|null;first_disclosed_at?:string|null;first_seen_at?:string;event_type?:string;category?:string;asset_id?:string;asset_ids?:string[];ticker?:string;summary?:string;url?:string;evidence_ids?:string[];articles?:Article[];novelty?:string;event_version?:number;links?:{entity_id:string;link_type:string;mechanism:string;evidence_ids:string[]}[];quality_flags?:string[]};
export type AnalysisSignal = {
  method?:{policy_id:'technical_alignment_v1';policy_sha256:string;experimental:true;horizon_semantics:'conditional_hypothesis_reassessment';eligible_action:'buy'|'sell'|'wait'|'insufficient_data'};
  action:'buy'|'sell'|'wait'|'insufficient_data';
  horizon_days:number;
  as_of:string;
  rationale:string;
  evidence_ids:string[];
  risks:string[];
  counterpoints:string[];
  invalidation_conditions:string[];
};
// Persisted v1.0 analyses have no signal. Never infer one from opportunity.
export type Analysis = {schema_version?:'1.0'|'1.1'|'1.2';signal?:AnalysisSignal;analysis_id:string;asset_id:string;as_of:string;status:string;synthesis:string;claims:{kind:string;statement:string;evidence_ids:string[]}[];scenarios:{case:string;horizon_days:number;description:string;conditions:string[];invalidation_conditions:string[];evidence_ids:string[]}[];opportunity:{classification:string;rationale:string;evidence_ids:string[]};risks:string[];missing_evidence:string[];data_quality_flags:string[];evidence_ids:string[]};
export type Metric = {label?:string;start_at?:string|null;end_at?:string|null;formula_version?:string;name?:string;metric_id?:string;value:number|null;unit?:string;missing_reason?:string;formula?:string;start_date?:string;end_date?:string;quality_flags?:string[]};
export type SnapshotEvidence = {evidence_id:string;record_type:'article'|'filing'|'price'|'macro'|'computed_metric'|'exposure';source_id:string;url:string|null;title:string;content:string;content_sha256:string;access_level:'headline_only'|'excerpt'|'full_text'|'structured';published_at:string|null;modified_at:string|null;first_seen_at:string;fetched_at:string;market_timestamp:string|null;timestamp_precision:'instant'|'date'|'unknown';publisher_group:string|null;original_publisher:string|null;entity_ids:string[];quality_flags:string[];available_at:string};
export type PriceWindowCoverage = {requested_start:string|null;requested_end:string|null;expected_last_session:string|null;observed_start:string|null;observed_end:string|null;expected_sessions:number|null;observed_sessions:number;missing_sessions:string[];status:'complete'|'partial'|'unknown';reason:string|null};
export type PriceSnapshot = {window_coverage?:PriceWindowCoverage|null;vintage_id:string;fetched_at:string;latest_capture_at:string;available_at:string;response_sha256:string;policy_sha256:string};
export type AssetDetail = Asset & {price_snapshot?:PriceSnapshot|null;current_price_metrics?:Metric[];prices:Price[];events:MarketEvent[];articles:Article[];analysis:Analysis|null;metrics:Metric[];evidence?:SnapshotEvidence[]};
export type Job = {id:string;kind:string;status:string;created_at:string;updated_at?:string;started_at?:string|null;finished_at?:string|null;error?:string|null;progress?:string|number|Record<string,unknown>|null;result?:unknown;not_before?:string|null;cancel_requested?:boolean|number};
export type AnalysisExecutionDiagnostic = {
  run_id:string;
  attempt:number;
  recorded_at:string;
  diagnostic:{
    version:1;
    stage:'cli';
    exit_code:number|null;
    category:'nonzero_exit'|'output_schema_rejected'|'authentication'|'rate_limit'|'model_unavailable';
    event_types:('error'|'turn.failed')[];
  };
};
export type NumericValidationReason = 'unsupported_format'|'unsupported_date'|'unit_mismatch'|'quantity_not_found'
  |'filing_account_unrecognized'|'filing_account_ambiguous'|'filing_period_unrecognized'
  |'filing_period_ambiguous'|'filing_period_incompatible';
export type AnalysisValidationIssue = {code:string;path:string;rule:string|null;numeric_reason?:NumericValidationReason};
export type AnalysisJob = {manual_scope?:'current'|'outside';execution_diagnostics?:AnalysisExecutionDiagnostic[];validation_diagnostics?:{run_id:string;attempt:number;recorded_at:string;diagnostic:{version:1;stage:string;issues:AnalysisValidationIssue[]}}[];asset_id:string;job:Job|null;queue_position:number|null;queue_position_reason:string|null;result_analysis:Pick<Analysis,'analysis_id'|'status'|'as_of'>|null;as_of:string};
export type Source = {next_attempt_at?:string|null;id:string;name:string;status?:string;last_attempt_at?:string|null;last_success_at?:string|null;item_count?:number|null;count?:number;details?:Record<string,unknown>;reason?:string|null;error?:string|null;access_level?:string;requested_start?:string;requested_end?:string;available_start?:string;available_end?:string;attempts?:unknown[];quota?:unknown;capability?:string;url?:string};
export type Alert = {id:string;title?:string;body:string;created_at?:string;asset_id?:string;read?:boolean;archived?:boolean;kind?:string};
export type AlertRule = {id:string;name:string;kind:string;enabled:boolean;asset_id?:string|null};
export type Settings = {news_interval_minutes:number;max_analyses_per_day:number;horizons_days:number[];theme:string;ai:AI;mode:string};
