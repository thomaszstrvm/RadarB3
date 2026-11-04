"""Inactive QA prototype: retain one whole bounded sentence or reject it.

No runner, grant, file write, network, database, or financial acceptance gate.
The caller alone selects the rejected path and controls authorization/retention.
"""
import hashlib
import json
import re
from decimal import Decimal


PUBLIC_BUNDLE_PIN = '4c3b8ccbb596170dd2b686ef8fa665c4ba4aef88ca2638a1578556209504c1d8'
GRAMMAR = 'counterpoint_capture_candidate_v1'
ERROR_CODES = frozenset({
    'candidate_size_blocked', 'candidate_character_blocked', 'candidate_vocabulary_blocked',
    'candidate_bundle_untrusted', 'candidate_number_uncovered', 'candidate_literal_limit',
    'candidate_label_limit', 'candidate_context_blocked', 'candidate_unknown_number_blocked',
    'candidate_unknown_limit', 'candidate_empty_numeric_scope',
})
WORDS = frozenset(('a o as os de da das do dos em no na nos nas por para com sem e ou um uma '
    'à ao aos às é foi são foram está estão tem teve têm valor valores receita receitas '
    'lucro líquido líquida atribuído atribuída atribuível acionistas trimestre trimestral '
    'acumulado acumulada período posição saldo caixa equivalentes operacional operacionais '
    'atividades informado ajustado fechamento abertura máxima mínima volume close adjustedclose '
    'variação retorno desvio volatilidade alinhamento diária diário anualizada anualizado '
    'janela janelas longa curta sessão sessões cinco vinte uma sessenta três quatro '
    'ausente ausentes ausência falta observações suficientes insuficiente insuficientes '
    'amostra cobertura limitada limitado hipótese experimental contraponto contexto '
    'indisponibilidade limitaria leitura atividade negociação dados de risco riscos '
    'pode podem não ainda apenas depende hipótese condicional comparação cresceu cotação').split())
LABEL = re.compile(r'(?<![\w])(?:R63|M5|M21|s20)(?![\w])')
NUMBER = r'[+\-]?[0-9]{1,16}(?:[.,][0-9]{1,24})?'
CURRENCY = r'(?:US\$|USD|R\$|BRL)'
SCALE = r'(?:milhões|milhão|bilhões|bilhão|mil|mi|bi)'
UNIT = r'(?:annualized_fraction|dimensionless|fraction|fração)'
LITERAL = re.compile(
    rf'(?<![\w.,])(?P<paren>\()?'
    rf'(?:(?P<currency>{CURRENCY}) *)?(?P<number>{NUMBER})'
    rf'(?: +(?P<scale>{SCALE}))?(?: +(?P<currency_suffix>BRL|reais))?'
    rf'(?: +(?P<unit>{UNIT}))?'
    rf'(?(paren)\))(?![\w]|[.,][0-9])', re.I)
LEGACY_SHORT = re.compile(r'[+\-]?[0-9]{1,6}(?:[.,][0-9]{1,4})?\Z')
ACCOUNT_IDS = frozenset({'net_sales_revenue', 'net_income_attributable_to_vale_shareholders',
                         'net_cash_from_operating_activities', 'cash_and_cash_equivalents'})
UNITS = frozenset({'fraction', 'annualized_fraction', 'dimensionless'})


class CandidateError(ValueError):
    """A fixed reason code only; never retain input or exception text."""
    def __init__(self, code):
        self.code = code if isinstance(code, str) and code in ERROR_CODES else 'candidate_context_blocked'
        super().__init__(self.code)


def _financial_lexemes(bundle):
    """Read enumerated typed financial fields from the one audited public bundle."""
    try:
        encoded = json.dumps(bundle, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':'), allow_nan=False).encode('utf-8')
        if hashlib.sha256(encoded).hexdigest() != PUBLIC_BUNDLE_PIN:
            raise ValueError
        # Extract from the same immutable serialization that was checked; a
        # caller mutating its dictionary after hashing must not donate values.
        pinned = json.loads(encoded, parse_float=Decimal)
        known, volumes = set(), set()

        def add(value, target, *, string_allowed=False):
            if value is None:
                return
            if type(value) not in (int, float, Decimal) and not (string_allowed and type(value) is str):
                raise ValueError
            spelling = str(value)
            if re.fullmatch(NUMBER, spelling) and sum(c.isdigit() for c in spelling) <= 32:
                target.add(spelling)

        for metric in pinned['computed_metrics']:
            if metric['unit'] in UNITS:
                add(metric['value'], known)
        for item in pinned['evidence']:
            if item['record_type'] == 'filing':
                document = json.loads(item['content'], parse_float=Decimal)
                for observation in document['observations']:
                    if observation['account'] in ACCOUNT_IDS:
                        add(observation['value'], known, string_allowed=True)
            elif item['record_type'] == 'price':
                document = json.loads(item['content'], parse_float=Decimal)
                for row in document['financial_series']:
                    for field in ('open', 'high', 'low', 'close', 'adjustedClose', 'volume'):
                        add(row[field], known)
                    add(row['volume'], volumes)
        return frozenset(known), frozenset(volumes)
    except Exception:
        raise CandidateError('candidate_bundle_untrusted') from None


