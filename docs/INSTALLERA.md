# Installationsguide: så får du OL Video Maker på din dator

Den här guiden visar hur du hämtar programmet och startar det första gången. Du behöver inte
kunna något om datorer. Det tar ungefär 5 minuter.

**Det här behöver du:**

- En dator med **Windows 10 eller Windows 11**.
- Ungefär **500 MB ledigt utrymme** för programmet, och mer för dina filmer och videor.
- Internet för att ladda ner programmet. Sedan fungerar programmet helt utan internet.

Programmet är gratis. Du behöver inte skapa något konto och inte installera något annat.
Python och ffmpeg (som gör själva videon) följer med i mappen.

---

## Steg 1: Ladda ner rätt fil

1. Öppna den här sidan i din webbläsare:
   **https://github.com/hoa79/ol_video_maker/releases/latest**
2. Scrolla ner till rubriken **Assets**. Där finns flera filer.
3. Klicka på filen som heter **OL_Video_Maker_…_windows.zip** (till exempel
   `OL_Video_Maker_0.4.0-beta_windows.zip`, ungefär 80 MB).

   > **Ladda inte ner "Source code (zip)" eller "Source code (tar.gz)".** De filerna lägger
   > GitHub till automatiskt. De innehåller bara programkoden och går inte att starta.
   > Du behöver inte heller **ffmpeg-7.1.tar.xz**. Den finns där av licensskäl.

4. Vänta tills nedladdningen är klar. Filen hamnar i mappen **Hämtade filer**.

**Om webbläsaren varnar för filen:** Microsoft Edge kan skriva att filen *"inte laddas ner
så ofta"*. Det beror på att programmet är nytt, inte på att något är fel.

1. Håll muspekaren över filen i listan med nedladdningar och klicka på **…** (tre punkter).
2. Välj **Behåll**.
3. Klicka på **Visa mer** och sedan på **Behåll ändå**.

---

## Steg 2: Packa upp zip-filen

En zip-fil är som ett paket. Programmet fungerar inte om du startar det direkt inifrån
paketet, så du måste packa upp det först.

1. Öppna mappen **Hämtade filer** (klicka på mappikonen i aktivitetsfältet längst ner och
   välj **Hämtade filer** till vänster).
2. **Högerklicka** på `OL_Video_Maker_…_windows.zip`.
3. Välj **Extrahera alla…**.
4. Klicka på **Extrahera**. Windows skapar en ny mapp med samma namn som zip-filen och
   öppnar den.
5. I den nya mappen finns mappen **OL Video Maker**. Det är programmet.

> **Tips:** Vill du ha programmet någon annanstans, till exempel direkt på
> **Lokal disk (C:)**, kan du flytta hela mappen **OL Video Maker** dit. Flytta alltid hela
> mappen, inte bara en fil ur den.

---

## Steg 3: Starta programmet

1. Öppna mappen **OL Video Maker**.
2. Dubbelklicka på **OL Video Maker.exe**. (Ser du inte ".exe"? Leta efter **OL Video Maker**
   med Pythons blå och gula ikon.)
3. Visar Windows en blå ruta med texten **"Windows skyddade datorn"**?
   1. Klicka på **Mer information**.
   2. Klicka på **Kör ändå**.

   Rutan kan visas för att programmet är nytt. Du behöver bara göra detta första gången.
4. Programmet öppnas i ett eget fönster. Det kan ta några sekunder första gången.

---

## Steg 4: Läs och godkänn villkoren

Första gången du startar programmet visas rutan **Innan du börjar** med programmets villkor.
Där står bland annat att du själv ansvarar för vad du filmar och hur du använder videorna.

1. Läs villkoren. (Du kan också läsa dem i förväg i [VILLKOR.md](VILLKOR.md), eller i filen
   **Villkor.html** i programmets mapp.)
2. Kryssa i **Jag har läst villkoren och godkänner dem.**
3. Klicka på **Godkänn och fortsätt**.

Vill du inte godkänna villkoren, klicka på **Avsluta**. Då stängs programmet.

Programmet kommer ihåg att du har godkänt, så rutan visas inte nästa gång. Den visas igen
bara om villkoren ändras i en ny version.

---

## Steg 5: Skapa en genväg på skrivbordet (frivilligt)

1. Högerklicka på **OL Video Maker.exe** i mappen **OL Video Maker**.
2. I Windows 11: välj **Visa fler alternativ**.
3. Välj **Skicka till** och sedan **Skrivbord (skapa genväg)**.

Nu kan du starta programmet från skrivbordet.

Klart! Nu kan du göra din första video. Läs [guiden](GUIDE.md) (finns också som
**Guide.html** i programmets mapp) för hur du spelar in Livelox och gör videon.

---

## Om något inte fungerar

**Ingenting händer när jag dubbelklickar.**

1. Kontrollera att du har packat upp zip-filen (steg 2) och att du dubbelklickar i den
   uppackade mappen, inte inne i zip-filen.
2. Vänta 20 sekunder. Första starten kan vara långsam.
3. Starta om datorn och prova igen.

**Programmet säger att "Windows spärrade programmet ffmpeg.exe".**

Windows 11 har ett skydd som heter **Smart appkontroll**. Det kan stoppa program som
Microsoft inte känner igen.

1. Kontrollera att mappen **ffmpeg** finns i mappen **OL Video Maker**. Saknas den, packa upp
   zip-filen igen (steg 2).
2. Starta om datorn och prova igen. Ibland känner Windows igen programmet efter en stund.
3. Fungerar det fortfarande inte, kontakta den som gav dig programmet och berätta vilket
   meddelande du fick.

Stäng **inte** av Smart appkontroll. Det skyddar din dator.

**Mitt antivirusprogram stoppar programmet.**

Vissa antivirusprogram är försiktiga med nya program. Ladda bara ner programmet från sidan i
steg 1, så vet du att det är rätt fil. Fråga någon du litar på innan du gör undantag i
antivirusprogrammet.

---

## Uppdatera till en ny version

1. Ladda ner och packa upp den nya zip-filen (steg 1 och 2).
2. Ta bort den gamla mappen **OL Video Maker**, eller behåll den tills du har provat den nya.
3. Har du en genväg på skrivbordet? Ta bort den och gör en ny (steg 5), annars startar den
   gamla versionen.

Dina filmer och videor påverkas inte. De ligger kvar där du sparade dem.

## Ta bort programmet

Programmet installerar ingenting i Windows. För att ta bort det:

1. Ta bort mappen **OL Video Maker** (och zip-filen om den finns kvar).
2. Ta bort genvägen på skrivbordet om du gjorde en.
3. Vill du ta bort allt? Tryck på **Windows-tangenten + R**, skriv `%LOCALAPPDATA%` och tryck
   **Enter**. Ta bort mappen **OL Video Maker** där. Den innehåller bara fönstrets
   inställningar och att du har godkänt villkoren.

Dina egna filmer och videor tas inte bort.
