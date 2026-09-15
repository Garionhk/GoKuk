@echo off
REM ===================================================================
REM  Gokuk Setup - run from source (developers). Users run "Gokuk Setup.exe".
REM ===================================================================
setlocal EnableExtensions
cd /d "%~dp0"
title Gokuk Setup

set "PY="
where py >nul 2>nul && set "PY=py"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY goto :no_python
set "PYW=pyw"
where pyw >nul 2>nul || set "PYW=%PY%"

%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 goto :no_python

%PY% -c "import PySide6, requests" >nul 2>nul
if errorlevel 1 (
    echo Installing the window libraries once...
    %PY% -m pip install -r "%~dp0requirements.txt" || goto :failed
)

start "Gokuk Setup" %PYW% "%~dp0GokukSetup.py"
exit /b 0

:no_python
echo Python 3.10 or newer is needed to run Gokuk Setup from source.
echo Most people should use "Gokuk Setup.exe" from the release zip instead.
pause
exit /b 1

:failed
echo Could not install the libraries. Try:  %PY% -m pip install -r requirements.txt
pause
exit /b 1
