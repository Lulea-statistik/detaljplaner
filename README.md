# Pågående detaljplaner i Luleå

Veckovis övervakning av [Luleå kommuns pågående detaljplaner](https://www.lulea.se/samhalle--utveckling/samhallsplanering/detaljplaner/pagaende-detaljplaner.html).

## Datainnehåll
- `data/latest.csv` – senast observerade detaljplaner, titel, ärendenummer, publicerad status, planprocess och källänk.
- `data/latest.json` – samma data i JSON.
- `data/snapshots/` – daterade ögonblicksbilder för historik.
- `data/changes/` – förändringar relativt föregående lyckade körning.
- `data/CHANGELOG.md` – sammanfattning av senaste körningen.

## Uppdatering
GitHub Actions körs på måndagar 06:20 UTC samt manuellt via Actions → Veckovis detaljplanekontroll → Run workflow.

**Viktigt:** Den första körningen bildar baslinjen. Ett ärende som försvinner från pågående-listan är inte automatiskt antaget eller lagakraftvunnet. Ändringar i kommunens webbstruktur kan påverka insamlingen. Flödet avbryts vid för få hittade planer, större plötsliga bortfall eller omfattande hämtningsfel.

Källa: Luleå kommun. Detta är en fristående återpublicering, inte kommunens officiella planregister.

## Webbplats

En sökbar startsida finns i `docs/index.html` för publicering via GitHub Pages (`main` / `docs`). Efter aktivering är avsedd adress: https://lulea-statistik.github.io/detaljplaner/ . Webbplatsen läser de senaste insamlade datafilerna och ett index över historiska förändringar.

Områden lämnas tomma när de inte uttryckligen kan verifieras på plansidan. Ärendenummer kompletteras genom varje plans undersida; första körningen efter kodändringen behöver verifieras.
