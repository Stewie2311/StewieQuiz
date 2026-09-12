@echo off
title Chia se app cho ban be - Cloudflare Tunnel
echo ===============================================================
echo   DANG TAO LINK CHIA SE... vui long cho vai giay.
echo ---------------------------------------------------------------
echo   * Truoc khi chay file nay, hay chac chan app Flask DANG CHAY
echo     (cong 5000).
echo   * Link se hien trong KHUNG o ben duoi, dang:
echo         https://xxxx-yyyy.trycloudflare.com
echo   * GUI link do cho ban be.
echo   * GIU CUA SO NAY MO trong suot luc chia se.
echo     Dong cua so = ngat link.
echo ===============================================================
echo.
"C:\Program Files (x86)\cloudflared\cloudflared.exe" tunnel --url http://localhost:5000
echo.
echo Tunnel da dung. Bam phim bat ky de dong.
pause >nul
