"""Conservative, read-only projections of mutable operational provider clocks.

This is not historical replay: provider_state retains only the latest row.
Invalid/future records remain stored and are explicitly withheld from display.
"""
import json
from datetime import date, datetime, time, timedelta

from .timeutil import SAO_PAULO, instant, iso

CLOCK_ERROR = 'provider_state_clock_invalid'


def known_clock(value, as_of, *, scheduled=False):
    if value is None:
        return None, False
    try:
        parsed = instant(value)
        if not scheduled and parsed > instant(as_of):
            return None, True
        return iso(parsed), False
    except (ValueError, TypeError, AttributeError, OverflowError):
        return None, True


def documentary_clock(value, as_of):
    """Keep civil-date precision; do not invent an intraday observation time."""
    if isinstance(value, str) and len(value) == 10:
        try:
            day = date.fromisoformat(value)
            # Until the day ends, its unknown observation time may be future.
            end = datetime.combine(day + timedelta(days=1), time.min, SAO_PAULO)
            return (value, False) if end <= instant(as_of) else (None, True)
        except (ValueError, TypeError, OverflowError):
            return None, True
    return known_clock(value, as_of)


def configuration_evaluation_at(evaluation, as_of):
    if not isinstance(evaluation, dict):
        return None
    evaluated, bad = known_clock(evaluation.get('evaluated_at'), as_of)
    if bad or evaluated is None:
        return None
    result = dict(evaluation, evaluated_at=evaluated, network_attempted=False)
    invalid = []
    for field in ('documented_probe_at', 'documented_check_at'):
        result[field], bad = documentary_clock(evaluation.get(field), evaluated)
        if bad:
            invalid.append(field)
    if invalid:
        result.update(reason=None, clock_error=CLOCK_ERROR, invalid_clock_fields=invalid)
    return result


def provider_state_at(row, as_of, source=None):
    if not row:
        return {}
    result = dict(row)
    invalid = []
    for key in ('last_attempt_at', 'last_success_at', 'next_attempt_at'):
        result[key], bad = known_clock(row.get(key), as_of, scheduled=key=='next_attempt_at')
        if bad:
            invalid.append(key)
    if 'last_attempt_at' in invalid:
        # The current payload has no trustworthy availability clock. Do not
        # expose a future error/details/retry as an observed past event.
        result.update(status='unavailable', error_code=CLOCK_ERROR,
                      details_json='{}', next_attempt_at=None)
    elif 'last_success_at' in invalid and not row.get('error_code'):
        result.update(status='unavailable', error_code=CLOCK_ERROR)
    if invalid:
        result['clock_error'] = CLOCK_ERROR
        result['invalid_clock_fields'] = invalid
    details_invalid = False
    try:
        details = json.loads(result.get('details_json', '{}'))
    except (ValueError, TypeError):
        details = {}
        details_invalid = True
    if not isinstance(details, dict):
        details = {}
        details_invalid = True
    if details_invalid:
        details['details_error'] = 'provider_state_details_invalid'
        if not result.get('error_code'):
            result.update(status='unavailable', error_code='provider_state_details_invalid')
    error = row.get('error_code')
    documentary = (error in {'rss_endpoint_not_verified', 'rss_reuse_terms_review_required'}
                   or isinstance(error, str) and error.startswith('discovery_'))
    evaluation = details.get('configuration_evaluation')
    if documentary:
        # Legacy collectors refreshed this field without making any request.
        # Correct the read projection only; the stored record remains untouched.
        result['last_attempt_at'] = None
        result['next_attempt_at'] = None
        if 'last_attempt_at' not in invalid:
            result['error_code'] = 'rss_endpoint_not_verified'
        if evaluation is None:
            source = source or {}
            evaluation = {
                'evaluated_at': row.get('last_attempt_at'), 'reason': error,
                'documented_probe_at': source.get('last_operational_probe_at'),
                'documented_check_at': source.get('feed_checked_at'),
                'legacy_local_evaluation': True,
            }
    projected = configuration_evaluation_at(evaluation, as_of)
    details.pop('configuration_evaluation', None)
    if projected is not None:
        details['configuration_evaluation'] = projected
        result['configuration_evaluation'] = projected
        if not result.get('error_code'):
            result.update(status='unavailable', error_code='rss_endpoint_not_verified')
    result['details_json'] = json.dumps(details, ensure_ascii=False, sort_keys=True)
    return result


def latest_clock(values, as_of):
    known = [known_clock(value, as_of)[0] for value in values]
    return max((value for value in known if value is not None), key=instant, default=None)
