<#
.SYNOPSIS
  CRISVIS: descarga e instala todo con un solo comando (Windows 10/11).

.DESCRIPTION
  1. Instala con winget lo que falte: Git, uv (Python), Node.js LTS y Ollama.
  2. Descarga (o actualiza) el código de CRISVIS.
  3. npm run setup: entorno Python, interfaz, configuración, voz clonada y modelos.
  4. OpenClaw fijado a la versión auditada, solo en 127.0.0.1:18789 y endurecido
     (sin canales, hooks, webhooks, túneles ni arranque automático).
  5. Conectores de las skills de OpenClaw (gog para Google, ntn para Notion), sin
     conectar ninguna cuenta.

  Nunca pide ni guarda tokens. No arranca OpenClaw ni lo deja en el inicio de Windows.

.EXAMPLE
  # Repositorio privado (pide iniciar sesión en GitLab al clonar):
  winget install --id Git.Git -e --silent --accept-source-agreements --accept-package-agreements; $env:Path += ";$env:ProgramFiles\Git\cmd"; git clone https://gitlab.com/cristoferarango/crisvis.git "$HOME\CRISVIS"; powershell -ExecutionPolicy Bypass -File "$HOME\CRISVIS\install.ps1"

.EXAMPLE
  # Si el repositorio fuera público:
  irm https://gitlab.com/cristoferarango/crisvis/-/raw/main/install.ps1 | iex

.EXAMPLE
  # Con opciones:
  & ([scriptblock]::Create((irm https://gitlab.com/cristoferarango/crisvis/-/raw/main/install.ps1))) -SinVoz

.EXAMPLE
  # Desde una copia ya descargada:
  powershell -ExecutionPolicy Bypass -File install.ps1
#>
[CmdletBinding()]
param(
    [string]$Destino = (Join-Path $HOME 'CRISVIS'),
    [string]$Repo = 'https://gitlab.com/cristoferarango/crisvis.git',
    [string]$Rama = 'main',
    [string]$OpenClawVersion = '2026.9.8',
    [switch]$SinVoz,
    [switch]$SinModelo,
    [switch]$SinVision,
    [switch]$SinOpenClaw,
    [switch]$SinConectores
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Paso($texto) { Write-Host "`n> $texto" -ForegroundColor Cyan }
function Bien($texto) { Write-Host "  OK $texto" -ForegroundColor Green }
function Aviso($texto) { Write-Host "  ! $texto" -ForegroundColor Yellow }
function Falla($texto) { Write-Host "`nX $texto" -ForegroundColor Red; exit 1 }

function Update-SessionPath {
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = "$machine;$user"
}

function Test-Cmd([string]$name) { [bool](Get-Command $name -ErrorAction SilentlyContinue) }

function Install-Winget([string]$id, [string]$cmd, [string]$nombre) {
    if (Test-Cmd $cmd) { Bien "$nombre ya está instalado"; return }
    Write-Host "  Instalando $nombre ($id)..."
    winget install --id $id -e --silent --accept-source-agreements --accept-package-agreements
    Update-SessionPath
    if (-not (Test-Cmd $cmd)) {
        Falla "$nombre no quedó disponible. Cierra esta consola, abre otra y vuelve a ejecutar el instalador."
    }
    Bien "$nombre instalado"
}

function Invoke-Checked([string]$what, [scriptblock]$block) {
    & $block
    if ($LASTEXITCODE -ne 0) { Falla "Falló: $what" }
}

if ($env:OS -ne 'Windows_NT') { Falla 'Este instalador es para Windows. En Linux/macOS usa: npm run setup' }
if (-not (Test-Cmd 'winget')) {
    Falla 'Falta winget (Instalador de aplicaciones). Instálalo desde Microsoft Store y repite.'
}

# -- 1. requisitos -------------------------------------------------------------

Paso 'Requisitos'
Install-Winget 'Git.Git' 'git' 'Git'
Install-Winget 'astral-sh.uv' 'uv' 'uv (Python)'
Install-Winget 'OpenJS.NodeJS.LTS' 'node' 'Node.js LTS'
$nodeVersion = [version]((node --version).TrimStart('v'))
if ($nodeVersion -lt [version]'24.16.0') {
    Aviso "Node $nodeVersion es antiguo (OpenClaw pide >= 24.16). Actualizando Node.js LTS..."
    winget upgrade --id OpenJS.NodeJS.LTS -e --silent --accept-source-agreements --accept-package-agreements
    Update-SessionPath
    $nodeVersion = [version]((node --version).TrimStart('v'))
}
Bien "Node $nodeVersion"
Install-Winget 'Ollama.Ollama' 'ollama' 'Ollama'

# -- 2. código -----------------------------------------------------------------

Paso 'Código de CRISVIS'
$aqui = if ($PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot 'scripts\setup.mjs'))) { $PSScriptRoot }
if ($aqui) {
    $Destino = $aqui
    Bien "Uso la copia actual: $Destino"
} elseif (Test-Path (Join-Path $Destino '.git')) {
    Invoke-Checked 'git pull' { git -C $Destino pull --ff-only }
    Bien "Actualizado en $Destino"
} else {
    Invoke-Checked 'git clone' { git clone --branch $Rama $Repo $Destino }
    Bien "Descargado en $Destino"
}
Set-Location $Destino

# -- 3. Ollama en marcha ---------------------------------------------------------

Paso 'Ollama'
ollama list *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host '  Arrancando Ollama...'
    Start-Process -FilePath 'ollama' -ArgumentList 'serve' -WindowStyle Hidden
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Seconds 1
        ollama list *> $null
        if ($LASTEXITCODE -eq 0) { break }
    }
}
if ($LASTEXITCODE -eq 0) { Bien 'Ollama responde' } else { Aviso 'Ollama no responde; los modelos se descargarán cuando lo abras.' }

