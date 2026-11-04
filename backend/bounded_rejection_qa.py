"""Opt-in, single-use public QA exception; never loaded by API or worker config."""
import hashlib
import json
import os
import re
from datetime import timedelta
from pathlib import Path

from .core import digest, dumps
from .numeric_support import quantities
from .timeutil import instant, iso
from .validation_diagnostics import diagnostic_for


QA_ROOT = Path(__file__).resolve().parents[1] / 'docs/qa-20261101/bounded-rejection-qa'
FIELDS = {'version', 'scope', 'retention_authorization_ref', 'execution_authorization_ref', 'asset_id', 'bundle_id', 'bundle_sha256',
          'cache_key', 'prior_run_id', 'prior_attempt', 'next_attempt', 'issued_at', 'expires_at',
          'prompt_hash', 'schema_hash', 'executable_sha256', 'code_pins'}
HASH_FIELDS = {'bundle_sha256', 'cache_key', 'prompt_hash', 'schema_hash', 'executable_sha256'}
ROOT = Path(__file__).resolve().parents[1]
VALIDATION_FILES = tuple('backend/' + name + '.py' for name in (
    'evidence', 'numeric_support', 'validation_diagnostics', 'fundamentals', 'vintage_evidence',
    'price_vintages', 'technical_policy', 'session_continuity', 'market_calendar', 'timeutil',
    'core', 'cli_runner', 'bounded_rejection_qa')) + (
        'schemas/evidence-bundle-v1.2.schema.json', 'schemas/fundamentals-v1.schema.json',
        'docs/qa-20261101/bounded-rejection-qa/.gitignore')
VALIDATION_FILES += ('docs/qa-20261101/bounded-rejection-qa/run_reviewed_retry.py',)
WORDS = set('a o as os de da das do dos em no na nos nas por para com sem e ou um uma à ao aos às é foi são foram está estão tem teve têm total valor valores receita receitas lucro líquido líquida atribuído atribuída atribuível aos acionistas vale trimestre trimestral acumulado acumulada período periodo posição saldo caixa equivalentes operacional operacionais atividades gerado gerada pelas pelo pela milhões milhão mil mi bilhões bilhão bi reais brl usd us informado ajustado fechamento abertura máxima mínima volume variação retorno desvio volatilidade alinhamento'.split())
MONEY = re.compile(r'(?<!\w)(?:US\$|USD|R\$|BRL) *[+\-]?[0-9]{1,6}(?:[.,][0-9]{1,4})? +(?:milhões|milhão|bilhões|bilhão|mil|mi|bi)\b', re.I)
ERROR_CODES = set('qa_failed qa_directory_unavailable qa_path_not_regular qa_file_too_large qa_file_unavailable qa_directory_changed qa_artifact_exists qa_write_failed qa_capture_size_blocked qa_capture_character_blocked qa_capture_vocabulary_blocked qa_capture_quantity_blocked qa_permission_path_invalid qa_permission_hash_mismatch qa_permission_invalid qa_permission_changed qa_permission_expired qa_binding_mismatch qa_prior_run_invalid qa_prior_consumption_invalid qa_next_reservation_exists qa_permission_already_used qa_capture_issue_blocked qa_capture_references_blocked qa_capture_failed qa_capture_receipt_failed qa_capture_binding_invalid qa_cleanup_hash_mismatch qa_cleanup_receipt_mismatch qa_cleanup_failed'.split())
ERROR_CODES.add('qa_code_pin_mismatch')
ERROR_CODES.add('qa_configuration_changed')


class QAError(ValueError):
    """All callers supply static codes; never construct from exception text."""
    def __init__(self, code):
        super().__init__(code if isinstance(code, str) and code in ERROR_CODES else 'qa_failed')


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate_key')
        result[key] = value
    return result


def checked_directory():
    base = QA_ROOT.absolute()
    try:
        if not base.is_dir():
            raise QAError('qa_directory_unavailable')
        for part in (base, *base.parents):
            info = part.lstat()
            if part.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise QAError('qa_path_not_regular')
        return base
    except OSError:
        raise QAError('qa_directory_unavailable') from None


