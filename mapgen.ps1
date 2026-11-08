# Runs mapgen with the local venv; working directory is tools/mapgen.
Set-Location -LiteralPath $PSScriptRoot
& "$PSScriptRoot\.venv\Scripts\python.exe" -m mapgen @args
exit $LASTEXITCODE
