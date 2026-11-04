"""A fixed experimental technical heuristic; no forecast or fundamental vote."""
import math

from .core import digest
from .fundamentals import is_reviewed_filing, validate_filing
from .timeutil import publication_in_window

POLICY = {
    'policy_id':'technical_alignment_v1', 'experimental':True,
    'formula':'M_n=(log(A_end)-log(A_start))/(sample_std(last20_log_A_returns)*sqrt(n))',
    'windows':[5,21], 'kappa':1, 'comparison':'strict_unrounded',
    'horizon_semantics':'conditional_hypothesis_reassessment',
    'horizons_days':[30,90], 'fundamentals_news_role':'context_not_votes',
    'limitations':['no_predictive_calibration','not_probability','not_personalized',
                   'not_investor_total_return','no_growth_or_valuation_inference'],
}
POLICY_HASH=digest(POLICY)


def classify_alignment(m5,m21):
    """Strict comparisons; invalid inputs are unavailable, never neutral."""
    try: valid=all(type(value) in (int,float) and math.isfinite(value) for value in (m5,m21))
    except OverflowError: valid=False
    if not valid:
        return 'insufficient_data'
    if m5>1 and m21>1: return 'buy'
    if m5< -1 and m21< -1: return 'sell'
    return 'wait'


def evaluate_policy(metrics,evidence,asset_id,as_of):
    values={metric['metric_id']:metric for metric in metrics}
    technical=[values.get('technical_alignment_'+str(n)) for n in (5,21)]
    candidate=classify_alignment(*(item['value'] if item else None for item in technical))
    reasons=[item['missing_reason'] if item else 'alignment_metric_missing' for item in technical
             if item is None or item['value'] is None]
    filings=[];context=[];origins=set()
    for item in evidence:
        if is_reviewed_filing(item):
            document=validate_filing(item,asset_id,as_of)
            # Validation pins issuer, accounting scope, currency and typed periods.
            # These accounts establish context, never a directional vote.
            if any(obs['value'] is not None for obs in document['observations']):
                filings.append(item['evidence_id']);context.append(item['evidence_id'])
                origins.add(document['source']['document_url'])
        elif (item['record_type']=='article' and asset_id in item['entity_ids']
              and item['access_level'] in {'excerpt','full_text'} and item['content'].strip()
              and publication_in_window(item['published_at'],item['timestamp_precision'],as_of)):
            context.append(item['evidence_id'])
            origins.add(item['original_publisher'] or item['publisher_group'] or item['source_id'])
    eligible=candidate
    if not filings:
        reasons.append('typed_issuer_filing_missing')
        if candidate in {'buy','sell'}:eligible='insufficient_data'
    if candidate!='insufficient_data' and not context:
        eligible='insufficient_data';reasons.append('substantive_context_missing')
    return {'definition':POLICY,'policy_sha256':POLICY_HASH,'technical_candidate':candidate,
            'eligible_action':eligible,'missing_reasons':sorted(set(reasons)),
            'metric_evidence_ids':[item['evidence_id'] for item in technical if item and item['evidence_id']],
            'filing_evidence_ids':sorted(set(filings)), 'context_evidence_ids':sorted(set(context)),
            'context_origin_count':len(origins)}


def signal_method(policy):
    return {'policy_id':POLICY['policy_id'],'policy_sha256':POLICY_HASH,'experimental':True,
            'horizon_semantics':POLICY['horizon_semantics'],'eligible_action':policy['eligible_action']}
