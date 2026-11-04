"""Local query API. Queries never perform collection or invoke a model."""
from __future__ import annotations

import csv
import io
import json
import os
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from .core import Conflict, Store, digest, dumps, uid, wal_supported
from .timeutil import iso, instant, news_window, publication_in_window, SAO_PAULO
from .execution_mode import resolve_mode, ANALYSIS_KINDS
from .operations import manual_slot, manual_quota
from .operations import worker_state, collection_state, job_admission
from .session_continuity import calendar_status

ROOT = Path(__file__).resolve().parents[1]


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class JobRequest(Input):
    kind: Literal['refresh', 'backfill', 'probe', 'news', 'catalog', 'prices', 'macro'] = 'refresh'
    scope: dict = Field(default_factory=dict)


class AnalysisRequest(Input):
    reanalyze: StrictBool = False


class WatchRequest(Input):
    asset_id: str = Field(max_length=100)


class AlertPatch(Input):
    read: bool | None = None
    archived: bool | None = None


class Rule(Input):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal['new_event', 'analysis_change', 'source_failure'] = 'new_event'
    enabled: bool = True
    asset_id: str | None = None
    cooldown_minutes: int = Field(default=60, ge=1, le=10080)


class SettingsPatch(Input):
    news_interval_minutes: int | None = Field(default=None, ge=60, le=1440)
    max_analyses_per_day: int | None = Field(default=None, ge=0, le=10)
    horizons_days: list[Literal[30, 90]] | None = None
    theme: Literal['light', 'dark'] | None = None


def ai_status():
    from .cli_runner import status
    return status()


