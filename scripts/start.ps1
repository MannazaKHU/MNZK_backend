$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Virtual environment creation failed" }
}
& .venv\Scripts\python.exe scripts/setup_env.py
if ($LASTEXITCODE -ne 0) { throw "Environment setup failed" }
& .venv\Scripts\python.exe -m pip install -q -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed" }
& .venv\Scripts\python.exe -m app
