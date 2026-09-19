@echo off
echo ==========================================
echo Building Laser Engraver Desktop App
echo ==========================================

pip install -r requirements.txt

pyinstaller ^
  --onefile ^
  --windowed ^
  --name LaserEngraver ^
  desktop_app.py

echo.
echo Build complete!
echo Your app is inside:
echo dist\LaserEngraver.exe
pause