def create_app(database: str | Path | None = None, profile: str | None = None, execution_mode=None):
    mode = profile or os.environ.get('APP_MODE', 'real')
    process_mode = resolve_mode(execution_mode if execution_mode is not None else os.environ.get('RADAR_EXECUTION_MODE', 'collection-only' if database is None else 'analysis-enabled'))

    def inference_restricted():
        lease = worker_state(store)
        return process_mode == 'collection-only' or (lease['status'] == 'active' and lease['execution_mode'] == 'collection-only') or (process_mode != 'manual-analysis' and lease['status'] == 'active' and lease['execution_mode'] == 'manual-analysis')

    def ai_capabilities():
        result = ai_status()
        result = {**result, 'homologated': result.get('enabled') is True, 'execution_mode': process_mode}
        if inference_restricted():
            result.update(enabled=False, reason='Inferência bloqueada nesta API pelo modo de execução. Análises pendentes são preservadas; confira também o modo do worker.', error_code='analysis_disabled_by_execution_mode')
        if process_mode == 'manual-analysis':
            state, _ = manual_slot(store)
            if state != 'ready':
                result.update(enabled=False, reason='Uma análise manual está em andamento; aguarde sua conclusão.' if state == 'busy' else 'O executor manual não está disponível; nenhuma análise será enfileirada.',
                              error_code='manual_analysis_busy' if state == 'busy' else 'manual_worker_unavailable')
            else:
                quota, valid = manual_quota(store)
                if not valid or quota['remaining'] == 0:
                    result.update(enabled=False, reason='Limite local de análises atingido; os resultados salvos continuam disponíveis.' if valid else 'Configuração do limite local inválida; nenhuma análise será iniciada.',
                                  error_code='manual_quota_exhausted' if valid else 'manual_quota_configuration_invalid')
        if mode == 'demo':
            result = {**result, 'enabled': False, 'reason': 'Modo demonstração: chamadas externas de IA desativadas.', 'error_code': 'cli_demo_analysis_disabled'}
        return result

    if mode not in {'real', 'demo'}:
        raise RuntimeError('APP_MODE deve ser real ou demo')
    store = Store(database or ROOT / 'data' / mode / 'radar.sqlite3', profile=mode, wal=wal_supported())
    app = FastAPI(title='Radar B3', version='0.1.0', docs_url='/api/docs', redoc_url=None)
    app.state.store = store
    app.state.execution_mode = process_mode
    app.state.token = secrets.token_urlsafe(32)
    source_defs = json.loads((ROOT / 'config/news-sources.json').read_text(encoding='utf-8'))['sources']
    source_names = {s['id']: s['name'] for s in source_defs}

    @app.middleware('http')
    async def local_boundary(request: Request, call_next):
        host = request.headers.get('host', '')
        host_name = host.split(':', 1)[0]
        if host_name not in {'127.0.0.1', 'localhost'}:
            return JSONResponse({'detail': 'Host não permitido'}, status_code=400)
        origin = request.headers.get('origin')
        if origin and origin != f'http://{host}':
            return JSONResponse({'detail': 'Origem externa não permitida'}, status_code=403)
        if request.headers.get('sec-fetch-site') == 'cross-site' and request.url.path.startswith('/api/'):
            return JSONResponse({'detail': 'Solicitação externa não permitida'}, status_code=403)
        if request.method in {'POST', 'PATCH', 'PUT', 'DELETE'}:
            if not secrets.compare_digest(request.headers.get('x-radar-token', ''), app.state.token):
                return JSONResponse({'detail': 'Sessão local expirada; recarregue a página'}, status_code=403)
            try:
                length = int(request.headers.get('content-length', '0'))
            except ValueError:
                return JSONResponse({'detail': 'Tamanho inválido'}, status_code=400)
            if length > 100_000:
                return JSONResponse({'detail': 'Comando muito grande'}, status_code=413)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        return response

    @app.exception_handler(Conflict)
    async def conflict(_request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=409)

    def one(query, args=(), connection=None):
        rows = [dict(r) for r in connection.execute(query, args)] if connection is not None else store.rows(query, args)
        if not rows:
            raise HTTPException(404, 'Registro não encontrado')
        return rows[0]

    def settings_value(connection=None):
        def value(key, default):
            if connection is None:
                return store.setting(key, default)
            row = connection.execute('SELECT value_json FROM settings WHERE key=?', (key,)).fetchone()
            return json.loads(row[0]) if row else default
        result = {'news_interval_minutes': value('news_interval_minutes', 60),
                  'max_analyses_per_day': value('max_analyses_per_day', 10),
                  'horizons_days': value('horizons_days', [30, 90]),
                  'theme': value('theme', 'light'), 'mode': mode, 'as_of': iso()}
        # Mutation receipts persist only applied settings, never CLI diagnostics.
        if connection is None:
            result.update(ai=ai_capabilities(), storage='Banco local separado por perfil')
        return result

    def validate_mutation_key(key):
        if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,200}', key):
            raise HTTPException(422, 'Chave de idempotência inválida')
        return key

    def mutate(request, payload, operation, status_code=200):
        """Commit the effect and its receipt together; a replay never runs it again."""
        supplied_key = request.headers.get('idempotency-key')
        key = validate_mutation_key(supplied_key) if supplied_key is not None else uid('mutation')
        method, path = request.method, request.url.path
        fingerprint = digest({'method': method, 'path': path, 'payload': payload})
        with store.transaction() as con:
            saved = con.execute('SELECT * FROM mutation_receipts WHERE key=?', (key,)).fetchone()
            if saved:
                if saved['request_hash'] != fingerprint:
                    raise HTTPException(409, 'Chave de idempotência já usada em outro comando')
                return JSONResponse(json.loads(saved['response_json']), status_code=saved['status_code'])
            con.execute('SAVEPOINT mutation_effect')
            try:
                result = operation(con)
            except (HTTPException, Conflict) as exc:
                # Rejections are durable too, but must not retain partial effects.
                con.execute('ROLLBACK TO mutation_effect')
                status_code = exc.status_code if isinstance(exc, HTTPException) else 409
                if not 400 <= status_code < 500:
                    raise
                result = {'detail': exc.detail if isinstance(exc, HTTPException) else str(exc)}
            con.execute('RELEASE mutation_effect')
            con.execute('INSERT INTO mutation_receipts VALUES(?,?,?,?,?,?,?)',
                        (key, method, path, fingerprint, status_code, dumps(result), iso()))
        return JSONResponse(result, status_code=status_code)

    @app.exception_handler(sqlite3.OperationalError)
    async def database_unavailable(_request, _exc):
        return JSONResponse({'detail': 'Banco local temporariamente indisponível; consulte o resultado do pedido antes de repetir.'}, status_code=503)

    def articles_for(ids=None, article_id=None, current_only=True):
        sql='SELECT a.*,r.* FROM articles a JOIN article_revisions r ON r.revision_id=a.latest_revision_id'
        args=[]
        if ids is not None:
            if not ids: return []
            sql+=' WHERE r.revision_id IN ('+','.join('?' for _ in ids)+')'
            args=list(ids)
        elif article_id:
            sql+=' WHERE a.article_id=?'; args=[article_id]
        rows = store.rows(sql+' ORDER BY r.published_at DESC,a.article_id',args)
        now=iso()
        if current_only:
            rows = [r for r in rows if publication_in_window(r['published_at'] or r['first_seen_at'], r['timestamp_precision'] if r['published_at'] else 'instant', now)]
        return [{**r, 'id': r['article_id'], 'url': r['canonical_url'], 'source': source_names.get(r['source_id'], r['source_id']),
                 'excerpt': r['content'], 'quality_flags': json.loads(r['quality_flags_json'])} for r in rows]

    def event_rows():
        rows = store.rows('SELECT v.payload_json,v.created_at FROM events e JOIN event_versions v ON v.event_id=e.event_id AND v.version=e.latest_version ORDER BY v.created_at DESC,e.event_id')
        result = []
        for row in rows:
            p = json.loads(row['payload_json'])
            if not publication_in_window(p['first_disclosed_at'] or p['first_seen_at'], p['timestamp_precision'] if p['first_disclosed_at'] else 'instant', iso()):
                continue
            current_articles=articles_for(p['evidence_ids'])
            if not current_articles:
                continue
            result.append({**p, 'id': p['event_id'], 'date': p['first_disclosed_at'], 'category': p['event_type'],
                           'articles': current_articles, 'updated_at': row['created_at']})
        return result

    def asset_rows(events=None):
        from .price_vintages import price_projection
        now=iso()
        vintage_assets={row['asset_id'] for row in store.rows('SELECT DISTINCT asset_id FROM price_vintages WHERE available_at<=?',(now,))} if store.rows("SELECT 1 FROM sqlite_master WHERE name='price_vintages' AND type='table'") else set()
        event_counts = {}
        disclosed_dates = {}
        last_discoveries = {}
        if events is None:
            events = event_rows()
        for e in events:
            published = e.get('first_disclosed_at')
            day = None
            if published:
                day = published[:10] if e['timestamp_precision']=='date' else instant(published).astimezone(SAO_PAULO).date().isoformat()
            discovery = (instant(e['first_seen_at']), e['id'], e['title']) if e.get('first_seen_at') else None
            for asset_id in e['asset_ids']:
                event_counts[asset_id] = event_counts.get(asset_id,0)+1
                if day and day > disclosed_dates.get(asset_id,''):
                    disclosed_dates[asset_id] = day
                if discovery and (asset_id not in last_discoveries or discovery > last_discoveries[asset_id]):
                    last_discoveries[asset_id] = discovery
        analysis_states = {}
        # Read metadata once for the whole list, choosing the same latest
        # persisted result as the detail view (including deterministic ties).
        for analysis in store.rows('SELECT asset_id,status FROM analyses ORDER BY as_of DESC,created_at DESC,analysis_id DESC'):
            analysis_states.setdefault(analysis['asset_id'], analysis['status'])
        result = []
        # Filter, sort and paginate the full catalogue, including late tickers.
        for a in store.list_assets(limit=-1):
            if not a['eligible'] and a['asset_type'] != 'unknown':
                continue
            projection=price_projection(store,a,now) if a['asset_id'] in vintage_assets else None
            if projection is not None:
                latest=projection['bars'][-1] if projection['bars'] else {}
                a.update(price=latest.get('close'),market_timestamp=None,session_date=latest.get('session_date'),
                         adjustment='unknown',price_provider='brapi',price_quality_flags_json='["daily_bar_timestamp_not_trade_time"]',
                         price_fetched_at=projection['snapshot']['fetched_at'])
            count = event_counts.get(a['asset_id'], 0)
            latest_discovery = last_discoveries.get(a['asset_id'])
            stale = a['market_timestamp'] is None or datetime.now(timezone.utc) - instant(a['market_timestamp']) > timedelta(days=4)
            if projection and a['session_date']:
                stale=(instant(now).astimezone(SAO_PAULO).date()-__import__('datetime').date.fromisoformat(a['session_date'])).days>4
            price_flags=json.loads(a.get('price_quality_flags_json') or '[]')
            result.append({**a, 'id': a['asset_id'], 'type': a['asset_type'], 'price_at': a['market_timestamp'],
                           'price_timestamp_precision': 'date' if 'daily_bar_timestamp_not_trade_time' in price_flags else 'instant',
                           'price_session_date': a.get('session_date'),
                           'watchlisted': bool(a['watched']), 'return_21': None, 'volume_ratio': None,
                           'missing_reason': 'Close informado e adjustedClose são campos distintos; métricas ajustadas descritivas no detalhe.' if projection else 'Ajuste ou sessões comparáveis não confirmados; retorno não calculado.',
                           'event_count': count, 'last_event_disclosed_date': disclosed_dates.get(a['asset_id']),
                           'last_event_seen_date': latest_discovery[0].astimezone(SAO_PAULO).date().isoformat() if latest_discovery else None,
                           'last_event': latest_discovery[2] if latest_discovery else None,
                           'priority': None, 'evidence_quality': 'low' if count else 'unknown',
                           'coverage': 'antiga' if stale and a['price'] is not None else ('parcial' if a['price'] is not None else 'ausente'),
                           'analysis_state': analysis_states.get(a['asset_id'], 'indisponível'), 'stale': stale})
        return result

    def matches_market_state(asset, state):
        if state == 'priced': return asset['price'] is not None
        if state == 'unpriced': return asset['price'] is None
        if state == 'analysed': return asset['analysis_state'] != 'indisponível'
        return asset['analysis_state'] == state

    def job_view(job):
        if not job:
            raise HTTPException(404, 'Execução não encontrada')
        return {**job, 'id': job['job_id'], 'error': job['error_code'], 'finished_at': job['updated_at'] if job['status'] in {'succeeded','failed','partial','cancelled'} else None}

    def mutation_job_view(job):
        view = job_view(job)
        # Results/checkpoints may contain provider diagnostics; keep receipts small.
        fields = ('id', 'job_id', 'kind', 'status', 'created_at', 'updated_at', 'finished_at',
                  'progress', 'cancel_requested', 'error', 'error_code', 'not_before', 'attempts')
        return {field: view[field] for field in fields}

    def bounded(items, page, page_size):
        return {'items': items[(page-1)*page_size:page*page_size], 'total': len(items), 'page': page, 'page_size': page_size, 'as_of': iso()}

    @app.get('/api/v1/session')
    def session():
        return {'token': app.state.token}

    @app.get('/api/v1/mutations/{key}')
    def mutation_receipt(key: str):
        row = one('SELECT * FROM mutation_receipts WHERE key=?', (validate_mutation_key(key),))
        return {'key': row['key'], 'method': row['method'], 'path': row['path'],
                'state': 'completed', 'status_code': row['status_code'],
                'response': json.loads(row['response_json']), 'completed_at': row['completed_at']}

    @app.get('/api/v1/health/live')
    def live():
        return {'status': 'alive', 'as_of': iso()}

    @app.get('/api/v1/health/ready')
    def ready():
        return {'status': 'ready', 'database': store.coverage(), 'ai': ai_capabilities(), 'as_of': iso()}

    @app.get('/api/v1/operations')
    def operations():
        now = iso()
        lease = worker_state(store, now)
        capabilities = ai_capabilities()
        manual = {}
        if process_mode == 'manual-analysis':
            state, _ = manual_slot(store, now=now)
            if state == 'ready' and not capabilities.get('enabled'):
                state = 'unavailable'
            quota, _ = manual_quota(store, now=now)
            manual = {'manual_analysis': {'state': state, 'pending_limit': 1, 'repair_attempts': 0, 'quota': quota}}
        return {**manual, 'execution_mode': process_mode, 'worker': lease,
                'job_control': job_admission(store, process_mode, now=now),
                'mode_mismatch': lease['status'] == 'active' and lease['execution_mode'] != process_mode,
                'ai': capabilities, 'collection': collection_state(store, [s['id'] for s in source_defs]),
                'latest_analysis_snapshot': store.rows('SELECT MAX(as_of) AS latest FROM analyses')[0]['latest'],
                'queried_at': now}

    @app.get('/api/v1/overview')
    def overview():
        c = store.coverage()
        events = event_rows()
        market = asset_rows(events)
        jobs = store.list_jobs(500)
        last = None
        for candidate in jobs:
            if candidate['kind'] != 'refresh' or candidate['status'] not in {'succeeded','partial'}:
                continue
            children=[store.get_job(i) for i in (candidate['result'] or {}).get('children',[])]
            if children and all(j and j['status'] in {'succeeded','partial','blocked','failed','cancelled'} for j in children):
                last={**candidate,'status':'succeeded' if all(j['status']=='succeeded' for j in children) else 'partial',
                      'updated_at':max(j['updated_at'] for j in children)}
                break
        now = iso()
        return {'as_of': now, 'window_start': news_window(now)[0], 'mode': mode,
                'counts': {'assets': c['eligible_assets'], 'priced': c['assets_with_prices'], 'articles': len(articles_for()), 'events': len(events), 'analysed': c['assets_analyzed'],
                           'pending': sum(j['kind'] in {'analyse','analysis'} and j['status'] in {'queued','running','retry_wait','paused','blocked'} for j in jobs)},
                'market_counts': {state:sum(matches_market_state(asset,state) for asset in market) for state in ('priced','analysed')},
                'coverage': c, 'ai': ai_capabilities(), 'last_run': job_view(last) if last else None,
                'notice': 'Primeira coleta: ainda não existe comparação com uma varredura anterior.' if not last else 'Cobertura parcial; veja as fontes e suas datas.'}

    @app.get('/api/v1/assets')
    def assets(search: str = '', watchlist: bool = False, sector: str = '', type: str = '', quality: str = '', state: str = '', sort: str = 'ticker', page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200)):
        rows = [a for a in asset_rows() if search.casefold() in (a['ticker']+' '+a['name']).casefold()]
        if watchlist: rows = [a for a in rows if a['watchlisted']]
        if sector: rows = [a for a in rows if a['sector'] == sector]
        if type == 'eligible': rows = [a for a in rows if a['eligible']]
        elif type and type != 'all': rows = [a for a in rows if a['type'] == type]
        if quality: rows = [a for a in rows if a['evidence_quality'] == quality]
        if state: rows = [a for a in rows if matches_market_state(a,state)]
        key = sort.lstrip('-').split(':')[0]
        if key not in {'ticker','name','price','event_count','priority','return_21','updated_at'}:
            raise HTTPException(422, 'Ordenação não suportada')
        # Stable secondary order keeps equal values and missing values in ticker order.
        rows.sort(key=lambda a:(a['ticker'],a['id']))
        present = [a for a in rows if a.get(key) is not None]
        absent = [a for a in rows if a.get(key) is None]
        rows = sorted(present, key=lambda a:a[key], reverse=sort.startswith('-') or sort.endswith(':desc')) + absent
        return bounded(rows, page, page_size)

    @app.get('/api/v1/assets/{asset_id}/prices')
    def prices(asset_id: str):
        from .price_vintages import price_projection
        asset=one('SELECT * FROM assets WHERE asset_id=?', (asset_id,))
        now=iso();projection=price_projection(store,asset,now)
        if projection is not None:
            return {'items':projection['bars'],'as_of':now,'missing_reason':None if projection['bars'] else 'Sem observações na janela',
                    'price_snapshot':projection['snapshot'],'current_price_metrics':projection['metrics']}
        rows = store.rows('SELECT b.session_date AS date,b.provider,b.adjustment,r.* FROM price_bars b JOIN price_revisions r ON r.revision_id=b.latest_revision_id WHERE b.asset_id=? ORDER BY b.session_date', (asset_id,))
        start,end=news_window(iso())
        start_date=instant(start).astimezone(SAO_PAULO).date().isoformat()
        end_date=instant(end).astimezone(SAO_PAULO).date().isoformat()
        rows=[r for r in rows if start_date<=r['date']<=end_date]
        return {'items': rows, 'as_of': iso(), 'missing_reason': None if rows else 'Histórico não disponível'}

    @app.get('/api/v1/assets/{asset_id}/analysis-job')
    def asset_analysis_job(asset_id: str):
        now = iso()
        manual_scope = None
        # Keep selection, queue position and result ownership in one read snapshot.
        # This route never starts the worker, probes the CLI or enqueues work.
        with store.connect() as con:
            con.execute('BEGIN')
            if not con.execute('SELECT 1 FROM assets WHERE asset_id=?', (asset_id,)).fetchone():
                raise HTTPException(404, 'Ativo não encontrado')
            ordering = "CASE WHEN status IN ('queued','running','retry_wait') THEN 0 ELSE 1 END"
            args = (asset_id,)
            if process_mode == 'manual-analysis':
                _, manual_owner = manual_slot(store, con, now)
                ordering = """CASE WHEN status IN ('queued','running','retry_wait')
                    AND json_extract(payload_json,'$.manual_owner')=? THEN 0
                    WHEN status IN ('queued','running','retry_wait') THEN 2 ELSE 1 END"""
                args += (manual_owner,)
            row = con.execute("""SELECT * FROM jobs
                WHERE kind IN ('analyse','analysis') AND json_extract(payload_json,'$.asset_id')=?
                ORDER BY """ + ordering + ", created_at DESC,job_id DESC LIMIT 1", args).fetchone()
            selected = store.decode_job(dict(row)) if row else None
            position, reason, result_analysis = None, 'no_job', None
            if selected:
                reason = 'not_waiting'
                if process_mode == 'manual-analysis':
                    _, manual_owner = manual_slot(store, con, now)
                    manual_scope = 'current' if manual_owner and selected['payload'].get('manual_owner') == manual_owner else 'outside'
                if selected['status'] in {'queued', 'retry_wait'}:
                    if manual_scope == 'outside':
                        reason = 'manual_scope_inactive'
                    elif selected['cancel_requested']:
                        reason = 'cancellation_requested'
                    elif selected['not_before'] > now:
                        reason = 'not_ready'
                    elif manual_scope == 'current':
                        position, reason = 1, None
                    else:
                        # Same ready set and ordering as Store.claim, across all job kinds.
                        ahead = con.execute("""SELECT COUNT(*) FROM jobs
                            WHERE status IN ('queued','retry_wait') AND not_before<=? AND cancel_requested=0
                            AND (priority,created_at,job_id)<(?,?,?)""",
                            (now, selected['priority'], selected['created_at'], selected['job_id'])).fetchone()[0]
                        position, reason = ahead + 1, None
                result_id = (selected['result'] or {}).get('analysis_id')
                if result_id:
                    result_row = con.execute('SELECT analysis_id,status,as_of FROM analyses WHERE analysis_id=? AND asset_id=?', (result_id, asset_id)).fetchone()
                    result_analysis = dict(result_row) if result_row else None
            diagnostics = []
            execution_diagnostics = []
            if selected and con.execute("SELECT 1 FROM sqlite_master WHERE name='analysis_runs'").fetchone():
                from .validation_diagnostics import sanitize_issue, safe_execution_diagnostic
                records = con.execute("""SELECT a.created_at,a.payload_json,r.run_id,r.attempt,b.payload_json AS bundle_json FROM audit_events a
                    JOIN analysis_runs r ON r.run_id=json_extract(a.payload_json,'$.run_id')
                    JOIN evidence_bundles b ON b.bundle_id=r.bundle_id
                    WHERE a.event_type='analysis_validation' AND json_extract(a.payload_json,'$.job_id')=?
                    AND b.asset_id=? ORDER BY a.id DESC LIMIT 2""", (selected['job_id'], asset_id)).fetchall()
                for record in records:
                    value = json.loads(record['payload_json'])['diagnostic']
                    known_ids = [item['evidence_id'] for item in json.loads(record['bundle_json'])['evidence']]
                    diagnostics.append({'run_id': record['run_id'], 'attempt': record['attempt'],
                        'recorded_at': record['created_at'], 'diagnostic': {'version': 1,
                        'stage': value['stage'] if value.get('stage') in {'schema','semantic'} else 'unknown',
                        'issues': [sanitize_issue(i, known_ids)
                                   for i in value.get('issues',[])[:8]]}})
                runtime_records = con.execute("""SELECT a.created_at,a.payload_json,r.run_id,r.attempt FROM audit_events a
                    JOIN analysis_runs r ON r.run_id=json_extract(a.payload_json,'$.run_id')
                    JOIN evidence_bundles b ON b.bundle_id=r.bundle_id
                    WHERE a.event_type='analysis_cli_failure' AND json_extract(a.payload_json,'$.job_id')=?
                    AND b.asset_id=? ORDER BY a.id DESC LIMIT 2""", (selected['job_id'], asset_id)).fetchall()
                for record in runtime_records:
                    value = json.loads(record['payload_json']).get('diagnostic', {})
                    execution_diagnostics.append({'run_id': record['run_id'], 'attempt': record['attempt'],
                        'recorded_at': record['created_at'], 'diagnostic': safe_execution_diagnostic(value)})
        return {**({'manual_scope': manual_scope} if manual_scope else {}), **({'validation_diagnostics': diagnostics} if diagnostics else {}),
                **({'execution_diagnostics': execution_diagnostics} if execution_diagnostics else {}), 'asset_id': asset_id, 'job': job_view(selected) if selected else None,
                'queue_position': position, 'queue_position_reason': reason,
                'result_analysis': result_analysis, 'as_of': now}

    @app.get('/api/v1/assets/{asset_id}')
    def asset(asset_id: str):
        a = next((a for a in asset_rows() if a['id'] == asset_id), None)
        if not a: raise HTTPException(404, 'Ativo não encontrado')
        es = [e for e in event_rows() if asset_id in e['asset_ids']]
        analyses = store.rows('SELECT payload_json,bundle_id,status FROM analyses WHERE asset_id=? ORDER BY as_of DESC,created_at DESC,analysis_id DESC LIMIT 1', (asset_id,))
        bs = store.rows('SELECT payload_json FROM evidence_bundles WHERE bundle_id=?', (analyses[0]['bundle_id'],)) if analyses else store.rows('SELECT payload_json FROM evidence_bundles WHERE asset_id=? ORDER BY as_of DESC LIMIT 1', (asset_id,))
        bundle = json.loads(bs[0]['payload_json']) if bs else None
        price_view=prices(asset_id)
        return {**a, 'prices': price_view['items'], 'price_snapshot':price_view.get('price_snapshot'),
                'current_price_metrics':price_view.get('current_price_metrics',[]),'events': es, 'articles': articles_for([i for e in es for i in e['evidence_ids']]),
                'analysis_state': analyses[0]['status'] if analyses else 'indisponível',
                'analysis': json.loads(analyses[0]['payload_json']) if analyses else None, 'metrics': bundle['computed_metrics'] if bundle else [],
                'evidence': bundle['evidence'] if bundle else [], 'ai': ai_capabilities(), 'as_of': iso()}

    @app.post('/api/v1/assets/{asset_id}/analyse', status_code=202)
    def analyse(asset_id: str, request: Request, body: AnalysisRequest | None = None):
        reanalyze = body is not None and body.reanalyze
        if reanalyze and not request.headers.get('idempotency-key'):
            raise HTTPException(422, 'manual_reanalysis_idempotency_key_required')
        if process_mode != 'manual-analysis' and inference_restricted():
            raise HTTPException(409, 'analysis_disabled_by_execution_mode')
        def apply(con):
            if reanalyze and process_mode != 'manual-analysis':
                raise HTTPException(409, 'manual_reanalysis_scope_required')
            a = one('SELECT * FROM assets WHERE asset_id=? AND eligible=1', (asset_id,), con)
            key = request.headers.get('idempotency-key') or f'analyse:{asset_id}:{iso()[:13]}'
            payload = {'asset_id': a['asset_id']}
            if process_mode == 'manual-analysis':
                state, owner = manual_slot(store, con)
                if state != 'ready':
                    raise HTTPException(409, 'manual_analysis_busy' if state == 'busy' else 'manual_worker_unavailable')
                if not ai_status().get('enabled') or mode != 'real':
                    raise HTTPException(409, 'manual_analysis_unavailable')
                quota, valid = manual_quota(store, con)
                if not valid or quota['remaining'] == 0:
                    raise HTTPException(409, 'manual_quota_exhausted' if valid else 'manual_quota_configuration_invalid')
                payload['manual_owner'] = owner
                if reanalyze:
                    payload['reanalyze'] = True
            queued = store.enqueue('analyse', payload, key, max_attempts=1 if process_mode == 'manual-analysis' else 3, connection=con)
            if queued['kind'] != 'analyse' or queued['payload'].get('asset_id') != asset_id:
                raise HTTPException(409, 'Chave de idempotência já usada em outro comando')
            if queued['payload'].get('reanalyze', False) is not reanalyze:
                raise HTTPException(409, 'manual_reanalysis_request_mismatch')
            if process_mode == 'manual-analysis' and queued['payload'].get('manual_owner') != owner:
                raise HTTPException(409, 'manual_scope_inactive')
            return mutation_job_view(queued)
        # Preserve the fingerprint of historical/default commands. An explicit
        # reanalysis is a different durable command, never an implicit retry.
        return mutate(request, {'reanalyze': True} if reanalyze else None, apply, 202)

    @app.get('/api/v1/events')
    def events(search: str = '', page: int = Query(1,ge=1), page_size: int = Query(50,ge=1,le=200)):
        return bounded([e for e in event_rows() if search.casefold() in e['title'].casefold()], page, page_size)

    @app.get('/api/v1/events/{event_id}')
    def event(event_id: str):
        found = next((e for e in event_rows() if e['id'] == event_id), None)
        if not found: raise HTTPException(404, 'Evento não encontrado')
        return found

    @app.get('/api/v1/articles/{article_id}')
    def article(article_id: str):
        found = next(iter(articles_for(article_id=article_id,current_only=False)), None)
        if not found: raise HTTPException(404, 'Notícia não encontrada')
        found['revisions'] = store.rows('SELECT revision_id,title,published_at,modified_at,fetched_at,access_level FROM article_revisions WHERE article_id=? ORDER BY fetched_at', (article_id,))
        return found

    @app.get('/api/v1/jobs')
    def jobs():
        return {'items': [job_view(j) for j in store.list_jobs(200)], 'as_of': iso()}

    @app.get('/api/v1/jobs/{job_id}')
    def job(job_id: str):
        return job_view(store.get_job(job_id))

    def require_consumer(kind, con):
        control = job_admission(store, process_mode, connection=con)
        if kind not in control['allowed_kinds']:
            reason = control['reason'] if control['reason'] != 'ready' else 'manual_consumer_incompatible'
            raise HTTPException(409, reason)

    @app.post('/api/v1/jobs', status_code=202)
    def enqueue(body: JobRequest, request: Request):
        def apply(con):
            if process_mode == 'manual-analysis':
                raise HTTPException(409, 'manual_operation_not_allowed')
            if body.scope:
                raise HTTPException(422, 'Escopo arbitrário não permitido; use a janela configurada')
            require_consumer(body.kind, con)
            key = request.headers.get('idempotency-key') or f'manual:{body.kind}:{iso()[:16]}'
            queued = store.enqueue(body.kind, {}, key, connection=con)
            if queued['kind'] != body.kind or queued['payload'] != {}:
                raise HTTPException(409, 'Chave de idempotência já usada em outro comando')
            return mutation_job_view(queued)
        return mutate(request, body.model_dump(), apply, 202)

    @app.post('/api/v1/jobs/{job_id}/cancel')
    def cancel(job_id: str, request: Request):
        def apply(con):
            one('SELECT job_id FROM jobs WHERE job_id=?', (job_id,), con)
            return mutation_job_view(store.cancel_job(job_id, connection=con))
        return mutate(request, None, apply)

    @app.post('/api/v1/jobs/{job_id}/retry')
    def retry(job_id: str, request: Request):
        def apply(con):
            job = one('SELECT kind,payload_json FROM jobs WHERE job_id=?', (job_id,), con)
            if process_mode == 'manual-analysis' or json.loads(job['payload_json']).get('manual_owner') is not None:
                raise HTTPException(409, 'manual_operation_not_allowed')
            if job['kind'] in ANALYSIS_KINDS:
                lease = con.execute('SELECT execution_mode FROM worker_lease WHERE expires_at>?', (iso(),)).fetchone()
                if process_mode != 'analysis-enabled' or (lease and lease['execution_mode'] != 'analysis-enabled'):
                    raise HTTPException(409, 'analysis_disabled_by_execution_mode')
            require_consumer(job['kind'], con)
            return mutation_job_view(store.retry_job(job_id, connection=con))
        return mutate(request, None, apply)

    @app.get('/api/v1/sources')
    def sources():
        from .price_vintages import vintage_status
        captured=vintage_status(store,iso())
        states = {p['provider_id']: p for p in store.rows('SELECT * FROM provider_state')}
        scheduled = calendar_status(store,iso())
        definitions = source_defs + [{'id': x, 'name': n} for x,n in [('brapi_catalog','brapi · cadastro'),('brapi_prices','brapi · preços'),('gdelt_doc2','GDELT · descoberta'),('bcb_sgs','BCB · séries macro'),('cvm','CVM · documentos'),('market_calendar','Calendário de pregões'),('codex_cli','Codex CLI')]]
        known_ids={s['id'] for s in definitions}
        definitions += [{'id': key,'name':source_names.get(key.split(':')[0],key)+' · descoberta GDELT'} for key in states if key not in known_ids and key.endswith(':gdelt')]
        result = []
        for s in definitions:
            p = states.get(s['id'], {})
            result.append({'id': s['id'], 'name': s['name'], 'status': p.get('status','not_tested'),
                           'last_attempt_at': p.get('last_attempt_at'), 'last_success_at': p.get('last_success_at'),
                           'error': p.get('error_code'), 'reason': p.get('error_code'), 'next_attempt_at':p.get('next_attempt_at'),
                           'details': json.loads(p.get('details_json','{}')), 'count': store.rows('SELECT COUNT(*) n FROM articles WHERE source_id=?',(s['id'],))[0]['n']})
            if s['id']=='market_calendar' and scheduled is not None:
                result[-1].update({key:scheduled[key] for key in ('status','reason','details','available_start','available_end','count','capability')})
                result[-1].update(error=scheduled['error_code'],last_success_at=scheduled['available_at'],
                                  next_attempt_at=scheduled['next_attempt_at'])
            if s['id']=='brapi_prices' and captured is not None:
                result[-1].update(captured)
        return {'items': result, 'as_of': iso()}

    @app.get('/api/v1/coverage')
    def coverage():
        c=store.coverage()
        scheduled = calendar_status(store,iso())
        calendar_gap = scheduled['reason'] if scheduled is not None else 'Ajustes e calendário de pregões ainda precisam de homologação.'
        return {**c, 'archived_articles':c['articles'], 'articles':len(articles_for()), 'events':len(event_rows()), 'as_of': iso(), 'partitions': store.rows('SELECT * FROM coverage_partitions ORDER BY start_at DESC LIMIT 500'),
                'quotas': store.rows('SELECT provider,period,SUM(units) used FROM quota_ledger WHERE status<>\'released\' GROUP BY provider,period'),
                'known_gaps': ['RSS não garante três meses de arquivo.', calendar_gap, 'Fundamentos CVM indisponíveis; múltiplos não calculados.'] + ([ai_capabilities()['reason']] if not ai_capabilities()['enabled'] else [])}

    @app.get('/api/v1/watchlist')
    def watchlist():
        return {'items': [a for a in asset_rows() if a['watchlisted']]}

    @app.post('/api/v1/watchlist')
    def add_watch(body: WatchRequest, request: Request):
        def apply(con):
            one('SELECT asset_id FROM assets WHERE asset_id=?', (body.asset_id,), con)
            con.execute('INSERT OR IGNORE INTO watchlist VALUES(?,?)',(body.asset_id,iso()))
            return {'asset_id': body.asset_id, 'watchlisted': True}
        return mutate(request, body.model_dump(), apply)

    @app.delete('/api/v1/watchlist/{asset_id}')
    def remove_watch(asset_id: str, request: Request):
        def apply(con):
            con.execute('DELETE FROM watchlist WHERE asset_id=?', (asset_id,))
            return {'asset_id': asset_id, 'watchlisted': False}
        return mutate(request, None, apply)

    @app.get('/api/v1/alerts')
    def alerts():
        return {'items': [{**a, 'id':a['alert_id'], 'read':bool(a['is_read']), 'archived':bool(a['archived'])} for a in store.rows('SELECT * FROM alerts ORDER BY created_at DESC LIMIT 200')]}

    @app.patch('/api/v1/alerts/{alert_id}')
    def patch_alert(alert_id: str, body: AlertPatch, request: Request):
        def apply(con):
            a = one('SELECT * FROM alerts WHERE alert_id=?', (alert_id,), con)
            con.execute('UPDATE alerts SET is_read=?,archived=? WHERE alert_id=?',(a['is_read'] if body.read is None else int(body.read),a['archived'] if body.archived is None else int(body.archived),alert_id))
            return {'id':alert_id,'updated':True}
        return mutate(request, body.model_dump(), apply)

    @app.get('/api/v1/alert-rules')
    def rules():
        return {'items': [{**r, **json.loads(r['config_json']), 'id':r['rule_id'], 'enabled':bool(r['enabled'])} for r in store.rows('SELECT * FROM alert_rules ORDER BY created_at')]}

    def save_rule(body, rule_id, con):
        if body.asset_id: one('SELECT asset_id FROM assets WHERE asset_id=?', (body.asset_id,), con)
        now=iso()
        con.execute('INSERT INTO alert_rules VALUES(?,?,?,?,?,?,?) ON CONFLICT(rule_id) DO UPDATE SET name=excluded.name,kind=excluded.kind,enabled=excluded.enabled,config_json=excluded.config_json,updated_at=excluded.updated_at',
                    (rule_id,body.name,body.kind,int(body.enabled),dumps({'asset_id':body.asset_id,'cooldown_minutes':body.cooldown_minutes}),now,now))
        return {'id':rule_id, **body.model_dump()}

    @app.post('/api/v1/alert-rules')
    def add_rule(body: Rule, request: Request):
        return mutate(request, body.model_dump(), lambda con: save_rule(body, uid('rule'), con))

    @app.patch('/api/v1/alert-rules/{rule_id}')
    def update_rule(rule_id: str, body: dict, request: Request):
        def apply(con):
            r = one('SELECT * FROM alert_rules WHERE rule_id=?', (rule_id,), con)
            current = {'name':r['name'], 'kind':r['kind'], 'enabled':bool(r['enabled']), **json.loads(r['config_json'])}
            try: value = Rule.model_validate({**current, **body})
            except ValueError: raise HTTPException(422, 'Regra inválida')
            return save_rule(value, rule_id, con)
        return mutate(request, body, apply)

    @app.delete('/api/v1/alert-rules/{rule_id}')
    def delete_rule(rule_id: str, request: Request):
        def apply(con):
            con.execute('DELETE FROM alert_rules WHERE rule_id=?', (rule_id,))
            return {'deleted':True}
        return mutate(request, None, apply)

    @app.get('/api/v1/settings')
    def settings(): return settings_value()

    @app.patch('/api/v1/settings')
    def save_settings(body: SettingsPatch, request: Request):
        values = body.model_dump(exclude_none=True)
        def apply(con):
            if 'horizons_days' in values and (not values['horizons_days'] or len(set(values['horizons_days'])) != len(values['horizons_days'])):
                raise HTTPException(422, 'Selecione 30 e/ou 90 dias sem duplicatas')
            for key, value in values.items():
                con.execute('INSERT INTO settings(key,value_json,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,version=version+1,updated_at=excluded.updated_at', (key, dumps(value), iso()))
            return settings_value(con)
        return mutate(request, values, apply)

    @app.get('/api/v1/analyses')
    def analyses(search: str = ''):
        rows=store.rows('SELECT * FROM analyses ORDER BY as_of DESC')
        return {'items': [{**json.loads(r['payload_json']), 'id':r['analysis_id'], 'evaluation': 'Ainda não maturou' if instant(r['as_of'])+timedelta(days=30)>datetime.now(timezone.utc) else 'Sem preços comparáveis para avaliação'} for r in rows if search.casefold() in r['asset_id'].casefold()], 'as_of':iso()}

    @app.get('/api/v1/analyses/{analysis_id}')
    def analysis(analysis_id: str):
        r=one('SELECT * FROM analyses WHERE analysis_id=?',(analysis_id,))
        b=one('SELECT payload_json FROM evidence_bundles WHERE bundle_id=?',(r['bundle_id'],))
        return {**json.loads(r['payload_json']), 'bundle':json.loads(b['payload_json'])}

    @app.get('/api/v1/exports/analyses')
    def export(format: Literal['json','csv']='json', search: str=''):
        data=analyses(search)
        if format=='json': return JSONResponse(data, headers={'Content-Disposition':'attachment; filename=radar-analises.json'})
        out=io.StringIO(); writer=csv.writer(out); writer.writerow(['analysis_id','asset_id','as_of','status','synthesis','evidence_ids'])
        for row in data['items']:
            fields=[str(row.get(k,'')) for k in ['analysis_id','asset_id','as_of','status','synthesis','evidence_ids']]
            writer.writerow(["'"+v if v.lstrip().startswith(('=','+','-','@','\t','\r')) else v for v in fields])
        return Response(out.getvalue(),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename=radar-analises.csv'})

    build=ROOT/'frontend/dist'
    if (build/'assets').exists(): app.mount('/assets',StaticFiles(directory=build/'assets'),name='assets')

    @app.get('/')
    def index():
        if not (build/'index.html').is_file(): raise HTTPException(503,'Frontend não compilado. Execute npm ci e npm run build em frontend.')
        return FileResponse(build/'index.html')

    return app
