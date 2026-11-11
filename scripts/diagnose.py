"""Observe RadarB3 without Store, collection, AI, service or scheduler changes."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.cli_runner import status
from backend.operational_diagnostics import diagnose, diagnostic_exit_code
from scripts.validate_live import local_get


def windows_task():
    if os.name!='nt':return {'status':'not_supported'}
    executable=Path(os.environ.get('SystemRoot','C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
    command="""$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$t=Get-ScheduledTask -TaskName 'RadarB3-ColetaDiaria';$i=Get-ScheduledTaskInfo -TaskName 'RadarB3-ColetaDiaria'
@{status='observed';task='RadarB3-ColetaDiaria';state=[string]$t.State;observed_at=[DateTime]::UtcNow.ToString('o');
last_run_time=$i.LastRunTime.ToUniversalTime().ToString('o');last_result=$i.LastTaskResult;
next_run_time=$i.NextRunTime.ToUniversalTime().ToString('o');missed_runs=$i.NumberOfMissedRuns;
start_when_available=$t.Settings.StartWhenAvailable;logon_type=[string]$t.Principal.LogonType}|ConvertTo-Json -Compress"""
    try:
        result=subprocess.run([str(executable),'-NoLogo','-NoProfile','-NonInteractive','-Command',command],
                              capture_output=True,timeout=20,creationflags=subprocess.CREATE_NO_WINDOW,check=False)
    except (OSError,subprocess.TimeoutExpired) as exc:
        return {'status':'observation_failed','error_type':type(exc).__name__}
    if result.returncode:return {'status':'observation_failed','error_type':'TaskQueryFailed'}
    if len(result.stdout)>10000:return {'status':'observation_failed','error_type':'ResponseTooLarge'}
    return json.loads(result.stdout.decode('utf-8-sig'))


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',required=True,type=Path)
    parser.add_argument('--root',type=Path,default=ROOT)
    parser.add_argument('--receipts-dir',required=True,type=Path)
    parser.add_argument('--base',default='http://127.0.0.1:8765/api/v1')
    parser.add_argument('--backup-report',type=Path)
    parser.add_argument('--backup-file',type=Path)
    parser.add_argument('--skip-windows-task',action='store_true',help='Explicitly mark task state not observed')
    args=parser.parse_args(argv)
    if bool(args.backup_report)!=bool(args.backup_file):parser.error('--backup-report e --backup-file devem ser usados juntos')
    report=diagnose(args.db,args.receipts_dir,args.root,base=args.base,get=local_get,cli_status=status,
                    schedule_probe=None if args.skip_windows_task else windows_task,backup_report=args.backup_report,backup_file=args.backup_file)
    # ASCII JSON survives Windows PowerShell OEM/ANSI redirection without corrupting accents.
    print(json.dumps(report,ensure_ascii=True,indent=2))
    return diagnostic_exit_code(report)


if __name__=='__main__':raise SystemExit(main())
