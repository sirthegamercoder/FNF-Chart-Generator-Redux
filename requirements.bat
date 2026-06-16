@echo off
cd /d "%~dp0"
echo The process has started...

if not exist ".venv\Scripts\activate.bat" (
    echo Create virtual environment
    python -m venv .venv --upgrade-deps
    echo Virtual environment has created!
)

echo.
echo Activate virtual environment
call .venv\Scripts\activate.bat
echo Install requirements
pip install -r requirements.txt
echo.
echo Requirements has installed!
exit /b