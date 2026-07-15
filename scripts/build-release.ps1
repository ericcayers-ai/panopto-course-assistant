[CmdletBinding()]
param(
    [string]$OutputDir
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    throw "Python 3.10 or newer is required to build a release."
}

$arguments = @((Join-Path $PSScriptRoot "build_release.py"))
if ($OutputDir) {
    $arguments += "--output-dir"
    $arguments += $OutputDir
}

& $python.Source @arguments
exit $LASTEXITCODE
