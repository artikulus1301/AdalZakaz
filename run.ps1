# Run AdalZakaz on Windows PowerShell
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creating Python 3.11 environment..."
    py -3.11 -m venv .venv
}

Write-Host "Installing dependencies..."
& .venv\Scripts\python.exe -m pip install -q -r requirements.txt

if (-not (Test-Path ".env")) {
    Write-Host "Creating .env from .env.example..."
    Copy-Item .env.example .env
}

Write-Host "Starting AdalZakaz at http://localhost:8000"
Write-Host "Seller password: check ADAL_SELLER_PASSWORD in .env (default: demo)"
Write-Host ""

& .venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
