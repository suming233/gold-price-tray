@echo off
chcp 65001 >nul
REM ============================================================
REM  打包金价托盘为可移植单文件 exe
REM  产物：dist\GoldPriceTray.exe（约 19MB）
REM  直接运行 = 托盘程序；在未安装的机器上双击 = 启动安装向导
REM ============================================================

python -m PyInstaller --noconfirm --onefile --windowed --icon app.ico --hidden-import chart --name GoldPriceTray gold_price_tray.py

if errorlevel 1 (
    echo.
    echo [打包失败] 请确认已安装依赖：pip install -r requirements.txt
    pause
    exit /b 1
)

echo.
echo [完成] 产物已生成：dist\GoldPriceTray.exe
pause
