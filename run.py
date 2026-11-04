"""Start the local HTTP server and one durable worker; Ctrl+C stops both."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parent

def main():
    parser=argparse.ArgumentParser(description='Radar B3 local')
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--mode',choices=['real','demo'],default='real')
    parser.add_argument('--no-worker',action='store_true',help='Somente consulta, sem atualização automática')
    parser.add_argument('--execution-mode',choices=['collection-only','analysis-enabled','manual-analysis'],default='collection-only',help='Manual: somente análises solicitadas na UI, sem agenda ou coleta')
    args=parser.parse_args()
    if args.execution_mode == 'manual-analysis' and args.no_worker:
        parser.error('O modo manual-analysis exige seu worker dedicado; não combine com --no-worker')
    if not 1024<=args.port<=65535: parser.error('Porta deve estar entre 1024 e 65535')
    if not (ROOT/'frontend/dist/index.html').exists(): parser.error('Frontend ausente: execute npm ci e npm run build em frontend')
    os.chdir(ROOT)
    from backend.core import Store,wal_supported
    if not wal_supported(): parser.error('SQLite sem correção WAL: execute scripts/install_sqlite.py no Windows ou atualize o runtime')
    db=ROOT/'data'/args.mode/'radar.sqlite3'
    Store(db,args.mode,wal=True)
    try:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',args.port))
    except OSError: parser.error(f'Porta {args.port} ocupada; escolha --port sem interromper outro aplicativo')
    env=os.environ.copy()
    env['APP_MODE']=args.mode
    env['RADAR_EXECUTION_MODE']=args.execution_mode
    env['PYTHONUNBUFFERED']='1'
    # No model or paid API environment variables are needed by this application.
    for key in list(env):
        if key in {'OPENAI_API_KEY','ANTHROPIC_API_KEY','AZURE_OPENAI_API_KEY'}: env.pop(key)
    children=[]
    flags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name=='nt' else 0
    try:
        children.append(subprocess.Popen([sys.executable,'-m','uvicorn','backend.api:create_app','--factory','--host','127.0.0.1','--port',str(args.port),'--no-access-log'],cwd=ROOT,env=env,creationflags=flags))
        if not args.no_worker:
            children.append(subprocess.Popen([sys.executable,'-m','backend.worker','--db',str(db),'--profile',args.mode,'--execution-mode',args.execution_mode],cwd=ROOT,env=env,creationflags=flags))
        print(f'Radar B3: http://127.0.0.1:{args.port} | perfil {args.mode}. Ctrl+C encerra os processos.',flush=True)
        while all(child.poll() is None for child in children): time.sleep(0.5)
        if any(child.returncode not in {None,0} for child in children):
            raise RuntimeError('API ou worker encerrou com erro; revise o diagnóstico acima.')
    except KeyboardInterrupt:
        pass
    finally:
        for child in children:
            if child.poll() is None:
                try: child.send_signal(signal.CTRL_BREAK_EVENT if os.name=='nt' else signal.SIGTERM)
                except OSError: child.terminate()
        for child in children:
            try: child.wait(timeout=8)
            except subprocess.TimeoutExpired:
                child.kill(); child.wait()

if __name__=='__main__': main()
