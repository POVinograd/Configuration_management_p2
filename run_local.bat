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

py -3 -u src\dependency_graph.py --package demo --repository "%~dp0Packages" --test-repository --installation-order --ascii-tree --image-file "%~dp0dependency_graph_demo.png"

echo.
echo Program finished with code %ERRORLEVEL%
goto end

:error
echo Python 3 was not found. Check Python installation.

:end
pause

