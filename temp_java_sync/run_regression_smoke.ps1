# Run Xecurify + Acme NDA regression smoke (Dev UI must be on :8090).
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

python test_xecurify_policies.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

python test_acme_nda_policies.py
exit $LASTEXITCODE
