@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"

echo Starting Dependency Graph: bash (index downloaded from the internet)...
py -3 --version
if errorlevel 1 goto error

echo.
echo Building dependency graph and calculating installation order...
echo.

py -3 -u src\dependency_graph.py --package bash --repository https://archive.ubuntu.com/ubuntu/dists/noble/main/binary-amd64 --installation-order --ascii-tree --image-file "%~dp0dependency_graph_bash.png"
set EXITCODE=%ERRORLEVEL%

if exist "%~dp0dependency_graph_bash.png" (
    echo PNG successfully created!
) else (
    echo ERROR: PNG file was not created!
)

echo.
echo Program finished with code %EXITCODE%
goto end

:error
echo Python 3 was not found. Check Python installation.

:end
pause
