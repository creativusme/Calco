@echo off
rem Starts the Calco engine and opens the desktop UI in the browser.
cd /d "%~dp0"
if not exist engine\.venv\Scripts\python.exe (
  echo First run: creating the Python environment...
  python -m venv engine\.venv || goto :fail
  engine\.venv\Scripts\python -m pip install -e engine || goto :fail
)
rem OpenMVS, the GPU engine and the splat engine are downloaded once by the engine itself on start,
rem with progress shown in the app (no download buttons)
if not exist desktop\dist\index.html (
  echo First run: building the desktop UI...
  pushd desktop && call npm install && call npm run build && popd
)
rem open the browser once the engine has had a few seconds to start
start "" /min cmd /c "timeout /t 4 >nul & start http://localhost:7878"
engine\.venv\Scripts\python -m calco serve
goto :eof
:fail
echo Setup failed. See the messages above.
pause
