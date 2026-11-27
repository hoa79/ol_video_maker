"""Villkoren som användaren godkänner innan programmet används första gången.

Texten finns bara här. Fönstret får den via /api/state, och docs/VILLKOR.md (som också
följer med Windows-paketet) skapas härifrån: python -m ol_video_maker.villkor > docs/VILLKOR.md Ändras texten i sak, öka VERSION så att alla får godkänna den nya texten.

Godkännandet sparas i en liten fil på den egna datorn:
    %LOCALAPPDATA%\\OL Video Maker\\godkannande.json   (Windows)
    ~/.cache/OL Video Maker/godkannande.json         (andra system)
Filen innehåller bara versionsnumret och datumet. Den skickas aldrig någonstans.
"""

from __future__ import annotations

import datetime
import json
import os
from typing import Any, Dict, Optional

# Juristens text (granskad 2026-11-27). Ändra inte orden utan att juristen granskar igen.
# **fet** och *kursiv* är den enda formatering som används. Varje punkt har en rubrik och
# block: ("p", stycke) eller ("ul", [punktlista]).
VERSION = 1
TITLE = "Innan du börjar"
INTRO = ("OL Video Maker är ett gratis program som gör en video av din headcam-film och din "
         "Livelox-inspelning. Läs de här villkoren och godkänn dem för att använda programmet.")
POINTS = [
    ("Du ansvarar för det du filmar.", [
        ("p", "Det är ditt ansvar att det du filmar är tillåtet. Oftast får man filma på allmän "
              "plats, men tänk särskilt på det här:"),
        ("ul", ["Filma inte i omklädningsrum, duschar, toaletter eller andra platser där "
                "människor har rätt att vara ifred. Det kan vara brottsligt.",
                "Filma inte där det finns skyltar om fotograferingsförbud, till exempel vid "
                "militära områden och andra skyddsobjekt.",
                "Var försiktig med privata tomter och gårdar."]),
    ]),
    ("Du ansvarar för personer som syns eller hörs i videon.", [
        ("p", "Om andra personer går att känna igen, till exempel på ansikte, nummerlapp eller "
              "röst, kan dataskyddsförordningen (GDPR) gälla när du lägger ut videon på "
              "internet. Fråga gärna dem som syns tydligt om det är okej, och ta bort eller "
              "klipp bort personer som inte vill vara med."),
    ]),
    ("Du ansvarar för att du får publicera allt i videon.", [
        ("p", "Det gäller till exempel kartan, musik och annat material som någon annan har "
              "gjort."),
        ("ul", ["Kartan ägs av tävlingsarrangören och visas via Livelox. **Ange Livelox som "
                "källa** när du publicerar videon, till exempel i videobeskrivningen: "
                "*\"Karta och vägval: Livelox\"*.",
                "Livelox tillåter delning när det inte är kommersiellt. Ska videon användas för "
                "att tjäna pengar, till exempel med reklamintäkter, behöver du först fråga "
                "Livelox.",
                "Lägg inte till musik som du inte har rätt att använda."]),
    ]),
    ("Du bestämmer över din puls och dina filer.", [
        ("p", "Programmet skickar inte dina filmer, din karta eller din puls någonstans. Allt "
              "sker på din dator. Om du lägger pulsen i videon och publicerar den är det ditt "
              "val att dela den."),
    ]),
    ("Utvecklarna ansvarar inte för vad som filmas eller hur videorna används.", [
        ("p", "Det är du som filmar, gör videon och bestämmer var den publiceras. Utvecklarna "
              "av OL Video Maker ansvarar inte för innehållet i videorna eller för hur de "
              "används."),
    ]),
    ("Programmet lämnas som det är, utan garantier.", [
        ("p", "Programmet är gratis och kan innehålla fel. Utvecklarna ansvarar inte för skador "
              "eller förluster som programmet orsakar, till exempel filer som går förlorade, så "
              "långt som lagen tillåter. Programmet ändrar inte dina originalfiler, men spara "
              "alltid en kopia. Den fullständiga licensen (MIT) finns i licensfilen som följer "
              "med programmet (LICENS.txt i programmets mapp)."),
    ]),
]
CHECKBOX = "Jag har läst villkoren och godkänner dem."
ACCEPT = "Godkänn och fortsätt"
DECLINE = "Avsluta"
FILE_NAME = "godkannande.json"


def data_dir() -> str:
    """Mappen där programmet sparar sina egna små filer (samma som fönstrets profil)."""
    override = os.environ.get("OLVM_DATA_DIR")  # för tester
    if override:
        return override
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "OL Video Maker")


def _path() -> str:
    return os.path.join(data_dir(), FILE_NAME)


def as_dict() -> Dict[str, Any]:
    """Texten till fönstret."""
    return {"version": VERSION, "title": TITLE, "intro": INTRO,
            "points": [{"title": t, "blocks": [{"type": k, "content": c} for k, c in blocks]}
                       for t, blocks in POINTS],
            "checkbox": CHECKBOX, "accept": ACCEPT, "decline": DECLINE}


def as_markdown() -> str:
    """Texten som Markdown: VILLKOR.md i repot och i Windows-paketet (test kontrollerar att
    filen i repot är lika med den här)."""
    lines = ["# Villkor för OL Video Maker", "",
             f"Villkorsversion {VERSION}. Programmet visar villkoren första gången det startas, "
             f"och igen om de ändras. Du godkänner dem genom att kryssa i "
             f"*{CHECKBOX}* och klicka på **{ACCEPT}**.", "",
             f"## {TITLE}", "", INTRO, ""]
    for i, (title, blocks) in enumerate(POINTS, 1):
        lines.append(f"{i}. **{title}**")
        for kind, content in blocks:
            if kind == "p":
                lines.append(f"    {content}")
            else:
                lines += [f"    - {item}" for item in content]
            lines.append("")
    return "\n".join(lines)


def accepted_version() -> Optional[int]:
    try:
        with open(_path(), encoding="utf-8") as f:
            v = json.load(f).get("version")
        return v if isinstance(v, int) else None
    except (OSError, ValueError, AttributeError):
        return None


def is_accepted() -> bool:
    """Har användaren godkänt den nuvarande texten (eller en senare)?"""
    v = accepted_version()
    return v is not None and v >= VERSION


def accept() -> None:
    """Spara att användaren godkänt. Går det inte att spara gäller godkännandet ändå
    tills programmet stängs (då visas villkoren igen nästa gång)."""
    data = {"version": VERSION, "datum": datetime.date.today().isoformat()}
    try:
        os.makedirs(data_dir(), exist_ok=True)
        tmp = _path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, _path())
    except OSError:
        pass


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    sys.stdout.write(as_markdown())
