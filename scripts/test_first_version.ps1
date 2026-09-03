$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
$env:PYTHONPATH = Join-Path $projectRoot "src"

Write-Host "[ACP] compile source"
python -m compileall -q src

Write-Host "[ACP] run first-version architecture and workflow tests"
python -m pytest -q `
  tests/test_acp_cli.py `
  tests/test_architecture_invariants.py `
  tests/test_calculations_plans.py `
  tests/test_calculation_executor.py `
  tests/test_batch_optimize.py `
  tests/test_irc.py `
  tests/test_pes_search.py `
  tests/test_acp_workflow_retirement.py `
  tests/test_acp_stage_tasks.py `
  tests/test_acp_artifacts.py `
  tests/test_acp_api.py `
  tests/test_acp_api_v1.py
