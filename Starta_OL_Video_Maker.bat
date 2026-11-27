@echo off
rem Dubbelklicka på den här filen för att starta OL Video Maker.
cd /d "%~dp0"
where pyw >nul 2>nul && (start "" pyw -m ol_video_maker & exit /b)
where pythonw >nul 2>nul && (start "" pythonw -m ol_video_maker & exit /b)
echo Python hittades inte. Installera Python enligt guiden (docs\GUIDE.md), steg 1.
pause
