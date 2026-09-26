@echo off
rem Run from the project folder so Streamlit picks up .streamlit\config.toml and secrets.toml.
cd /d "%~dp0"
"%USERPROFILE%\anaconda3\envs\streamlitenv\python.exe" -m streamlit run app.py
if errorlevel 1 pause
