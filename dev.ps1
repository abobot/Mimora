# Mimora dev launcher - uses the uv tool env.
# Data (models, config, logs) lives in this project directory (repo mode).
# Usage: .\dev.ps1 [args]
$ErrorActionPreference = "Stop"

# The Python from the uv tool env (has torch, transformers, etc.).
$py = "C:\Users\abobot\AppData\Roaming\uv\tools\mimora\Scripts\python.exe"

# If no args, default to running the app.
if ($args.Count -eq 0) {
    $args = @("main.py")
}

& $py @args
