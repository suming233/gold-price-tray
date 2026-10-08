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

REM build\ 是 PyInstaller 的中间缓存（约 22MB），__pycache__\ 是字节码缓存，
REM 两者都能自动重建，没必要留在工程里占地方 —— 打完包顺手清掉。
if exist build rmdir /S /Q build
if exist __pycache__ rmdir /S /Q __pycache__

echo.
echo [完成] 产物已生成：dist\GoldPriceTray.exe
echo [已清理] build\ 与 __pycache__\ 中间产物
pause
