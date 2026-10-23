@echo off
rem Starts Skin Studio at http://localhost:7777 and opens it in the browser.
rem Uses any Python 3.10+: the py launcher, python on PATH, or Blender's own Python.
cd /d "%~dp0"
rem "python --version" also weeds out the Microsoft Store stub, which is not a real Python.
py -3 --version >nul 2>nul && (py -3 server.py %* & goto :eof)
python --version >nul 2>nul && (python server.py %* & goto :eof)
for /d %%B in ("%ProgramFiles%\Blender Foundation\Blender *") do (
  for /d %%V in ("%%B\*") do if exist "%%V\python\bin\python.exe" (
    "%%V\python\bin\python.exe" server.py %* & goto :eof
  )
)
echo Could not find Python. Install Python 3 from python.org, or Blender, and try again.
pause
