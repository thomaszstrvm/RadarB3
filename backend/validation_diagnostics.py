"""Bounded metadata only: never serialize exception messages or rejected values."""
import re
FIELDS = set('schema_version analysis_id as_of asset_id status synthesis claims kind statement evidence_ids scenarios case horizon_days description conditions invalidation_conditions opportunity classification rationale risks missing_evidence data_quality_flags asset window evidence computed_metrics coverage bundle_id'.split())
CODES = set('analysis_id_mismatch analysis_identity_or_time_mismatch claim_evidence_missing unsupported_financial_certainty factual_number_not_in_cited_evidence invalid_scenario_horizon scenario_requires_conditions scenario_evidence_missing opportunity_evidence_missing opportunity_support_required root_evidence_not_exact_union abstention_classification_mismatch insufficient_support_for_hypothesis schema_invalid'.split())
RULES = set('type enum const required additionalProperties items minItems maxItems minimum maximum pattern format'.split())
FIELDS.update('signal action rationale risks counterpoints invalidation_conditions'.split())
CODES.update('signal_required signal_identity_or_time_mismatch signal_horizon_invalid signal_evidence_missing signal_classification_mismatch signal_support_insufficient signal_directional_data_unverified'.split())
CODES.add('explanation_empty')
CODES.add('signal_evidence_duplicate')
CODES.update({'signal_policy_mismatch','signal_experimental_disclosure_required'})
CODES.add('fundamental_comparison_unsupported')
NUMERIC_REASONS = {'unsupported_format', 'unsupported_date', 'unit_mismatch', 'quantity_not_found'}
NUMERIC_REASONS.update({'filing_account_unrecognized', 'filing_account_ambiguous',
                        'filing_period_unrecognized', 'filing_period_ambiguous',
                        'filing_period_incompatible'})


def safe_execution_diagnostic(value):
    value = value if isinstance(value, dict) else {}
    categories = {'nonzero_exit', 'output_schema_rejected', 'authentication', 'rate_limit', 'model_unavailable'}
    category = value.get('category')
    exit_code = value.get('exit_code')
    events = value.get('event_types', [])
    return {'version': 1, 'stage': 'cli',
            'exit_code': exit_code if type(exit_code) is int and -(2**31) <= exit_code < 2**32 else None,
            'category': category if isinstance(category, str) and category in categories else 'nonzero_exit',
            'event_types': sorted({item for item in events if isinstance(item, str) and item in {'error', 'turn.failed'}}) if isinstance(events, list) else []}

def safe_issue(code, path=(), rule=None, *, evidence_ids=(), known_evidence_ids=(), claim_kind=None, numeric_reason=None):
    code = code if isinstance(code, str) and code in CODES else 'validation_error_unclassified'
    parts = []
    for part in list(path)[:12]:
        if type(part) is int and 0 <= part <= 1000000:
            parts.append(str(part))
        elif isinstance(part, str) and part in FIELDS:
            parts.append(part)
        else:
            break
    result = {'code': code, 'path': '/' + '/'.join(parts) if parts else '/',
              'rule': rule if isinstance(rule, str) and rule in RULES else None}
    known = set(known_evidence_ids)
    resolved = sorted({item for item in evidence_ids if isinstance(item, str)
                       and item in known and re.fullmatch(r'[A-Za-z0-9_:.\-]{1,128}', item)})[:8]
    if resolved:
        result['evidence_ids'] = resolved
    if isinstance(claim_kind, str) and claim_kind in {'fact', 'inference'}:
        result['claim_kind'] = claim_kind
    if code == 'factual_number_not_in_cited_evidence' and isinstance(numeric_reason, str) and numeric_reason in NUMERIC_REASONS:
        result['numeric_reason'] = numeric_reason
    return result


def sanitize_issue(issue, known_evidence_ids=()):
    if not isinstance(issue, dict):
        return safe_issue(None)
    path = issue.get('path', '/')
    parts = str(path).strip('/').split('/')[:12]
    return safe_issue(issue.get('code'), [int(p) if p.isdecimal() and len(p) < 8 else p for p in parts],
                      issue.get('rule'), evidence_ids=issue.get('evidence_ids', []) if isinstance(issue.get('evidence_ids', []), list) else [],
                      known_evidence_ids=known_evidence_ids, claim_kind=issue.get('claim_kind'), numeric_reason=issue.get('numeric_reason'))

def diagnostic_for(error, bundle=None):
    issues = getattr(error, 'issues', None)
    if issues is None:
        fields = {'analysis_id_mismatch': ['analysis_id'], 'analysis_identity_or_time_mismatch': ['asset_id'],
                  'claim_evidence_missing': ['claims'], 'factual_number_not_in_cited_evidence': ['claims'],
                  'unsupported_financial_certainty': ['claims'], 'invalid_scenario_horizon': ['scenarios'],
                  'scenario_requires_conditions': ['scenarios'], 'scenario_evidence_missing': ['scenarios'],
                  'opportunity_evidence_missing': ['opportunity','evidence_ids'],
                  'opportunity_support_required': ['opportunity','evidence_ids'],
                  'root_evidence_not_exact_union': ['evidence_ids'], 'abstention_classification_mismatch': ['opportunity','classification'],
                  'insufficient_support_for_hypothesis': ['status']}
        issues = [safe_issue(str(error), fields.get(str(error), []))]
    known = [item['evidence_id'] for item in (bundle or {}).get('evidence', [])]
    return {'version': 1, 'stage': 'schema' if str(error).startswith('schema_invalid:') else 'semantic',
            'issues': [sanitize_issue(issue, known) for issue in issues[:8]]}
