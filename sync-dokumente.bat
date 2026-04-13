@echo off
echo === HupfGaudi Dokumente synchronisieren ===

set SRC=C:\Users\Gutse\Projekte\Hupfgaudi
set DEST=C:\Users\Gutse\OneDrive\Desktop\HupfGaudi-Dokumente

:: Mietverträge (nur aktuelle HTML-Versionen)
copy /Y "%SRC%\mietvertrag-huepfburg.html" "%DEST%\Mietverträge\" >nul
copy /Y "%SRC%\mietvertrag-partyzubehoer.html" "%DEST%\Mietverträge\" >nul

:: Preise & Produkte
copy /Y "%SRC%\products.json" "%DEST%\Preise & Produkte\" >nul
copy /Y "%SRC%\prices.json" "%DEST%\Preise & Produkte\" >nul
copy /Y "%SRC%\settings.json" "%DEST%\Preise & Produkte\" >nul

:: Kundendaten
copy /Y "%SRC%\anfragen.json" "%DEST%\Kundendaten\" >nul
copy /Y "%SRC%\contracts.json" "%DEST%\Kundendaten\" >nul
copy /Y "%SRC%\confirmed_bookings.json" "%DEST%\Kundendaten\" >nul

:: Website
copy /Y "%SRC%\index.html" "%DEST%\Website\" >nul
copy /Y "%SRC%\style.css" "%DEST%\Website\" >nul
copy /Y "%SRC%\impressum.html" "%DEST%\Website\" >nul
copy /Y "%SRC%\slideshow.json" "%DEST%\Website\" >nul

:: Einstellungen
copy /Y "%SRC%\api_proxy.py" "%DEST%\Einstellungen\" >nul
copy /Y "%SRC%\.env" "%DEST%\Einstellungen\" >nul

echo Synchronisierung abgeschlossen!
