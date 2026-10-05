$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "未找到 .venv。请先运行: python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -e ."
}

Set-Location -LiteralPath $PSScriptRoot
& $python run.py
