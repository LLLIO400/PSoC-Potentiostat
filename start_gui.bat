@echo off
title PSoC-Potentiostat-YI 启动
cd /d "%~dp0"

REM ---------- 检查 Python ----------
where python >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python，请先运行 install_env.bat
    echo.
    pause
    exit /b 1
)

REM ---------- 检查依赖 ----------
python -c "import serial, matplotlib, ttkbootstrap, scipy" >nul 2>&1
if errorlevel 1 (
    echo [错误] 依赖缺失，请先运行 install_env.bat
    echo.
    pause
    exit /b 1
)

echo 正在启动 GUI，请稍候（本窗口会在软件界面出现后自动关闭）...
if exist _gui_ready.flag del _gui_ready.flag
REM 优先用 pythonw 启动（后台无黑窗口），找不到则退回 python
where pythonw >nul 2>&1
if errorlevel 1 (
    start "" python main.py
) else (
    start "" pythonw main.py
)
REM 等待 GUI 主窗口就绪（main.py 窗口显示后写 _gui_ready.flag），最多等 60 秒
set /a _n=0
:wait_gui
timeout /t 1 >nul
if exist _gui_ready.flag goto gui_up
set /a _n+=1
if %_n% lss 60 goto wait_gui
echo [提示] 等待超时。若 GUI 未出现，请查看 startup_error.txt
pause
exit /b 1
:gui_up
del _gui_ready.flag
exit /b 0
