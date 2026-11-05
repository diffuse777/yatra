@echo off
cd /d "%~dp0"
title Yatra Saarthi - GPS auto bridge
python tools\start_yatra_gps_auto.py %*
if errorlevel 1 pause
