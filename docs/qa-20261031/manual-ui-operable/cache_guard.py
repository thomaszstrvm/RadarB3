"""Temporary QA restriction. Never substitutes data or produces an analysis."""
import json
from backend.cli_process import CLIError
from backend.cli_runner import MODEL, POLICY_VERSION, REASONING_EFFORT
from backend.core import digest
from backend.evidence import validate_bundle, validate_analysis, EvidenceError


def identity(runner, bundle):
    system = (runner.prompt_dir/'analysis-system.txt').read_text(encoding='utf-8-sig')
    system += '\n\n' + (runner.prompt_dir/'analysis-signal-v1.1.txt').read_text(encoding='utf-8-sig')
    prompt_hash = digest({'system':system,'request_template':(runner.prompt_dir/'analysis-request.txt').read_text(encoding='utf-8-sig'),
                          'wrapper':'json_envelope_v1','policy':POLICY_VERSION,'reasoning':REASONING_EFFORT})
    schema_hash = digest(json.loads((runner.schema_dir/'analysis-v1.1.schema.json').read_text(encoding='utf-8-sig')))
    cache_key = digest([bundle['bundle_id'],MODEL,prompt_hash,schema_hash])
    return {'bundle_id':bundle['bundle_id'],'bundle_hash':digest(bundle),'model':MODEL,
            'prompt_hash':prompt_hash,'schema_hash':schema_hash,'cache_key':cache_key,'analysis_id':'analysis_'+cache_key[:24]}


def cached_only(runner, bundle, reviewed, original_run, **kwargs):
    """Require an exact reviewed cache hit and forbid even quota reservation."""
    try:
        actual = identity(runner,bundle)
        if any(reviewed.get(key) != value for key,value in actual.items()):
            raise ValueError('identity')
        validate_bundle(bundle,runner.schema_dir)
        rows = runner.store.rows('SELECT * FROM analyses WHERE analysis_id=?',(actual['analysis_id'],))
        if len(rows) != 1 or any(rows[0][key] != actual[key] for key in ('bundle_id','model','prompt_hash','schema_hash')):
            raise ValueError('cache')
        analysis = json.loads(rows[0]['payload_json'])
        if digest(analysis) != reviewed['analysis_hash']:
            raise ValueError('review')
        validate_analysis(analysis,bundle,runner.schema_dir,actual['analysis_id'],expected_schema_version='1.1')
    except (ValueError,TypeError,KeyError,EvidenceError):
        raise CLIError('qa_cache_only_required') from None

    def denied_reservation(*args,**kwargs):
        raise CLIError('qa_cache_reservation_forbidden')
    def denied_process(*args,**kwargs):
        raise CLIError('qa_cache_process_forbidden')
    reserve, executor = runner.store.reserve_quota, runner.executor
    runner.store.reserve_quota, runner.executor = denied_reservation, denied_process
    try:
        result = original_run(bundle,**kwargs)
        if result.get('cache_reused') is not True or result.get('calls') != 0:
            raise CLIError('qa_cache_result_mismatch')
        return result
    finally:
        runner.store.reserve_quota, runner.executor = reserve, executor
