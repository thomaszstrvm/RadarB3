"""Conservative lexical quantities, not a proof of financial meaning.

Only unambiguous decimal/grouping spellings are normalized. Units and scales
must remain identical. Dates cannot donate their individual digits to a price.
"""
import json
import re
import unicodedata
from decimal import Decimal, InvalidOperation

from .fundamentals import is_reviewed_filing, validate_manifest
from .vintage_evidence import is_vintage


TOKEN = re.compile(
    r"(?<!\w)(?P<date>\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})(?!\w)"
    r"|(?<!\w)(?P<currency>US\$|R\$|BRL|USD|EUR|\$|€)?\s*"
    r"(?P<number>[+\-−]?\d[\d.,]*(?:[eE][+\-]?\d+)?)"
    r"(?:\s*(?P<scale>milhões|milhão|bilhões|bilhão|mil|mi|bi))?"
    r"(?:\s*(?P<currency_suffix>BRL|reais))?"
    r"(?:\s*(?P<unit>pontos?\s+percentuais|p\.p\.|pp|%|annualized_fraction|fraction|fração|frações|multiple|vezes|dimensionless))?(?!\w)",
    re.IGNORECASE,
)
CURRENCIES = {"r$": "BRL", "brl": "BRL", "us$": "USD", "usd": "USD", "eur": "EUR", "€": "EUR", "$": "$"}
SCALES = {"milhão": "million", "milhões": "million", "mi": "million", "bilhão": "billion", "bilhões": "billion", "bi": "billion", "mil": "thousand"}
UNITS = {"%": "%", "fraction": "fraction", "fração": "fraction", "frações": "fraction",
         "annualized_fraction": "annualized_fraction", "multiple": "multiple", "vezes": "multiple", 'dimensionless':'dimensionless'}


def decimal_value(raw):
    raw = raw.replace("−", "-").rstrip(".")
    mantissa, *exponent = re.split("[eE]", raw)
    if "," in mantissa and "." in mantissa:
        if re.fullmatch(r"[+\-]?\d{1,3}(?:\.\d{3})+,\d+", mantissa):
            mantissa = mantissa.replace(".", "").replace(",", ".")
        elif re.fullmatch(r"[+\-]?\d{1,3}(?:,\d{3})+\.\d+", mantissa):
            mantissa = mantissa.replace(",", "")
        else:
            raise InvalidOperation
    elif max(mantissa.count(","), mantissa.count(".")) > 1:
        separator = "," if "," in mantissa else "."
        if not re.fullmatch(r"[+\-]?\d{1,3}(?:" + re.escape(separator) + r"\d{3})+", mantissa):
            raise InvalidOperation
        mantissa = mantissa.replace(separator, "")
    else:
        mantissa = mantissa.replace(",", ".")
    value = Decimal(mantissa + ("e" + exponent[0] if exponent else ""))
    if not value.is_finite():
        raise InvalidOperation
    return value


