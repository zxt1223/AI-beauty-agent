@echo off
chcp 65001 >nul
title beauty_agent AI 导购
cd /d "%~dp0"
echo.
echo  ========================================================
echo    🛍️  beauty_agent AI 粉底导购  正在启动...
echo  ========================================================
echo    服务起来后会自动打开浏览器 http://127.0.0.1:7860
echo    停止：关掉本窗口，或在窗口里按 Ctrl+C
echo.
rem 找一个带 pandas 的 python 来跑（web_server -^> agent -^> pandas/numpy）。
rem 依次尝试：PATH 里的 python（conda 已激活时通常是它）-^> tradingagents
rem 环境 -^> Anaconda 基础环境。WindowsApps 那个 0 字节占位 stub 没 pandas，
rem 下面的 import 检查会把它踢掉（这就是为什么不能直接用 py / 裸 python）。
set "PYEXE="
where python >nul 2>&1 && python -c "import pandas" >nul 2>&1 && set "PYEXE=python"
if not defined PYEXE if exist "C:\Users\Lenovo\.conda\envs\tradingagents\python.exe" set "PYEXE=C:\Users\Lenovo\.conda\envs\tradingagents\python.exe"
if not defined PYEXE if exist "%USERPROFILE%\.conda\envs\tradingagents\python.exe" set "PYEXE=%USERPROFILE%\.conda\envs\tradingagents\python.exe"
if not defined PYEXE if exist "C:\ProgramData\Anaconda3\python.exe" set "PYEXE=C:\ProgramData\Anaconda3\python.exe"
if not defined PYEXE (
    echo  [错误] 找不到带 pandas 的 python。
    echo         需要 conda tradingagents 或 Anaconda base（pandas + requests + PyMySQL + SQLAlchemy）。
    pause
    exit /b 1
)
echo  使用 python: %PYEXE%
%PYEXE% scripts\web_server.py
echo.
echo  服务已退出。按任意键关闭窗口...
pause >nul
