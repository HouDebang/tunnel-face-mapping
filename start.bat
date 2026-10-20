@echo off
setlocal EnableExtensions DisableDelayedExpansion
pushd "%~dp0"
if errorlevel 1 (
  echo ERROR: Cannot open the project directory.
  pause
  exit /b 1
)
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo ERROR: Project Python environment is missing.
  echo See README.md for installation instructions.
  popd
  pause
  exit /b 1
)
echo Face Mapping Workbench
echo Open http://127.0.0.1:8000 in your browser.
echo Keep this window open. Press Ctrl+C to stop the service.
"%~dp0.venv\Scripts\python.exe" -m uvicorn face_mapping.api:app --host 127.0.0.1 --port 8000
set "LAUNCH_EXIT_CODE=%ERRORLEVEL%"
popd
echo Service stopped. Exit code: %LAUNCH_EXIT_CODE%
pause
exit /b %LAUNCH_EXIT_CODE%
