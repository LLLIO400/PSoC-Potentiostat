@echo off
title PSoC-Potentiostat-YI 安装运行环境
cd /d "%~dp0"

echo ==========================================
echo   PSoC-Potentiostat-YI-1.0 上位机
echo   安装运行环境（只需在首次使用/换电脑时运行）
echo ==========================================
echo.

REM ---------- 检查 Python ----------
where python >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python。
    echo 请先安装 Python 3.10 或更高版本，安装时务必勾选 "Add Python to PATH"。
    echo 下载地址: https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)
echo [1/2] Python 已找到:
python --version
echo.

REM ---------- 安装依赖 ----------
echo [2/2] 正在安装依赖库，可能需要几分钟...
echo.
python -m pip install --upgrade pip
if errorlevel 1 goto pip_fail

REM 运行必需: pyserial(串口通信) + matplotlib(图表) + ttkbootstrap(界面主题)
python -m pip install pyserial matplotlib ttkbootstrap
if errorlevel 1 goto pip_fail

REM 算法功能需要: scipy(滤波/寻峰)
python -m pip install scipy
if errorlevel 1 goto pip_fail
echo.
echo ==========================================
echo   环境安装完成，以后直接双击 start_gui.bat 即可
echo ==========================================
pause
exit /b 0

:pip_fail
echo.
echo [错误] 依赖安装失败，请检查网络后重试。
pause
exit /b 1