def quantities(text):
    result = []
    covered_digits = set()
    for match in TOKEN.finditer(text):
        covered_digits.update(index for index in range(match.start(), match.end()) if text[index].isdigit())
        if match.group("date"):
            result.append(("date", match.group("date")))
            continue
        before, after = text[:match.start()].rstrip(), text[match.end():]
        # A second scale is not descriptive prose: consuming only the first
        # would make "milhões de milhões" appear to be a supported million.
        if match.group("scale") and re.match(r"^\s+(?:de\s+)?(?:milhões|milhão|bilhões|bilhão|mil|mi|bi)\b", after, re.I):
            result.append(("unsupported",))
            continue
        # Unknown suffixes, denominators and sign-before-currency spellings are
        # rejected whole. Letting regex backtracking keep a smaller number would
        # turn an unsupported financial literal into a different supported one.
        number_prefix = text[:match.start("number")]
        # A suffix must not relabel an explicit foreign currency skipped by the
        # token regex. A three-letter prefix is ambiguous for the new suffix,
        # regardless of case; this guard does not broaden currency support or
        # change the old prefix-only grammar. Ordinary "de" remains intact.
        unknown_currency_prefix = bool(before) and (
            unicodedata.category(before[-1]) == "Sc"
            or (match.group("currency_suffix") and bool(re.search(r"(?<!\w)[a-z]{3}$", before, re.I)))
        )
        if unknown_currency_prefix or before.endswith(("+", "-", "−")) or number_prefix.endswith((".", ",")) or (match.group("currency") and before.endswith("(")) or re.match(
                r"^\s*(?:/|por\b|BRL\b|USD\b|EUR\b|real\b|reais\b|d[oó]lares\b|euros\b|bps\b|million\b|billion\b|trillion\b|thousand\b)", after, re.I):
            result.append(("unsupported",))
            continue
        try:
            value = decimal_value(match.group("number"))
        except InvalidOperation:
            result.append(("unsupported",))
            continue
        currency = CURRENCIES.get((match.group("currency") or "").casefold(), "")
        if match.group("currency_suffix"):
            # The new suffix grammar is deliberately terminal. Continuing text
            # could add a rate denominator or another currency we do not parse.
            if (currency not in {"", "BRL"} or before.endswith("(")
                    or match.group("unit") or not re.fullmatch(r"[\s.!?]*", after)):
                result.append(("unsupported",))
                continue
            currency = "BRL"
        scale = SCALES.get((match.group("scale") or "").casefold(), "")
        unit = (match.group("unit") or "").casefold()
        unit = UNITS.get(unit, "percentage_points" if unit else "")
        result.append(("quantity", value, currency, scale, unit))
    # A numeric-looking literal must never disappear and make all([]) succeed.
    # Exchange names and ordinary B3 ticker identifiers are labels, not values.
    identifiers = set()
    for match in re.finditer(r"\b(?:[A-Z]{4}\d{1,2}|B3)\b", text):
        identifiers.update(range(match.start(), match.end()))
    if any(char.isdigit() and index not in covered_digits and index not in identifiers for index, char in enumerate(text)):
        result.append(("unsupported",))
    return result


FUNDAMENTAL_ACCOUNTS={
    'net_sales_revenue':r'\breceita\b',
    'net_income_attributable_to_vale_shareholders':r'\blucro\b',
    'net_cash_from_operating_activities':r'caixa operacional|caixa (?:das|de) atividades operacionais',
    'cash_and_cash_equivalents':r'saldo de caixa|caixa e equivalentes',
}


def unsupported_fundamental_comparison(text,items):
    # The pinned filing has no like-for-like prior period. A small fail-closed
    # grammar is deliberate; lexical support must not pretend quarter/YTD is growth.
    return (any(is_reviewed_filing(item) for item in items) and any(token[0]=='quantity' for token in quantities(text))
            and bool(re.search(r'\b(?:cresce\w*|cresc\w*|aument\w*|reduz\w*|caiu|queda|alta|maior|menor|superior|inferior|compar\w*|versus|vs|evolu\w*|melhor\w*|pior\w*)\b',text,re.I)))