def sanitize_candidate(text, bundle):
    """Return (identical_text, bounded_metadata), or a fixed CandidateError.

    Numbers matching public lexemes are eligible for retention only. Their
    account, period, currency, scale, unit and financial truth are NOT validated.
    """
    if type(text) is not str or not 1 <= len(text) <= 512:
        raise CandidateError('candidate_size_blocked')
    alphabet = set(''.join(WORDS) + ''.join(WORDS).upper())
    alphabet.update('RMsUSDABLfractionannualizeddimensionless')
    if any(char not in alphabet and char not in '0123456789 .,;:!?()+-$_' for char in text):
        raise CandidateError('candidate_character_blocked')
    if len(text.encode('utf-8')) > 2048:
        raise CandidateError('candidate_size_blocked')
    # A dotted word can be a domain even when each word is in the vocabulary.
    if (re.search(r'[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ0-9_-]*\.[A-Za-zÀ-ÿ]{2,}', text)
            or re.search(r'\b[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ0-9_-]* *\. *(?:com|net|org|io|dev|br)\b', text, re.I)):
        raise CandidateError('candidate_character_blocked')
    known, volumes = _financial_lexemes(bundle)
    labels = list(LABEL.finditer(text))
    if len(labels) > 4:
        raise CandidateError('candidate_label_limit')
    remaining = list(text)
    for match in labels:
        remaining[match.start():match.end()] = ' ' * len(match[0])
    without_labels = ''.join(remaining)
    literals = list(LITERAL.finditer(without_labels))
    if len(literals) > 3:
        raise CandidateError('candidate_literal_limit')
    unknown = 0
    for match in literals:
        spelling = match['number']
        if sum(char.isdigit() for char in spelling) > 32:
            raise CandidateError('candidate_unknown_number_blocked')
        is_known = spelling in known
        suffix = bool(match['currency_suffix'])
        money = (bool(match['currency']) != suffix) and not match['unit']
        if suffix and not re.fullmatch(r'[ .!?]*', without_labels[match.end():]):
            raise CandidateError('candidate_context_blocked')
        metric = bool(match['unit']) and not match['currency'] and not suffix and not match['scale']
        volume = (not match['currency'] and not suffix and not match['unit'] and not match['scale']
                  and spelling in volumes
                  and bool(re.search(r'\bvolume(?: de)? +$', without_labels[:match.start()], re.I)))
        if not (money or metric or volume):
            raise CandidateError('candidate_context_blocked')
        if not is_known:
            # Unknown coefficients are admitted ONLY by the previous monetary
            # shape. Short units alone do not establish an anonymous value.
            if (not money or not match['currency'] or suffix or not match['scale'] or match['paren']
                    or not LEGACY_SHORT.fullmatch(spelling)
                    or sum(char.isdigit() for char in spelling) > 7):
                raise CandidateError('candidate_unknown_number_blocked')
            unknown += 1
        remaining[match.start():match.end()] = ' ' * len(match[0])
    if unknown > 1:
        raise CandidateError('candidate_unknown_limit')
    rest = ''.join(remaining)
    if any(char.isdigit() for char in rest):
        raise CandidateError('candidate_number_uncovered')
    # Signs, currency/unit markers and parentheses must belong to a complete
    # recognized literal. No fragment, redaction or numeric remainder survives.
    if any(char in '()+-$_' for char in rest):
        raise CandidateError('candidate_context_blocked')
    if any(word.casefold() not in WORDS for word in re.findall(r'[^\W\d]+', rest)):
        raise CandidateError('candidate_vocabulary_blocked')
    if not literals and not labels:
        raise CandidateError('candidate_empty_numeric_scope')
    return text, {'grammar': GRAMMAR, 'origin': 'rejected_model_statement',
                  'value_validated': False, 'lexical_exact': True,
                  'literal_count': len(literals), 'unknown_literal_count': unknown,
                  'fixed_label_count': len(labels)}
