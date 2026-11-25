@echo off
chcp 65001 >nul
cd /d %~dp0
C:\xiaoer\venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name 小二 ^
  --add-data "assets\wooden_fish.png;assets" ^
  --collect-submodules winsdk ^
  --hidden-import winsdk.windows.media.control ^
  --hidden-import uiautomation ^
  --hidden-import comtypes ^
  --hidden-import psutil ^
  --exclude-module tkinter ^
  --exclude-module PySide6.QtWebEngineCore ^
  --exclude-module PySide6.QtWebEngineWidgets ^
  --exclude-module PySide6.Qt3DCore ^
  --exclude-module PySide6.QtCharts ^
  --exclude-module PySide6.QtDataVisualization ^
  --exclude-module PySide6.QtQuick ^
  --exclude-module PySide6.QtQuick3D ^
  --exclude-module PySide6.QtQml ^
  main.py
echo.
echo 打包完成：dist\小二.exe
