@echo off
rem Double-click to open the TRACER desktop app.
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Install Python 3.10 or newer from https://www.python.org
  echo and tick "Add python.exe to PATH" during installation. See README.md, "What you need".
  pause
  exit /b 1
)
python -c "import tkinter" >nul 2>nul
if errorlevel 1 (
  echo The app could not start. Run the setup steps in README.md, "Setup and use", step 2.
  pause
  exit /b 1
)
start "" pythonw "%~dp0olc_gui.py"