def read_regular(path, limit=16384):
    try:
        info = path.lstat()
        if path.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400 or not path.is_file():
            raise QAError('qa_path_not_regular')
        with path.open('rb') as stream:
            body = stream.read(limit + 1)
        if len(body) > limit:
            raise QAError('qa_file_too_large')
        return body
    except OSError:
        raise QAError('qa_file_unavailable') from None


def create_record(base, name, value):
    if base != checked_directory():
        raise QAError('qa_directory_changed')
    body = dumps(value).encode('utf-8')
    try:
        with (base / name).open('xb') as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        raise QAError('qa_artifact_exists') from None
    except OSError:
        raise QAError('qa_write_failed') from None
    return hashlib.sha256(body).hexdigest()


def sanitized_sentence(text):
    """Return the entire unchanged string or refuse; no redaction/truncation."""
    if not isinstance(text, str) or not 1 <= len(text) <= 512 or len(text.encode('utf-8')) > 2048:
        raise QAError('qa_capture_size_blocked')
    alphabet = set(''.join(WORDS) + ''.join(WORDS).upper())
    if any(char not in alphabet and char not in '0123456789 .,;:()%+$-' for char in text):
        raise QAError('qa_capture_character_blocked')
    matches = list(MONEY.finditer(text))
    currency_starts = {match.start() for match in matches if match[0][:2].casefold() == 'r$'}
    words = re.finditer(r'[^\W\d_]+', text, re.UNICODE)
    if any(word[0].casefold() not in WORDS and not (
            word[0].casefold() == 'r' and word.start() in currency_starts) for word in words):
        raise QAError('qa_capture_vocabulary_blocked')
    tokens = quantities(text)
    if len(tokens) != 1 or tokens[0][0] != 'quantity':
        raise QAError('qa_capture_quantity_blocked')
    token = tokens[0]
    # The only unknown-number exception is a short, explicit monetary literal.
    # Long/grouped/free/scientific numbers are never inferred to be anonymous.
    if len(matches) != 1 or any(char.isdecimal() for char in text[:matches[0].start()] + text[matches[0].end():]):
        raise QAError('qa_capture_quantity_blocked')
    if sum(char in '0123456789' for char in matches[0][0]) > 7:
        raise QAError('qa_capture_quantity_blocked')
    if token[2] not in {'USD', 'BRL'} or token[3] not in {'million', 'billion', 'thousand'} or token[4]:
        raise QAError('qa_capture_quantity_blocked')
    return text, 'bounded_monetary_literal'


