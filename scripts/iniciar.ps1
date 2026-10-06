<#
.SYNOPSIS
  Arranca CRISVIS en segundo plano y abre su interfaz. Es lo que ejecuta el icono del escritorio.

.DESCRIPTION
  Si CRISVIS ya está en marcha, solo abre http://127.0.0.1:8787. Si no, se asegura
  de que Ollama responde, arranca el núcleo sin ventana (él lanza la voz clonada)
  y abre el navegador cuando está listo. Arranca siempre en CONFIRMAR; LIBRE solo
  con `crisvis permisos libre`. No arranca OpenClaw.
  Registro de la consola: ~\.crisvis\logs\crisvis-consola.log
#>
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$url = 'http://127.0.0.1:8787'

function Test-Up([string]$u) {
    try { Invoke-WebRequest -UseBasicParsing -Uri $u -TimeoutSec 3 | Out-Null; $true } catch { $false }
}

function Show-Error([string]$text) {
    Add-Type -AssemblyName PresentationFramework
    [void][System.Windows.MessageBox]::Show($text, 'CRISVIS', 'OK', 'Error')
}

if (Test-Up "$url/health") {
    Start-Process $url
    exit 0
}

if (-not (Test-Up 'http://127.0.0.1:11434/api/tags')) {
    $app = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama app.exe'
    if (Test-Path $app) { Start-Process $app } else { Start-Process 'ollama' -ArgumentList 'serve' -WindowStyle Hidden }
    for ($i = 0; $i -lt 30 -and -not (Test-Up 'http://127.0.0.1:11434/api/tags'); $i++) { Start-Sleep 1 }
}

foreach ($v in 'CRISVIS_HOME', 'CRISVIS_VOZ_DIR', 'CRISVIS_CONFIG', 'OPENJARVIS_HOME', 'CRISVIS_PORT',
    'CRISVIS_HOST', 'CRISVIS_PERMISOS', 'CRISVIS_DEV') {
    Remove-Item "Env:$v" -ErrorAction SilentlyContinue
}

$logs = Join-Path $HOME '.crisvis\logs'
New-Item -ItemType Directory -Path $logs -Force | Out-Null
$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $uv) {
    Show-Error 'No encuentro uv. Ejecuta install.ps1 para instalar CRISVIS.'
    exit 1
}
Start-Process -FilePath $uv -ArgumentList 'run', '--no-sync', 'python', '-m', 'crisvis', '--no-browser' `
    -WorkingDirectory $root -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logs 'crisvis-consola.log') `
    -RedirectStandardError (Join-Path $logs 'crisvis-consola.err.log')

for ($i = 0; $i -lt 120; $i++) {
    Start-Sleep 1
    if (Test-Up "$url/health") {
        Start-Process $url
        exit 0
    }
}
Show-Error "CRISVIS no arrancó en 2 minutos. Revisa $logs\crisvis-consola.err.log"
exit 1
