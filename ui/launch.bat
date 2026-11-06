@echo off
rem Starts the Extractor UI using the project's virtual environment.
cd /d "%~dp0\.."
".venv\Scripts\python.exe" ui\run.py %*
pause
