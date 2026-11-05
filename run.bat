@echo off
cd /d "%~dp0"

echo Starting Dependency Graph...
py -3 --version
if errorlevel 1 goto error

if not exist "Packages" (
    echo Error: File Packages was not found!
    echo Place Packages in the project root directory.
    goto end
)

echo.
echo Building dependency graph and calculating installation order...
echo.

py -3 src\dependency_graph.py --package bash --repository https://archive.ubuntu.com/ubuntu/dists/noble/main/binary-amd64 --installation-order

echo.
echo Program finished with code %ERRORLEVEL%
goto end

:error
echo Python 3 was not found. Check Python installation.

:end
pause
