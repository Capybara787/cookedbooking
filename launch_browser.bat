@echo off
REM Starts Chrome (or Edge) with a "debugging port" open, listening on THIS PC only,
REM using its own profile folder so your normal browsing is untouched.
REM Log in to the CDC portal in the window that opens (solve the reCAPTCHA yourself),
REM then leave the window open. Minimising it is fine.

set "PROFILE=%USERPROFILE%\cdc_bot_profile"
set "BROWSER="

if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "BROWSER=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not defined BROWSER if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "BROWSER=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not defined BROWSER if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" set "BROWSER=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if not defined BROWSER if exist "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" set "BROWSER=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"

if not defined BROWSER (
    echo Could not find Chrome or Edge. Edit this file and set BROWSER to your browser's .exe path.
    pause
    exit /b 1
)

echo Starting: %BROWSER%
start "" "%BROWSER%" --remote-debugging-port=9222 --user-data-dir="%PROFILE%" https://bookingportal.cdc.com.sg/
