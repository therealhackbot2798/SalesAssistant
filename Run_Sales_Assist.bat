@echo off
echo Starting Sales Assist Co-Pilot...
call .\.venv\Scripts\activate
start pythonw -m sales_assist.gui
exit