def evidence_quantities(item, text='', *, modern=False):
    if is_vintage(item):
        # Only the revalidated financial projection is eligible. A sentence must
        # identify one field, so adjustedClose cannot donate a close or vice versa.
        fields={'adjustedClose':r'\badjustedClose\b|fechamento ajustado',
                'close':r'\bclose informado\b|fechamento informado',
                'open':r'\babertura\b','high':r'\bmáxima\b','low':r'\bmínima\b','volume':r'\bvolume\b'}
        selected=[key for key,pattern in fields.items() if re.search(pattern,text,re.I)]
        if len(selected)!=1:return set()
        data=json.loads(item['content'],parse_float=Decimal)
        currency=json.loads(data['response_body'])['results'][0]['currency']
        field=selected[0]
        return {('quantity',Decimal(row[field]),'' if field=='volume' else currency,'','')
                for row in data['financial_series'] if type(row.get(field)) in (int,Decimal)}
    if is_reviewed_filing(item):
        try:
            document = validate_manifest(json.loads(item["content"]))
        except (ValueError, KeyError, TypeError):
            return set()
        # Read only reviewed financial coefficients. Dates, IDs, page numbers,
        # scale metadata and prose never donate quantities. Do not multiply by
        # a million: the token retains that scale separately and exactly once.
        observations=document['observations']
        if modern:
            accounts=[account for account,pattern in FUNDAMENTAL_ACCOUNTS.items() if re.search(pattern,text,re.I)]
            periods=[kind for kind,pattern in (('quarter',r'trimestre|trimestral'),('year_to_date',r'acumulad[oa]|year_to_date|\bYTD\b'),('instant',r'saldo de caixa|caixa e equivalentes|posição')) if re.search(pattern,text,re.I)]
            if len(accounts)!=1 or len(periods)!=1:return set()
            observations=[obs for obs in observations if obs['account']==accounts[0] and obs['period']['kind']==periods[0]]
        return {("quantity", Decimal(observation["value"]), observation["currency"], "million", "")
                for observation in observations if observation["value"] is not None}
    if item.get("record_type") not in {"price", "computed_metric"}:
        return set(quantities(item["content"]))
    try:
        data = json.loads(item["content"], parse_float=Decimal)
    except (ValueError, TypeError):
        return set()
    fields = ("close", "open", "high", "low", "volume") if item["record_type"] == "price" else ("value",)
    result = set()
    for field in fields:
        value = data.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
            continue
        if item["record_type"] == "computed_metric":
            unit = data.get("unit")
            if unit in {"fraction", "annualized_fraction", "multiple",'dimensionless'}:
                result.add(("quantity", Decimal(value), "", "", unit))
            continue
        result.add(("quantity", Decimal(value), "", "", ""))
        if item["record_type"] == "price" and field != "volume" and data.get("currency") in {"BRL", "USD", "EUR"}:
            result.add(("quantity", Decimal(value), data["currency"], "", ""))
    return result


def _corpus(text,evidence_items,modern=False):
    modern=modern or any(is_vintage(item) or (item.get('record_type')=='computed_metric' and '"metric_id"' in item.get('content','')) for item in evidence_items)
    if modern:
        if any(is_reviewed_filing(item) for item in evidence_items) and any(re.search(pattern,text,re.I) for pattern in FUNDAMENTAL_ACCOUNTS.values()):
            evidence_items=[item for item in evidence_items if is_reviewed_filing(item)]
        families=[]
        for pattern,prefix in ((r'variaç[ãa]o|retorno|\bchange\b','provider_adjusted_change_'),
                               (r'volatilidade|desvio','provider_adjusted_daily_volatility_'),
                               (r'volatilidade|desvio','provider_adjusted_annualized_volatility_'),
                               (r'alinhamento|\balignment\b','technical_alignment_')):
            if re.search(pattern,text,re.I):families.append(prefix)
        if families:
            # A financial result must cite its calculated record, not a matching
            # coefficient somewhere in the series, headline or metadata.
            evidence_items=[item for item in evidence_items if item.get('record_type')=='computed_metric'
                            and any(json.loads(item['content']).get('metric_id','').startswith(prefix) for prefix in families)]
    return set().union(*(evidence_quantities(item,text,modern=modern) for item in evidence_items))


def numbers_supported(text, evidence_items,*,modern=False):
    corpus = _corpus(text,evidence_items,modern)
    return all(token != ("unsupported",) and token in corpus for token in quantities(text))


def numeric_rejection_reason(text, evidence_items,*,modern=False):
    """Called only after rejection; return a fixed category, never a quantity."""
    tokens = quantities(text)
    corpus = _corpus(text,evidence_items,modern)
    if ("unsupported",) in tokens:
        return "unsupported_format"
    for token in tokens:
        if token in corpus:
            continue
        if token[0] == "date":
            return "unsupported_date"
        if any(other[0] == "quantity" and other[1] == token[1] for other in corpus):
            return "unit_mismatch"
        return "quantity_not_found"
    return "quantity_not_found"