class ReviewedRetry:
    def __init__(self, document, sha256, base):
        self.document, self.sha256, self.base = document, sha256, base
        self.capture_outcome = {'status': 'not_attempted'}

    @classmethod
    def load(cls, path, expected_sha256):
        base = checked_directory()
        if Path(path).absolute() != base / 'authorization.json':
            raise QAError('qa_permission_path_invalid')
        body = read_regular(base / 'authorization.json')
        if not isinstance(expected_sha256, str) or not re.fullmatch('[a-f0-9]{64}', expected_sha256) or hashlib.sha256(body).hexdigest() != expected_sha256:
            raise QAError('qa_permission_hash_mismatch')
        try:
            document = json.loads(body, object_pairs_hook=unique_object)
            if not isinstance(document, dict) or set(document) != FIELDS:
                raise ValueError
            if type(document['version']) is not int or document['version'] != 1 or document['scope'] != 'public_rejection_qa_v1':
                raise ValueError
            if type(document['prior_attempt']) is not int or document['prior_attempt'] != 1 or type(document['next_attempt']) is not int or document['next_attempt'] != 2:
                raise ValueError
            for key in HASH_FIELDS:
                if not isinstance(document[key], str) or not re.fullmatch('[a-f0-9]{64}', document[key]):
                    raise ValueError
            for key in ('retention_authorization_ref', 'execution_authorization_ref', 'asset_id', 'bundle_id', 'prior_run_id'):
                if not isinstance(document[key], str) or not re.fullmatch(r'[A-Za-z0-9_:./-]{1,256}', document[key]):
                    raise ValueError
            if document['retention_authorization_ref'] == document['execution_authorization_ref']:
                raise ValueError
            pins = document['code_pins']
            if not isinstance(pins, dict) or set(pins) != set(VALIDATION_FILES) or any(
                    not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value) for value in pins.values()):
                raise ValueError
            issued, expires = instant(document['issued_at']), instant(document['expires_at'])
            if not timedelta(0) < expires - issued <= timedelta(hours=2):
                raise ValueError
        except (ValueError, TypeError, KeyError, OverflowError):
            raise QAError('qa_permission_invalid') from None
        return cls(document, expected_sha256, base)

    def claim(self, store, bundle, cache_key, prior, prompt_hash, schema_hash, executable_hash, now):
        current = self.load(self.base / 'authorization.json', self.sha256)
        if current.document != self.document:
            raise QAError('qa_permission_changed')
        p = self.document
        if not instant(p['issued_at']) <= instant(now) <= instant(p['expires_at']):
            raise QAError('qa_permission_expired')
        if any(hashlib.sha256(read_regular(ROOT / name, 500000)).hexdigest() != expected for name, expected in p['code_pins'].items()):
            raise QAError('qa_code_pin_mismatch')
        expected = {'asset_id': bundle['asset']['asset_id'], 'bundle_id': bundle['bundle_id'],
                    'bundle_sha256': digest(bundle), 'cache_key': cache_key, 'prompt_hash': prompt_hash,
                    'schema_hash': schema_hash, 'executable_sha256': executable_hash}
        if any(p[key] != value for key, value in expected.items()):
            raise QAError('qa_binding_mismatch')
        if len(prior) != 1:
            raise QAError('qa_prior_run_invalid')
        old = prior[0]
        if (old['run_id'] != p['prior_run_id'] or old['attempt'] != 1 or old['status'] != 'quarantined'
                or old['error_code'] != 'cli_analysis_validation_rejected' or not old['finished_at']
                or old['bundle_id'] != p['bundle_id'] or old['prompt_hash'] != prompt_hash
                or old['schema_hash'] != schema_hash or old['executable_hash'] != executable_hash):
            raise QAError('qa_prior_run_invalid')
        reservations = store.rows('SELECT * FROM quota_ledger WHERE reservation_id=?', (old['reservation_id'],))
        if (len(reservations) != 1 or reservations[0]['provider'] != 'codex_cli'
                or reservations[0]['status'] != 'consumed' or reservations[0]['units'] != 1):
            raise QAError('qa_prior_consumption_invalid')
        next_id = f'analysis:{cache_key}:2'
        if store.rows('SELECT 1 FROM quota_ledger WHERE reservation_id=?', (next_id,)):
            raise QAError('qa_next_reservation_exists')
        if any((self.base / name).exists() for name in ('authorization-used.json', 'capture-receipt.json', 'rejected-claim.json', 'cleanup-receipt.json')):
            raise QAError('qa_permission_already_used')
        # Exclusive claim is irreversible, including crashes before reservation.
        create_record(self.base, 'authorization-used.json', {
            'permission_sha256': self.sha256, 'cache_key': cache_key, 'prior_run_id': old['run_id'],
            'next_attempt': 2, 'reservation_id': next_id, 'claimed_at': now})
        return 2

    def capture(self, analysis, bundle, error, run_id, attempt, *, store):
        receipt = {'status': 'blocked', 'permission_sha256': self.sha256, 'attempt': 2}
        try:
            self.load(self.base / 'authorization.json', self.sha256)
            used = json.loads(read_regular(self.base / 'authorization-used.json'))
            rows = store.rows('SELECT * FROM analysis_runs WHERE run_id=?', (run_id,))
            if (used.get('permission_sha256') != self.sha256 or attempt != 2 or digest(bundle) != self.document['bundle_sha256']
                    or len(rows) != 1 or rows[0]['cache_key'] != self.document['cache_key'] or rows[0]['attempt'] != 2
                    or rows[0]['status'] != 'quarantined' or rows[0]['error_code'] != 'cli_analysis_validation_rejected'):
                raise QAError('qa_capture_binding_invalid')
            receipt['run_id'] = run_id
            diagnostic = diagnostic_for(error, bundle)
            issue = diagnostic['issues'][0]
            match = re.fullmatch(r'/claims/(\d{1,2})/statement', issue['path'])
            if issue['code'] != 'factual_number_not_in_cited_evidence' or not match:
                raise QAError('qa_capture_issue_blocked')
            claim = analysis['claims'][int(match[1])]
            ids = issue.get('evidence_ids', [])
            if not ids or len(ids) > 8 or set(ids) != set(claim['evidence_ids']):
                raise QAError('qa_capture_references_blocked')
            text, source = sanitized_sentence(claim['statement'])
            content = {'version': 1, 'permission_sha256': self.sha256, 'run_id': run_id,
                       'prior_run_id': self.document['prior_run_id'], 'attempt': attempt,
                       'asset_id': bundle['asset']['asset_id'], 'bundle_id': bundle['bundle_id'],
                       'bundle_sha256': digest(bundle), 'cache_key': self.document['cache_key'],
                       'prompt_hash': self.document['prompt_hash'], 'schema_hash': self.document['schema_hash'],
                       'issue': issue, 'statement': text, 'lexical_exact': True, 'quantity_source': source,
                       'origin': 'rejected_model_statement', 'value_validated': False,
                       'code_pins': self.document['code_pins']}
            sha = create_record(self.base, 'rejected-claim.json', content)
            receipt.update(status='captured', capture_sha256=sha, lexical_exact=True)
        except QAError as exc:
            receipt['reason'] = str(exc)
        except Exception:
            receipt['reason'] = 'qa_capture_failed'
        receipt['retention_pending'] = (self.base / 'rejected-claim.json').exists()
        self.capture_outcome = receipt
        try:
            create_record(self.base, 'capture-receipt.json', receipt)
        except Exception:
            self.capture_outcome = {'status': 'blocked', 'reason': 'qa_capture_receipt_failed',
                                    'retention_pending': (self.base / 'rejected-claim.json').exists()}
        return self.capture_outcome


