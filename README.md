# Sophämtning

Påminnelser om sophämtning i Nacka, i kalendern och som notis i mobilen.

Två gånger om dagen hämtar ett GitHub-arbetsflöde hämtschemat från Nacka vatten
och avfalls tjänst (samma tjänst som sökrutan på nacka.se använder) och

- skriver om `sophamtning.ics`, en kalender att prenumerera på,
- skickar en notis kvällen före varje tömning (kl. 19) med hur kärlet ska stå,
- skickar en notis när schemat ändras: ett datum flyttas eller ställs in, en ny
  tömningsregel, ny kärlplacering, ett kärl tillkommer eller försvinner,
- skickar en notis om hämtningen har misslyckats två dagar i rad, och en när den
  fungerar igen.

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
Installera appen och prenumerera på ämnet i repots hemlighet `NTFY_TOPIC` (det
finns också lokalt i filen `.ntfy-topic`, som inte checkas in). Ämnesnamnet är
slumpat och fungerar som ett lösenord: den som känner till det kan läsa notiserna.

## Inställningar

| Var | Namn | Innehåll |
|---|---|---|
| Secret | `SOPHAMTNING_ADRESS` | adressen som den skrivs i sökrutan |
| Secret | `NTFY_TOPIC` | ntfy-ämnet |
| Variable (valfri) | `PAMINNELSE_KL` | klockslag kvällen före, standard `19:00` |

Arbetsflödet kan också köras för hand: Actions → Uppdatera hämtschema → Run workflow.

## Vad som är publikt

Repot är publikt så att kalenderfilen kan hämtas utan inloggning. Adressen finns
bara i en secret och skrivs aldrig till loggen eller till filerna. Publikt är
koden, kalenderfilen och `state.json`: tömningsdatum, kärltyper och placering.

## Köra lokalt

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
SOPHAMTNING_ADRESS="Storgatan 1" .venv/bin/python sophamtning.py --torrkorning
.venv/bin/python -m unittest discover -s tests
```

`--torrkorning` skickar inga notiser och skriver inga filer. `--idag 2026-12-01`
låtsas att det är ett annat datum.

## Begränsningar

- Preliminära datum följer regeln bokstavligt. "Onsdag udda vecka" betyder onsdag i
  udda ISO-vecka, så när ett år har vecka 53 (som 2026) blir det två udda veckor i
  rad. Om Nacka i stället räknar varannan vecka rakt igenom blir de preliminära
  januaridatumen fel tills schemat publiceras; då kommer en notis.
- GitHubs schemalagda körningar kan bli försenade. Påminnelsen schemaläggs därför i
  ntfy till kl. 19, så den kommer i tid så länge någon körning hinner före dess.
- Enbostadshus har behovstömning: kärlet töms bara om det står ute med rätt sida mot
  vägen. Placeringen står i varje påminnelse och kalenderhändelse.
