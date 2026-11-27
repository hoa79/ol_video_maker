"""Startar OL Video Maker i den portabla Windows-versionen.

Den portabla versionen använder Pythons egen, signerade pythonw.exe (omdöpt till
"OL Video Maker.exe"), så att Windows Smart appkontroll släpper igenom den. pythonw.exe
startar inget program av sig själv. Den här filen läggs därför i paketet som
app/sitecustomize.py, som Python importerar automatiskt när det startar.

Fönstret öppnas vid dubbelklick, och även om man släpper en fil på exe-filen. Med -c
eller -m (används av byggets självtest) eller miljövariabeln OLVM_NO_START startas det inte.

Obs: programmet körs medan den här modulen importeras (under Pythons site-import).
Det fungerar, men fel under starten skrivs till ol_video_maker_fel.txt i temp-mappen
via ol_video_maker.__main__, och os._exit gör att Python avslutas direkt efteråt.
"""
import os
import sys

_args = list(getattr(sys, "orig_argv", ()))[1:]
if not ({"-c", "-m"} & set(_args)) and not os.environ.get("OLVM_NO_START"):
    sys.olvm_portable = True
    sys.argv = [sys.executable]  # en släppt fil ska inte tolkas som kommandoradsläge
    try:
        from ol_video_maker.__main__ import main
        main()
    finally:
        # Avsluta direkt efter att fönstret stängts (annars försöker Python köra den
        # släppta filen som ett skript, eller vänta på interaktiv indata).
        os._exit(0)
