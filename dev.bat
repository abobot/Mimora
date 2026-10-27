@echo off
REM Mimora dev launcher - uses the uv tool env.
REM Data (models, config, logs) lives in this project directory (repo mode).
REM Usage: dev.bat [args]

set PY=C:\Users\abobot\AppData\Roaming\uv\tools\mimora\Scripts\python.exe

if "%~1"=="" (
    %PY% main.py
) else (
    %PY% %*
)
