<#
.SYNOPSIS
  Crea el icono «CRISVIS» en el escritorio (uno solo; si ya existe, lo reemplaza).
  No añade nada al inicio de Windows.
#>
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$desktop = [Environment]::GetFolderPath('Desktop')
$lnk = Join-Path $desktop 'CRISVIS.lnk'

$shell = New-Object -ComObject WScript.Shell
$s = $shell.CreateShortcut($lnk)
$s.TargetPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$s.Arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$root\scripts\iniciar.ps1`""
$s.WorkingDirectory = $root
$s.IconLocation = "$root\assets\crisvis.ico,0"
$s.Description = 'Iniciar CRISVIS'
$s.WindowStyle = 7
$s.Save()
Write-Host "Icono creado: $lnk"
