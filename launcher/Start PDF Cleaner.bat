@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 goto use_py
where python >nul 2>nul
if not errorlevel 1 goto use_python
echo.
echo Python 3.10 or newer is required. Install it from https://www.python.org/downloads/
echo During setup, tick "Add python.exe to PATH".
goto done
:use_py
py -3 launch.py %*
goto done
:use_python
python launch.py %*
:done
echo.
pause
