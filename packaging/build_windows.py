"""Bygg Windows-paketet: en mapp (och zip) som går att köra utan att installera något.

Körs av GitHub Actions på en Windows-dator (se .github/workflows/build-windows.yml),
men går också att köra för hand på Windows:

    python packaging/build_windows.py --version 0.2.0

Standard är den portabla versionen med Pythons egna signerade filer (fungerar med
Smart appkontroll i Windows 11). --mode pyinstaller bygger en osignerad exe som förut.

Resultat:
    build_win/paket/OL Video Maker/        <- mappen som delas ut
        OL Video Maker.exe                 (Pythons signerade pythonw.exe med nytt namn)
        *.dll, DLLs/, Lib/, tcl/           (Python och Tcl/Tk från python.org)
        app/ol_video_maker/, app/sitecustomize.py (programmets kod och startfil)
        python312._pth                     (Pythons sökvägar)
        ffmpeg/ffmpeg.exe, ffprobe.exe, LICENSE.txt, README.txt, KALLKOD.txt
        licenser/Python_LICENSE.txt, Tcl_license.txt, Tk_license.txt
        Guide.html, Installationsguide.html, Villkor.html, Las_mig_forst.txt, LICENS.txt,
        TREDJEPART.txt
    build_win/OL_Video_Maker_<version>_windows.zip
    build_win/ffmpeg-7.1.tar.xz            <- ffmpegs källkod, delas ut bredvid zip-filen
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.join(ROOT, "packaging")
BUILD = os.path.join(ROOT, "build_win")
APP_NAME = "OL Video Maker"

# ffmpeg-build från gyan.dev (samma som "winget install Gyan.FFmpeg").
# Fast version och kontrollsumma, så att vi alltid vet exakt vad som skickas med.
FFMPEG_VERSION = "7.1"
FFMPEG_URL = ("https://github.com/GyanD/codexffmpeg/releases/download/"
              f"{FFMPEG_VERSION}/ffmpeg-{FFMPEG_VERSION}-essentials_build.zip")
FFMPEG_SHA256 = "fa7d4d7e795db0e2503f49f105f46ed5852386f0cfdd819899be3b65ebde24fc"
# Källkoden till exakt samma version (gyan-builden är gjord från taggen n7.1, commit b08d7969c5).
# GPL v3 kräver att vi själva tillhandahåller den bredvid binären (läggs i GitHub Release).
FFMPEG_SOURCE_URL = f"https://ffmpeg.org/releases/ffmpeg-{FFMPEG_VERSION}.tar.xz"
FFMPEG_SOURCE_SHA256 = "40973d44970dbc83ef302b0609f2e74982be2d85916dd2ee7472d30678a7abe6"
FFMPEG_SOURCE_NAME = f"ffmpeg-{FFMPEG_VERSION}.tar.xz"
RELEASES_URL = "https://github.com/hoa79/ol_video_maker/releases"
ISSUES_URL = "https://github.com/hoa79/ol_video_maker/issues"


# Delar av standardbiblioteket som programmet inte behöver (gör paketet mindre).
STDLIB_SKIP = {"test", "idlelib", "ensurepip", "lib2to3", "turtledemo", "site-packages",
               "venv", "pydoc_data", "__pycache__"}
# nmake och tix behövs inte och innehåller osignerade filer.
TCL_SKIP = {"demos", "images", "nmake"}


def portable_python(dist: str) -> str:
    """Kopiera den signerade Python-installationen (python.org) och lägg vår kod bredvid.

    Smart appkontroll i Windows 11 spärrar osignerade program, till exempel en
    PyInstaller-exe. Pythons egna filer är signerade av Python Software Foundation och
    släpps igenom. Startfilen är pythonw.exe med nytt namn (signaturen gäller fortfarande),
    och vår kod är vanliga .py-filer som Smart appkontroll inte kontrollerar.
    """
    if os.name != "nt":
        sys.exit("Den portabla versionen kan bara byggas på Windows.")
    base = sys.base_prefix
    app_dir = os.path.join(dist, APP_NAME)
    os.makedirs(app_dir)
    # Programfilerna i roten
    shutil.copy2(os.path.join(base, "pythonw.exe"), os.path.join(app_dir, APP_NAME + ".exe"))
    for name in os.listdir(base):
        if name.lower().endswith(".dll"):
            shutil.copy2(os.path.join(base, name), os.path.join(app_dir, name))
    shutil.copytree(os.path.join(base, "DLLs"), os.path.join(app_dir, "DLLs"),
                    ignore=shutil.ignore_patterns("*.pdb", "_test*", "*_d.pyd"))
    # Standardbiblioteket (Lib/os.py gör att Python hittar sin egen mapp)
    lib = os.path.join(app_dir, "Lib")
    shutil.copytree(os.path.join(base, "Lib"), lib,
                    ignore=lambda d, names: [n for n in names if n in STDLIB_SKIP
                                             or n.endswith(".pyc")])
    shutil.copytree(os.path.join(base, "tcl"), os.path.join(app_dir, "tcl"),
                    ignore=lambda d, names: [n for n in names if n in TCL_SKIP
                                             or n.lower().startswith("tix")])
    # Vår kod och startfilen
    shutil.copytree(os.path.join(ROOT, "ol_video_maker"),
                    os.path.join(app_dir, "app", "ol_video_maker"),
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    # Startfilen: Python importerar sitecustomize automatiskt när det startar.
    shutil.copy(os.path.join(HERE, "olvm_start.py"),
                os.path.join(app_dir, "app", "sitecustomize.py"))
    # python312._pth: Python använder bara sökvägarna här och ignorerar PYTHONHOME,
    # PYTHONPATH, registret och användarens egna paket (en annan Python-installation på
    # datorn kan alltså inte störa). "import site" behövs för att sitecustomize ska köras.
    dll = f"python{sys.version_info.major}{sys.version_info.minor}"
    with open(os.path.join(app_dir, dll + "._pth"), "w", encoding="utf-8") as f:
        f.write("Lib\nDLLs\napp\nimport site\n")
    # Förkompilera, så att första starten går fort (och mappen kan vara skrivskyddad).
    run([sys.executable, "-m", "compileall", "-q", "-j", "0", lib,
         os.path.join(app_dir, "app")])
    return app_dir


def run(cmd):
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def pyinstaller(dist: str) -> str:
    work = os.path.join(BUILD, "work")
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
         "--name", APP_NAME, "--distpath", dist, "--workpath", work, "--specpath", work,
         "--paths", ROOT, "--add-data",
         os.path.join(ROOT, "ol_video_maker", "web") + os.pathsep + os.path.join("ol_video_maker", "web"),
         os.path.join(HERE, "launcher.py")])
    return os.path.join(dist, APP_NAME)


def download(url: str, path: str, sha256: str) -> None:
    """Ladda ner (om det behövs) och kontrollera kontrollsumman."""
    if not os.path.isfile(path):
        print("Laddar ner", url, flush=True)
        urllib.request.urlretrieve(url, path)
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    if not sha256:
        print(f"OBS: ingen fast kontrollsumma för {os.path.basename(path)}. "
              f"SHA-256 = {h.hexdigest()}", flush=True)
        if os.environ.get("REQUIRE_PINNED") == "1":
            sys.exit("Kontrollsumman måste vara fastlåst för en release.")
        return
    if h.hexdigest() != sha256:
        os.remove(path)
        sys.exit(f"Fel kontrollsumma för {os.path.basename(path)}: {h.hexdigest()}")


def fetch_ffmpeg_source() -> str:
    path = os.path.join(BUILD, FFMPEG_SOURCE_NAME)
    download(FFMPEG_SOURCE_URL, path, FFMPEG_SOURCE_SHA256)
    return path


def fetch_ffmpeg(app_dir: str) -> None:
    cache = os.path.join(BUILD, f"ffmpeg-{FFMPEG_VERSION}.zip")
    download(FFMPEG_URL, cache, FFMPEG_SHA256)

    target = os.path.join(app_dir, "ffmpeg")
    os.makedirs(target, exist_ok=True)
    wanted = {"bin/ffmpeg.exe": "ffmpeg.exe", "bin/ffprobe.exe": "ffprobe.exe",
              "LICENSE": "LICENSE.txt", "README.txt": "README.txt"}
    with zipfile.ZipFile(cache) as z:
        for name in z.namelist():
            rel = name.split("/", 1)[1] if "/" in name else name
            if rel in wanted:
                with z.open(name) as src, open(os.path.join(target, wanted[rel]), "wb") as dst:
                    shutil.copyfileobj(src, dst)
    missing = [v for v in wanted.values() if not os.path.isfile(os.path.join(target, v))]
    if missing:
        sys.exit(f"Saknas i ffmpeg-zip: {missing}")
    with open(os.path.join(target, "KALLKOD.txt"), "w", encoding="utf-8") as f:
        f.write(
            f"ffmpeg {FFMPEG_VERSION} (essentials-build från www.gyan.dev)\n\n"
            "ffmpeg är fri programvara under GNU General Public License version 3 (se LICENSE.txt).\n\n"
            "Källkoden\n"
            "---------\n"
            f"En kopia av källkoden till ffmpeg ({FFMPEG_SOURCE_NAME}, taggen n{FFMPEG_VERSION}, "
            "commit b08d7969c5) delas ut tillsammans med den här zip-filen,\n"
            f"i samma GitHub Release: {RELEASES_URL}\n"
            "Har du fått zip-filen av någon annan ska du också ha fått källkodsfilen. "
            "Fråga annars den som gav dig programmet.\n\n"
            "Builden innehåller även andra fria bibliotek (till exempel x264 och x265). "
            "Vilka och vilka versioner står i README.txt.\n"
            "Källkoden till dem skickas på begäran, utan kostnad, i minst tre år efter att den här "
            f"versionen delades ut: öppna en issue på {ISSUES_URL}\n"
            "eller kontakta den som gav dig programmet.\n\n"
            f"Den ursprungliga binären kommer från: {FFMPEG_URL}\n"
            "OL Video Maker anropar ffmpeg.exe och ffprobe.exe som fristående program.\n"
        )


# Dokumenten i docs/ och vad de heter i paketet.
PAGES = {"GUIDE.md": "Guide.html", "INSTALLERA.md": "Installationsguide.html",
         "VILLKOR.md": "Villkor.html"}


def guide_html(md_path: str, out_path: str, title: str = "guide") -> None:
    import markdown  # endast vid bygget (BSD-licens)
    with open(md_path, encoding="utf-8") as f:
        text = f.read()
    # Länkar mellan guiderna pekar på .html-filerna i paketet.
    for md_name, html_name in PAGES.items():
        text = text.replace(f"]({md_name})", f"]({html_name})")
    body = markdown.markdown(text, extensions=["fenced_code", "tables"])
    html = f"""<!doctype html><html lang="sv"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OL Video Maker – {title}</title>
