@echo off
cd /d "%~dp0"
echo Starting Dependency Graph...
py -3 --version
if errorlevel 1 goto error
py -3 src\dependency_graph.py --package demo --repository https://example.com
echo.
echo Program finished with code %ERRORLEVEL%
goto end
:error
echo Python 3 was not found. Check Python installation.
:end
pause
