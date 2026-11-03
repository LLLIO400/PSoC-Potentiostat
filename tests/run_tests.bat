@echo off
title PSoC-Potentiostat-YI 测试与诊断
cd /d "%~dp0"

echo ==========================================
echo   PSoC-Potentiostat-YI-1.0 测试与诊断
echo   环境诊断 + 全量测试，输出 test_report.txt
echo ==========================================
echo.

REM ---------- 检查 Python ----------
where python >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python，请先运行 install_env.bat
    echo.
    pause
    exit /b 1
)

REM ---------- 检查依赖 ----------
python -c "import serial, matplotlib, ttkbootstrap" >nul 2>&1
if errorlevel 1 (
    echo [提示] 缺少依赖，正在安装 pyserial matplotlib ttkbootstrap ...
    python -m pip install pyserial matplotlib ttkbootstrap
    if errorlevel 1 (
        echo [错误] 依赖安装失败，请检查网络
        pause
        exit /b 1
    )
)

REM ---------- 询问是否跑真机实测 ----------
set "HW="
set /p "HWOPT=插着 PSoC 设备吗？输入 Y 加跑硬件实测，直接回车跳过: "
if /i "%HWOPT%"=="Y" set "HW=--hardware"

REM ---------- 运行测试与诊断 ----------
python run_tests.py %HW%
if errorlevel 1 (
    echo.
    echo ==========================================
    echo   存在问题，请把 test_report.txt 发给开发者
    echo ==========================================
    pause
    exit /b 1
)

echo.
echo ==========================================
echo   全部通过，详见 test_report.txt
echo ==========================================
pause
