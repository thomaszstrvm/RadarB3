param([ValidateRange(1024,65535)][int]$Port=8765,[ValidateSet('real','demo')][string]$Mode='real',[ValidateSet('collection-only','analysis-enabled')][string]$ExecutionMode='collection-only',[switch]$NoWorker)
$ErrorActionPreference='Stop'
$radarPython=Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $radarPython)) { throw 'Ambiente ausente. Siga a instalação do README.md.' }
$radarArgs=@('--port',$Port,'--mode',$Mode,'--execution-mode',$ExecutionMode)
if ($NoWorker) { $radarArgs+='--no-worker' }
& $radarPython (Join-Path $PSScriptRoot 'run.py') @radarArgs
