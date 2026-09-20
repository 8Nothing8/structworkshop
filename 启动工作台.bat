@echo off
rem structworkshop 结构工坊 · 启动本地工作台（Windows）
rem 首次运行：建 .venv → 装依赖 → 把本项目注册进这个 .venv → 起服务
rem 不碰系统 Python：所有东西都在本文件夹的 .venv 里，删掉文件夹 = 彻底卸载
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ==========================================
echo   structworkshop 结构工坊 - 本地工作台
echo ==========================================
echo.

rem ---- 1) 找一个 Python 3.10+（py 启动器优先，其次 python）----
set "LAUNCH=py -3"
%LAUNCH% -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul
if errorlevel 1 set "LAUNCH=python"
%LAUNCH% -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul
if errorlevel 1 (
  echo [x] 没找到 Python 3.10 以上版本。
  echo     装一个：https://www.python.org/downloads/   （推荐 3.12 / 3.13）
  echo     安装时务必勾选 "Add python.exe to PATH"，装完重新双击本脚本。
  echo.
  pause
  exit /b 1
)

rem ---- 2) 本文件夹内的虚拟环境（不污染系统 Python）----
set "VENV=%~dp0.venv"
set "VPY=%VENV%\Scripts\python.exe"
if not exist "%VPY%" (
  echo [1/3] 建虚拟环境 .venv（第一次要几十秒）...
  %LAUNCH% -m venv "%VENV%"
  if not exist "%VPY%" (
    echo [x] 建虚拟环境失败：请确认本文件夹解压在普通目录
    echo     （别放 C:\Program Files，也别放 OneDrive 同步夹）。
    echo.
    pause
    exit /b 1
  )
) else (
  echo [1/3] 虚拟环境已就绪。
)

rem ---- 3) 依赖 + 把本项目注册进 .venv（只写 .venv 内部）----
"%VPY%" -c "import numpy, numba, PIL, nbt" >nul 2>nul
if errorlevel 1 (
  if exist "%~dp0wheels" (
    echo [2/3] 从本地 wheels 目录离线安装依赖...
    "%VPY%" -m pip install --no-index --find-links "%~dp0wheels" -e ".[runtime]"
  ) else (
    echo [2/3] 安装依赖 numpy / numba / pillow / nbt（第一次要下载几十 MB）...
    "%VPY%" -m pip install --upgrade pip >nul 2>nul
    "%VPY%" -m pip install -e ".[runtime]"
  )
  if errorlevel 1 (
    echo [x] 依赖安装失败，请检查网络。
    echo     不能上网的机器：让发送方用  python tools\make_release.py --wheels  打离线包。
    echo.
    pause
    exit /b 1
  )
) else (
  echo [2/3] 依赖已就绪。
)

rem 文件夹被挪动过（.venv 里的注册路径失效）时就地补一次注册，只补注册不下载
"%VPY%" -c "import mccore" >nul 2>nul
if errorlevel 1 (
  echo [3/3] 修复本项目在 .venv 里的注册（不下载依赖）...
  "%VPY%" -m pip install -e . --no-deps >nul 2>nul
)

rem PYTHONPATH 兜底：包被挪动/注册失效也照样能跑，并保证引擎来自**本文件夹**
set "PYTHONPATH=%~dp0packages"

rem ---- 自检：确认引擎是本文件夹这一份（不是别处 pip install -e . 绑定的那一份）----
rem 注意：%~dp0 末尾带反斜杠，直接放进引号会被 C 运行时当成「转义引号」，所以要切掉
set "HERE=%~dp0"
"%VPY%" tools\which_copy.py --expect "%HERE:~0,-1%"
if errorlevel 1 (
  echo.
  echo [x] 自检没过：上面「引擎」那行指向的不是本文件夹。
  echo     A. 删掉本文件夹里的 .venv，再双击一次本脚本
  echo     B. 说明见 INSTALL.md 的「装了好几份，怎么启动确定的那一份」
  echo.
  pause
  exit /b 1
)

rem ---- 4) 起服务 ----
if not defined STRUCTWORKSHOP_NO_OPEN set "OPENFLAG=--open"
echo [3/3] 启动工作台：浏览器会自动打开（端口被占会自动换，看下面打印的地址）
echo       关掉这个黑窗口 = 退出工作台
echo.
"%VPY%" -m mcstudio serve %OPENFLAG% %*
echo.
echo 工作台已退出。
pause