def cleanup_capture(expected_sha256):
    """Explicit QA closure only: one fixed regular file, matching whole hash."""
    base = checked_directory()
    body = read_regular(base / 'rejected-claim.json')
    if hashlib.sha256(body).hexdigest() != expected_sha256:
        raise QAError('qa_cleanup_hash_mismatch')
    used = json.loads(read_regular(base / 'authorization-used.json'))
    permission_sha = used.get('permission_sha256')
    ReviewedRetry.load(base / 'authorization.json', permission_sha)
    # A failed/partial capture may have no receipt. The operator must still
    # supply the exact current file hash; the single-use permission stays used.
    if (base / 'capture-receipt.json').exists():
        receipt_body = read_regular(base / 'capture-receipt.json')
        try:
            receipt = json.loads(receipt_body)
        except (ValueError, UnicodeDecodeError):
            receipt = {}  # A partial receipt does not authorize a broader delete.
        if not isinstance(receipt, dict):
            receipt = {}
        if receipt.get('status') == 'captured' and receipt.get('capture_sha256') != expected_sha256:
            raise QAError('qa_cleanup_receipt_mismatch')
    try:
        (base / 'rejected-claim.json').unlink()
    except OSError:
        raise QAError('qa_cleanup_failed') from None
    create_record(base, 'cleanup-receipt.json', {'status': 'deleted', 'capture_sha256': expected_sha256,
                                               'permission_sha256': permission_sha, 'deleted_at': iso()})
