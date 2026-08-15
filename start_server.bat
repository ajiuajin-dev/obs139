@echo off
rem 启动 obs139 本地图片代理（供直链渲染）
rem 双击运行；Obsidian 插件加载时也会自动启动
cd /d "%~dp0"
python 139img.py
pause
