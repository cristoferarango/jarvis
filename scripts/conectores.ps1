<#
.SYNOPSIS
  Descarga los conectores que usan las skills incluidas en OpenClaw, sin conectar ninguna cuenta.

.DESCRIPTION
  - gog (Gmail, Calendar, Drive, Contacts, Sheets, Docs): binario oficial de
    github.com/openclaw/gogcli, versión fijada, comprobado con checksums.txt.
  - ntn (Notion): CLI oficial de Notion en npm, versión fijada.

  No ejecuta `gog auth`, `ntn login` ni nada que pida o guarde tokens. Conectar
  una cuenta es un paso aparte que requiere aprobación (docs/CONNECTOR_MATRIX.md).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\conectores.ps1
#>
[CmdletBinding()]
param(
    [string]$GogVersion = '0.43.0',
    [string]$NtnVersion = '0.23.17',
    [switch]$SinGog,
    [switch]$SinNotion
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Paso($texto) { Write-Host "`n> $texto" -ForegroundColor Cyan }
function Bien($texto) { Write-Host "  OK $texto" -ForegroundColor Green }
function Aviso($texto) { Write-Host "  ! $texto" -ForegroundColor Yellow }

function Add-UserPath([string]$dir) {
    $actual = [Environment]::GetEnvironmentVariable('Path', 'User')
    $partes = @($actual -split ';' | Where-Object { $_ })
    if ($partes -notcontains $dir) {
        [Environment]::SetEnvironmentVariable('Path', (($partes + $dir) -join ';'), 'User')
    }
    if (($env:Path -split ';') -notcontains $dir) { $env:Path = "$env:Path;$dir" }
}

if (-not $SinGog) {
    Paso "gog $GogVersion (Google Workspace: Gmail, Calendar, Drive...)"
    $arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'arm64' } else { 'amd64' }
    $zipName = "gogcli_${GogVersion}_windows_${arch}.zip"
    $base = "https://github.com/openclaw/gogcli/releases/download/v$GogVersion"
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ("gogcli-" + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $tmp | Out-Null
    try {
        $zip = Join-Path $tmp $zipName
        $sums = Join-Path $tmp 'checksums.txt'
        Invoke-WebRequest -UseBasicParsing -Uri "$base/$zipName" -OutFile $zip
        Invoke-WebRequest -UseBasicParsing -Uri "$base/checksums.txt" -OutFile $sums
        $esperado = (Get-Content $sums | Where-Object { $_ -match "\s\*?$([regex]::Escape($zipName))$" } |
            ForEach-Object { ($_ -split '\s+')[0] } | Select-Object -First 1)
        if (-not $esperado) { throw "checksums.txt no lista $zipName" }
        $real = (Get-FileHash -Algorithm SHA256 $zip).Hash
        if ($real -ne $esperado.ToUpper()) { throw "SHA-256 no coincide para $zipName" }
        Bien "SHA-256 verificado ($($real.Substring(0, 16))...)"

        Expand-Archive -Path $zip -DestinationPath (Join-Path $tmp 'x') -Force
        $exe = Get-ChildItem -Path (Join-Path $tmp 'x') -Recurse -Filter 'gog.exe' | Select-Object -First 1
        if (-not $exe) { throw "el ZIP no contiene gog.exe" }
        $dest = Join-Path $env:LOCALAPPDATA 'Programs\gogcli'
        New-Item -ItemType Directory -Path $dest -Force | Out-Null
        Copy-Item $exe.FullName (Join-Path $dest 'gog.exe') -Force
        Add-UserPath $dest
        Bien "gog.exe en $dest (añadido al PATH del usuario)"
        try {
            $ver = & (Join-Path $dest 'gog.exe') --version 2>&1
            Bien "gog --version: $ver"
        } catch {
            Aviso "gog.exe no arrancó: $($_.Exception.Message)"
            Aviso "Si lo bloquea el Control inteligente de aplicaciones, NO lo desactives; usa WSL2 o Docker (docs/RISK_REGISTER.md R-07)."
        }
    } finally {
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    }
}

if (-not $SinNotion) {
    Paso "ntn $NtnVersion (CLI oficial de Notion)"
    # Su preinstall (node install.cjs) solo prepara el propio paquete; se autoriza solo ese.
    npm install -g "ntn@$NtnVersion" --allow-scripts=ntn --no-fund --no-audit
    if ($LASTEXITCODE -ne 0) { throw "npm install -g ntn@$NtnVersion falló" }
    Bien "ntn --version: $(ntn --version 2>&1)"
}

Write-Host "`nConectores descargados. Ninguna cuenta conectada." -ForegroundColor Green
Write-Host "Conectar Gmail/Calendar/Drive (gog auth) o Notion (ntn login) es un paso aparte: ver docs/CONNECTOR_MATRIX.md."