# -- 4. CRISVIS ----------------------------------------------------------------

Paso 'CRISVIS (Python, interfaz, configuración, voz y modelos)'
$flags = @()
if ($SinVoz) { $flags += '--sin-voz' }
if ($SinModelo) { $flags += '--sin-modelo' }
if ($SinVision) { $flags += '--sin-vision' }
if ($flags.Count) {
    Invoke-Checked 'npm run setup' { npm run setup -- @flags }
} else {
    Invoke-Checked 'npm run setup' { npm run setup }
}

# -- 5. OpenClaw (solo local) ------------------------------------------------------

if (-not $SinOpenClaw) {
    Paso "OpenClaw $OpenClawVersion (solo 127.0.0.1:18789)"
    $actual = if (Test-Cmd 'openclaw') { (openclaw --version 2>$null | Out-String) } else { '' }
    if ($actual -notmatch [regex]::Escape($OpenClawVersion)) {
        Invoke-Checked 'npm install -g openclaw' {
            npm install -g "openclaw@$OpenClawVersion" --allow-scripts=openclaw --no-fund --no-audit
        }
        Update-SessionPath
    }
    Bien "$((openclaw --version 2>$null | Out-String).Trim())"

    $config = Join-Path $HOME '.openclaw\openclaw.json'
    if (-not (Test-Path $config)) {
        Write-Host '  Configuración inicial local (sin servicio, canales, skills ni hooks)...'
        Invoke-Checked 'openclaw onboard' {
            openclaw onboard --non-interactive --accept-risk --mode local `
                --auth-choice ollama --custom-base-url http://127.0.0.1:11434 --custom-model-id qwen3:8b `
                --gateway-port 18789 --gateway-bind loopback --gateway-auth token `
                --skip-daemon --skip-channels --skip-skills --skip-hooks --skip-search --skip-ui `
                --skip-health --skip-bootstrap --suppress-gateway-token-output
        }
    }
    $patch = Join-Path $Destino 'scripts\openclaw-endurecido.json5'
    Invoke-Checked 'openclaw config patch --dry-run' { openclaw config patch --file $patch --dry-run }
    Invoke-Checked 'openclaw config patch' { openclaw config patch --file $patch }
    Invoke-Checked 'openclaw config validate' { openclaw config validate }
    Bien 'OpenClaw endurecido: loopback, sin canales, hooks, webhooks, Tailscale, mDNS ni cron'
    Bien 'No se arranca solo. Cuando lo necesites: openclaw gateway run --port 18789 --bind loopback'
}

# -- 6. conectores ---------------------------------------------------------------

if (-not $SinConectores) {
    & (Join-Path $Destino 'scripts\conectores.ps1')
}

Write-Host "`nListo. Para arrancar CRISVIS:" -ForegroundColor Green
Write-Host "  cd `"$Destino`""
Write-Host '  npm start          (abre http://127.0.0.1:8787)'