<style>
body{{font-family:"Segoe UI",Arial,sans-serif;max-width:820px;margin:24px auto;padding:0 16px;
line-height:1.55;color:#1d1d1d;background:#fff}}
h1,h2,h3{{color:#1f5f3f}} code,pre{{background:#f2f4f2;border-radius:4px}}
pre{{padding:8px 12px;overflow:auto}} code{{padding:1px 4px}}
blockquote{{border-left:4px solid #ffcf3f;margin:12px 0;padding:4px 12px;background:#fffbea}}
img{{max-width:100%}}
</style></head><body>{body}</body></html>"""
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)


def third_party_licenses(app_dir: str) -> None:
    """Licenstexterna för Python (inkl. OpenSSL, zlib m.fl.) och Tcl/Tk följer med paketet."""
    target = os.path.join(app_dir, "licenser")
    os.makedirs(target, exist_ok=True)
    ver = f"python{sys.version_info.major}.{sys.version_info.minor}"
    found = {}
    for base in {sys.base_prefix, sys.prefix}:
        for cand in (os.path.join(base, "LICENSE.txt"), os.path.join(base, "lib", ver, "LICENSE.txt"),
                     os.path.join(base, "Lib", "LICENSE.txt")):
            if os.path.isfile(cand):
                found.setdefault("Python_LICENSE.txt", cand)
        # Kortaste sökvägen först: tk8.6/license.terms före tk8.6/demos/license.terms.
        terms = sorted(glob.glob(os.path.join(base, "tcl", "**", "license.terms"), recursive=True),
                       key=lambda p: (p.count(os.sep), p))
        print("Hittade licensfiler för Tcl/Tk:", terms, flush=True)
        for cand in terms:
            parts = [x.lower() for x in os.path.normpath(cand).split(os.sep)]
            if any(x.startswith("tk8") for x in parts):
                found.setdefault("Tk_license.txt", cand)
            elif any(x.startswith("tcl8") or x.startswith("tcl9") for x in parts):
                found.setdefault("Tcl_license.txt", cand)
    # Python-installationen på Windows har inte alltid Tcls egen licensfil. Då används en kopia
    # av samma fil från Tcl 8.6-källkoden (github.com/tcltk/tcl, license.terms).
    found.setdefault("Tcl_license.txt", os.path.join(HERE, "licenser", "Tcl_license.terms"))
    missing = {"Python_LICENSE.txt", "Tcl_license.txt", "Tk_license.txt"} - set(found)
    if missing:
        msg = "Hittar inte licenstexterna: " + ", ".join(sorted(missing))
        if os.name == "nt":
            sys.exit(msg)
        print("OBS (inte Windows):", msg, flush=True)
    for out, src in found.items():
        shutil.copy(src, os.path.join(target, out))


def extras(app_dir: str, version: str) -> None:
    third_party_licenses(app_dir)
    shutil.copy(os.path.join(ROOT, "LICENSE"), os.path.join(app_dir, "LICENS.txt"))
    shutil.copy(os.path.join(HERE, "TREDJEPART.txt"), os.path.join(app_dir, "TREDJEPART.txt"))
    with open(os.path.join(HERE, "Las_mig_forst.txt"), encoding="utf-8") as f:
        text = f.read().replace("{version}", version)
    # Windows Anteckningar visar UTF-8 med BOM rätt även i äldre versioner.
    with open(os.path.join(app_dir, "Las_mig_forst.txt"), "w", encoding="utf-8-sig",
              newline="\r\n") as f:
        f.write(text)
    guide_html(os.path.join(ROOT, "docs", "GUIDE.md"), os.path.join(app_dir, "Guide.html"))
    guide_html(os.path.join(ROOT, "docs", "INSTALLERA.md"),
               os.path.join(app_dir, "Installationsguide.html"), "installationsguide")
    guide_html(os.path.join(ROOT, "docs", "VILLKOR.md"), os.path.join(app_dir, "Villkor.html"),
               "villkor")
    # Bilder som guiden länkar till
    for name in os.listdir(os.path.join(ROOT, "docs")):
        if name.lower().endswith((".png", ".jpg", ".gif")):
            os.makedirs(os.path.join(app_dir, "docs"), exist_ok=True)
            shutil.copy(os.path.join(ROOT, "docs", name), os.path.join(app_dir, "docs", name))


def make_zip(folder: str, zip_path: str) -> None:
    base = os.path.dirname(folder)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for dirpath, _, files in os.walk(folder):
            for name in files:
                full = os.path.join(dirpath, name)
                z.write(full, os.path.relpath(full, base))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--version", default="dev")
    p.add_argument("--mode", choices=("portable", "pyinstaller", "none"), default="portable",
                   help="portable: signerad Python (standard). none: bara ffmpeg och textfiler.")
    a = p.parse_args()
    version = re.sub(r"[^0-9A-Za-z._-]+", "-", a.version.lstrip("v")) or "dev"

    os.makedirs(BUILD, exist_ok=True)
    dist = os.path.join(BUILD, "paket")
    if os.path.isdir(dist):
        shutil.rmtree(dist)
    if a.mode == "none":
        app_dir = os.path.join(dist, APP_NAME)
        os.makedirs(app_dir)
    elif a.mode == "pyinstaller":
        app_dir = pyinstaller(dist)
    else:
        app_dir = portable_python(dist)
    fetch_ffmpeg(app_dir)
    source = fetch_ffmpeg_source()
    extras(app_dir, version)
    zip_path = os.path.join(BUILD, f"OL_Video_Maker_{version}_windows.zip")
    make_zip(app_dir, zip_path)
    print("Klart:", zip_path, f"({os.path.getsize(zip_path) / 1e6:.0f} MB)")
    print("ffmpegs källkod (läggs bredvid zip-filen i releasen):", source)


if __name__ == "__main__":
    main()
