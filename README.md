# Sophämtning

Påminnelser om sophämtning i Nacka, i kalendern och som notis i mobilen.

Två delar:

- **Macen hämtar** (`mac/kor.sh`, launchd-jobbet `se.sophamtning.hamta`, kl. 06.45
  och 13.45 och när Macen vaknar). Den läser hämtschemat från Nacka vatten och
  avfalls tjänst (samma som sökrutan på nacka.se), skriver `state.json` och
  `sophamtning.ics`, pushar dem hit och skickar notis när schemat ändras: ett datum
  flyttas eller ställs in, ny tömningsregel, ny kärlplacering, ett kärl tillkommer
  eller försvinner. Hämtningen måste köras hemifrån: sajtens brandvägg spärrar
  GitHubs servrar ("Request Rejected").
- **GitHub påminner** (`.github/workflows/paminnelser.yml`, två gånger om dagen).
  Den läser `state.json` och skickar en notis kvällen före varje tömning (kl. 19)
  med hur kärlet ska stå. Den fungerar även när Macen är avstängd, och skickar en
  notis om schemat inte har hämtats på tre dagar.

Kalendern innehåller de publicerade datumen (i dag till årsskiftet) och därefter
preliminära datum, märkta "(prel.)", framräknade från tömningsregeln upp till fyra
månader fram. Regeln används bara om den återskapar exakt de publicerade datumen.
När de riktiga datumen publiceras ersätter de de preliminära, och skiljer de sig
kommer en notis.

## Prenumerera på kalendern

Adress: `https://raw.githubusercontent.com/jeppelina/sophamtning/main/sophamtning.ics`

- **iPhone:** Inställningar → Kalender → Konton → Lägg till konto → Annat →
  Lägg till prenumererad kalender. Klistra in adressen. Stäng av "Ta bort larm"
  om du vill ha kalenderns egen påminnelse kl. 19 kvällen före.
- **Mac:** Kalender → Arkiv → Ny kalenderprenumeration.
- **Google Kalender (webben):** Andra kalendrar → + → Från webbadress.
  Google läser om prenumerationer sällan (upp till ett dygn), så ändringar syns
  snabbast som notis.

## Notiser i mobilen

Notiserna går via [ntfy](https://ntfy.sh), en gratis app för iPhone och Android.
Installera appen och prenumerera på ämnet `NTFY_TOPIC` i `lokalt.env` (servern är
ntfy.sh). Ämnesnamnet är slumpat och fungerar som ett lösenord: den som känner till
det kan läsa notiserna. Det finns också som secret i repot.

## Inställningar

`lokalt.env` på Macen (checkas aldrig in):

```bash
SOPHAMTNING_ADRESS="Storgatan 1"
NTFY_TOPIC="sopor-..."
```

På GitHub: secret `NTFY_TOPIC`, och valfritt variabeln `PAMINNELSE_KL` (standard
`19:00`). Påminnelsekörningen kan också startas för hand under Actions.

## Installera på Macen

```bash
bash mac/installera.sh
```

Skriptet lägger en egen kopia av repot i `~/Library/Application Support/sophamtning`
(bakgrundsjobb nekas ofta åtkomst till Dokument-mappen), kopierar `lokalt.env` dit,
bygger en Pythonmiljö och startar launchd-jobbet. Kopian hämtar ändringar från
GitHub vid varje körning, så kod som pushas härifrån kommer med automatiskt. Logg:
`~/Library/Logs/sophamtning.log`. Ta bort jobbet med `bash mac/installera.sh --ta-bort`.

## Vad som är publikt

Repot är publikt så att kalenderfilen kan hämtas utan inloggning. Adressen finns
bara i `lokalt.env` på Macen och skrivs aldrig till loggen eller till filerna.
Publikt är koden, kalenderfilen, `state.json` (tömningsdatum, kärltyper och
placering) och `paminda.json` (vilka påminnelser som har skickats).

## Köra och testa lokalt

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
set -a; . ./lokalt.env; set +a; .venv/bin/python sophamtning.py --hamta --torrkorning
.venv/bin/python -m unittest discover -s tests
```

`--torrkorning` skickar inga notiser och skriver inga filer. `--idag 2026-12-01`
låtsas att det är ett annat datum. Utan `--hamta` eller `--paminn` körs båda delarna.

## Begränsningar

- Preliminära datum följer regeln bokstavligt. "Onsdag udda vecka" betyder onsdag i
  udda ISO-vecka, så när ett år har vecka 53 (som 2026) blir det två udda veckor i
  rad. Om Nacka i stället räknar varannan vecka rakt igenom blir de preliminära
  januaridatumen fel tills schemat publiceras; då kommer en notis.
- GitHubs schemalagda körningar kan bli försenade. Påminnelsen schemaläggs därför i
  ntfy till kl. 19, så den kommer i tid så länge någon körning hinner före dess.
- En ändring som Macen hittar efter att kvällens påminnelse redan schemalagts
  stoppar inte den påminnelsen, men ändringsnotisen kommer ändå.
- Enbostadshus har behovstömning: kärlet töms bara om det står ute med rätt sida mot
  vägen. Placeringen står i varje påminnelse och kalenderhändelse.
