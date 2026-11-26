#!/usr/bin/env python3
"""Påminnelser om sophämtning i Nacka: kalenderfil (ICS) och mobilnotiser (ntfy).

Två delar, som kan köras var för sig:

  --hamta   Hämtar hämtschemat från Nacka vatten och avfalls tjänst
            (futureweb.nvoa.se), skriver state.json och sophamtning.ics och
            skickar notis när schemat ändras eller hämtningen slutar fungera.
            Körs på Macen (mac/kor.sh), eftersom sajten spärrar molnservrar.
  --paminn  Skickar påminnelsen kvällen före utifrån state.json och larmar om
            schemat inte har hämtats på några dagar. Skriver paminda.json.
            Körs av GitHub (.github/workflows/paminnelser.yml).

Utan flagga körs båda.

Miljövariabler:
  SOPHAMTNING_ADRESS  adressen som den skrivs i sökrutan (krävs för --hamta)
  NTFY_TOPIC          ntfy-ämnet som notiserna skickas till (utan det: inga notiser)
  NTFY_SERVER         standard https://ntfy.sh
  PAMINNELSE_KL       klockslag för påminnelsen kvällen före, standard 19:00

Adressen skrivs aldrig ut i loggen eller i filerna, eftersom repot är publikt.
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = "https://futureweb.nvoa.se/EDP/FutureWebBasic/SimpleWastePickup"
INFO_URL = "https://www.nacka.se/nackavattenavfall/avfall/sophamtning/matavfall/tomningsdag/"
TZ = ZoneInfo("Europe/Stockholm")
ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "state.json"
ICS_FILE = ROOT / "sophamtning.ics"
PAMINDA_FILE = ROOT / "paminda.json"

PROGNOS_DAGAR = 120     # så långt fram förutses datum enligt tömningsregeln
BEHALL_PASSERADE = 60   # dagar som passerade tömningar ligger kvar i kalendern
LARM_EFTER_FEL = 4      # notis efter så många misslyckade hämtningar i rad (två per dag)
INAKTUELL_DAGAR = 3    # notis om schemat inte har hämtats på så många dagar

WEEKDAYS = {"måndag": 0, "tisdag": 1, "onsdag": 2, "torsdag": 3, "fredag": 4, "lördag": 5, "söndag": 6}
SV_DAY = ["mån", "tis", "ons", "tor", "fre", "lör", "sön"]
SV_MONTH = ["jan", "feb", "mar", "apr", "maj", "jun", "jul", "aug", "sep", "okt", "nov", "dec"]
PDF_DATE = re.compile(r"\b(mån|tis|ons|tor|fre|lör|sön)\s+(\d{1,2})\s+(" + "|".join(SV_MONTH) + r")\s+(\d{4})\b")
RULE_PARITY = re.compile(r"(måndag|tisdag|onsdag|torsdag|fredag)\s+(udda|jämn)\s+vecka", re.I)
RULE_WEEK = re.compile(r"vecka\s+(\d{1,2})\s+(måndag|tisdag|onsdag|torsdag|fredag)", re.I)


class FetchError(Exception):
    pass


# --- Hjälpfunktioner ---------------------------------------------------------

def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def lc_first(s: str) -> str:
    return s[:1].lower() + s[1:] if s else s


def fmt_date(d: dt.date, today: dt.date | None = None) -> str:
    s = f"{SV_DAY[d.weekday()]} {d.day} {SV_MONTH[d.month - 1]}"
    return s if today and d.year == today.year else f"{s} {d.year}"


def fmt_dates(ds: list[dt.date], today: dt.date, limit: int = 4) -> str:
    s = ", ".join(fmt_date(d, today) for d in ds[:limit])
    return s + (f" och {len(ds) - limit} till" if len(ds) > limit else "")


def fmt_num(x) -> str:
    if x is None:
        return ""
    return str(int(x)) if float(x).is_integer() else str(x).replace(".", ",")


def slug(s: str) -> str:
    s = s.lower().translate(str.maketrans("åäö", "aao"))
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def http(url: str, data: bytes | None = None, headers: dict | None = None, timeout: int = 30) -> bytes:
    h = {"User-Agent": "sophamtning-paminnelse/1.0", **(headers or {})}
    last: Exception | None = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, data=data, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            last = e
            if e.code < 500:
                break
        except Exception as e:  # nätverksfel, timeout
            last = e
        time.sleep(5 * (attempt + 1))
    # Frågedelen av URL:en innehåller adressen och skrivs därför inte ut.
    raise FetchError(f"{url.split('?')[0]}: {last}")


def get_json(url: str, data: bytes | None = None, headers: dict | None = None):
    body = http(url, data, headers)
    try:
        return json.loads(body)
    except ValueError:
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body.decode("utf-8", "replace"))).strip()
        raise FetchError(f"{url.split('?')[0]} svarade inte med JSON: {text[:160]!r}") from None


# --- Tömningsregler ----------------------------------------------------------

def rule_dates(rule: str, per_year: int, start: dt.date, end: dt.date) -> list[dt.date] | None:
    """Alla datum i [start, end] enligt regeltexten, eller None om regeln inte går att tolka.

    "Onsdag udda vecka": onsdagar i udda ISO-veckor, tolkat bokstavligt (ett år med
    vecka 53 ger då två udda veckor i rad).
    "Glas, Vecka 9 Torsdag" med 4 tömningar per år: torsdag vecka 9, 22, 35 och 48.
    """
    m = RULE_PARITY.search(rule or "")
    if m:
        wd = WEEKDAYS[m[1].lower()]
        odd = m[2].lower() == "udda"
        d = start + dt.timedelta((wd - start.weekday()) % 7)
        out = []
        while d <= end:
            if (d.isocalendar().week % 2 == 1) == odd:
                out.append(d)
            d += dt.timedelta(7)
        return out
    m = RULE_WEEK.search(rule or "")
    if m and per_year and 52 % per_year == 0:
        step = 52 // per_year
        weeks = {(int(m[1]) - 1 + step * k) % 52 + 1 for k in range(per_year)}
        wd = WEEKDAYS[m[2].lower()]
        out = []
        for year in range(start.isocalendar().year, end.isocalendar().year + 1):
            for w in weeks:
                d = dt.date.fromisocalendar(year, w, wd + 1)
                if start <= d <= end:
                    out.append(d)
        return sorted(out)
    return None


def project(rule: str, per_year: int, official_future: set[dt.date], today: dt.date,
            trusted_before: bool) -> tuple[list[dt.date], bool]:
    """Preliminära datum efter det publicerade schemats slut.

    Regeln används bara om den återskapar exakt de publicerade datumen. Finns inga
    publicerade datum alls används den om den stämde vid förra körningen.
    Returnerar (datum, om regeln stämmer).
    """
    end = today + dt.timedelta(PROGNOS_DAGAR)
    if official_future:
        first, horizon = min(official_future), max(official_future)
        expected = rule_dates(rule, per_year, first, horizon)
        ok = expected is not None and set(expected) == official_future
        start = horizon + dt.timedelta(1)
    else:
        ok = trusted_before and rule_dates(rule, per_year, today, today) is not None
        start = today + dt.timedelta(1)
    return (rule_dates(rule, per_year, start, end) or [] if ok else []), ok


# --- Hämtning ----------------------------------------------------------------

def find_building(search: str) -> str:
    body = json.dumps({"searchText": search}).encode()
    res = get_json(f"{BASE}/SearchAdress", body, {"Content-Type": "application/json; charset=utf-8"})
    hits = res.get("Buildings") or []
    if not res.get("Succeeded") or not hits:
        raise FetchError("adressen gav ingen träff")
    want = norm(search.split(",")[0])
    exact = [b for b in hits if norm(b.split(",")[0]) == want]
    if len(exact) != 1:
        raise FetchError(f"adressen gav {len(hits)} träffar, varav {len(exact)} exakta")
    return exact[0]


def parse_pdf_text(text: str, waste_types: list[str]) -> dict[str, set[dt.date]]:
    """Datum per avfallstyp. Varje datum hör till närmast föregående avfallstyp i texten."""
    marks = []
    for wt in set(waste_types):
        pat = r"\s+".join(map(re.escape, wt.split()))
        marks += [(m.start(), wt) for m in re.finditer(r"(?<!\S)" + pat + r"(?!\S)", text, re.I)]
    marks.sort()
    out: dict[str, set[dt.date]] = {wt: set() for wt in waste_types}
    for m in PDF_DATE.finditer(text):
        owners = [wt for pos, wt in marks if pos < m.start()]
        if not owners:
            continue
        d = dt.date(int(m[4]), SV_MONTH.index(m[3]) + 1, int(m[2]))
        if SV_DAY[d.weekday()] != m[1]:
            raise FetchError(f"veckodagen stämmer inte för {m[0]!r} i PDF:en")
        out[owners[-1]].add(d)
    return out


def fetch_pdf_dates(building: str, waste_types: list[str]) -> dict[str, set[dt.date]]:
    from pypdf import PdfReader  # importeras här så att resten fungerar utan pypdf

    q = urllib.parse.urlencode({"searchAdress": building})
    pdf = http(f"{BASE}/DownloadWastePickup?{q}", timeout=60)
    text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(pdf)).pages)
    dates = parse_pdf_text(text, waste_types)
    if not any(dates.values()):
        raise FetchError("hittade inga datum i PDF:en")
    return dates


def build_bins(services: list[dict], pdf_dates: dict[str, set[dt.date]] | None,
               old_bins: dict, today: dt.date) -> dict:
    bins: dict[str, dict] = {}
    for s in services:
        wt = (s.get("WasteType") or "").strip()
        if not wt:
            continue
        bt = s.get("BinType") or {}
        b = bins.setdefault(wt, {
            "regel": (s.get("WastePickupFrequency") or "").strip(),
            "per_ar": int(s.get("WastePickupsPerYear") or 0),
            "placering": (s.get("BinDirection") or "").strip(),
            "karl": " ".join(x for x in [
                f"{fmt_num(s.get('NumberOfBins', 1))} st",
                f"{fmt_num(bt.get('Size'))} {bt.get('Unit') or ''}".strip(),
                (bt.get("ContainerType") or "").lower(),
            ] if x),
            "datum": set(),
        })
        try:
            b["datum"].add(dt.date.fromisoformat((s.get("NextWastePickup") or "")[:10]))
        except ValueError:
            pass

    for wt, b in bins.items():
        old = old_bins.get(wt, {})
        if pdf_dates is not None:
            b["datum"] |= pdf_dates.get(wt, set())
        else:  # PDF:en gick inte att läsa: behåll förra körningens kommande datum
            b["datum"] |= {d for d in old.get("datum", set()) if d > today}
        future = {d for d in b["datum"] if d >= today}
        past = {d for d in old.get("datum", set())
                if today - dt.timedelta(BEHALL_PASSERADE) <= d < today}
        b["datum"] = past | future
        trusted = bool(old.get("regel_ok")) and norm(old.get("regel", "")) == norm(b["regel"])
        prognos, b["regel_ok"] = project(b["regel"], b["per_ar"], future, today, trusted)
        b["prognos"] = set(prognos)
    return bins


def fetch(search: str, old_bins: dict, today: dt.date) -> tuple[dict, list[str]]:
    """Returnerar (kärl, varningar). Kastar FetchError om grunddata saknas."""
    building = find_building(search)
    q = urllib.parse.urlencode({"address": building})
    data = get_json(f"{BASE}/GetWastePickupSchedule?{q}")
    services = [s for s in data.get("RhServices") or [] if s.get("IsActive", True)]
    if not services:
        raise FetchError("schemat innehöll inga kärl")
    warnings = []
    try:
        pdf_dates = fetch_pdf_dates(building, [(s.get("WasteType") or "").strip() for s in services])
    except Exception as e:
        pdf_dates = None
        warnings.append(f"PDF-schemat kunde inte läsas ({type(e).__name__}: {e}), "
                        "använder förra körningens datum")
    return build_bins(services, pdf_dates, old_bins, today), warnings


# --- Ändringar och påminnelser -----------------------------------------------

def changes(old_bins: dict, new_bins: dict, today: dt.date) -> list[str]:
    """Skillnader som är värda en notis. Bara datum efter i dag jämförs.

    Ett tidigare publicerat datum som saknas räknas som borttaget om det ligger inom
    det nya schemats slut (sista publicerade datumet för något kärl), så att en flytt
    av ett kärls sista datum inte missas. Ett preliminärt datum räknas bara som fel
    om kärlets egna publicerade datum nu når förbi det. Ett nytt publicerat datum
    räknas om det ligger inom det som var känt förut men inte fanns med.
    """
    horizon = max((d for b in new_bins.values() for d in b["datum"]), default=None)
    msgs = []
    for wt in sorted(set(old_bins) | set(new_bins)):
        o, n = old_bins.get(wt), new_bins.get(wt)
        if o is None:
            msgs.append(f"Nytt i schemat: {wt}, {n['regel']}.")
            continue
        if n is None:
            msgs.append(f"{wt} finns inte längre med i schemat.")
            continue
        if norm(o.get("regel", "")) != norm(n["regel"]):
            msgs.append(f"{wt}: ny tömningsregel, {n['regel']} (förut {o.get('regel')}).")
        if norm(o.get("placering", "")) != norm(n["placering"]):
            msgs.append(f"{wt}: kärlet ska nu ställas med {lc_first(n['placering'])} "
                        f"(förut {lc_first(o.get('placering', ''))}).")
        old_off = {d for d in o.get("datum", set()) if d > today}
        old_prel = {d for d in o.get("prognos", set()) if d > today}
        old_exp = old_off | old_prel
        new_off = {d for d in n["datum"] if d > today}
        own_end = max(new_off, default=None)
        gone = sorted({d for d in old_off if horizon and d <= horizon and d not in new_off}
                      | {d for d in old_prel if own_end and d <= own_end and d not in new_off})
        added = sorted(d for d in new_off if old_exp and d <= max(old_exp) and d not in old_exp)
        if len(gone) == 1 and len(added) == 1:
            msgs.append(f"{wt}: flyttad från {fmt_date(gone[0], today)} till {fmt_date(added[0], today)}.")
        else:
            if gone:
                msgs.append(f"{wt}: ingen tömning {fmt_dates(gone, today)}.")
            if added:
                msgs.append(f"{wt}: ny tömning {fmt_dates(added, today)}.")
    return msgs


def placement_text(b: dict) -> str:
    return f"ställ ut kärlet med {lc_first(b['placering'])}." if b.get("placering") else "ställ ut kärlet."


def reminder(bins: dict, today: dt.date, now: dt.datetime, kl: dt.time):
    """(rubrik, text, skickas_kl) för tömningar i morgon, eller None."""
    tomorrow = today + dt.timedelta(1)
    lines = []
    for wt, b in sorted(bins.items()):
        if tomorrow in b["datum"]:
            lines.append(f"{wt}: {placement_text(b)}")
        elif tomorrow in b["prognos"]:
            lines.append(f"{wt}: {placement_text(b)} (Preliminärt datum.)")
    if not lines:
        return None
    at = dt.datetime.combine(today, kl, TZ)
    return (f"Sophämtning i morgon, {fmt_date(tomorrow, today)}", "\n".join(lines),
            at if now < at - dt.timedelta(minutes=1) else None)


def next_pickups(bins: dict, today: dt.date) -> str:
    lines = []
    for wt, b in sorted(bins.items()):
        upcoming = sorted(d for d in b["datum"] | b["prognos"] if d >= today)
        if upcoming:
            lines.append(f"{wt}: {fmt_date(upcoming[0], today)} ({b['regel']})")
        else:
            lines.append(f"{wt}: inget datum publicerat")
    return "\n".join(lines)


# --- Notiser -----------------------------------------------------------------

def print_notice(title: str, message: str, tags=None, priority=3, at=None, note="torrkörning"):
    when = f", skickas {at:%Y-%m-%d %H:%M}" if at else ""
    print(f"[notis, {note}{when}] {title}\n  " + message.replace("\n", "\n  "))


def notify(title: str, message: str, tags=("wastebasket",), priority: int = 3, at=None):
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        print_notice(title, message, at=at, note="ingen NTFY_TOPIC, skickas inte")
        return
    payload = {"topic": topic, "title": title, "message": message,
               "tags": list(tags), "priority": priority, "click": INFO_URL}
    if at:
        payload["delay"] = str(int(at.timestamp()))
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    http(server + "/", json.dumps(payload).encode(), {"Content-Type": "application/json"})
    print(f"Notis skickad{f' (levereras {at:%H:%M})' if at else ''}: {title}")


# --- Kalenderfil -------------------------------------------------------------

def ics_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def ics_fold(line: str) -> str:
    """Radbryt enligt RFC 5545: högst 75 byte per rad, utan att dela tecken."""
    parts, cur = [], b""
    for ch in line:
        enc = ch.encode()
        if len(cur) + len(enc) > (75 if not parts else 74):
            parts.append(cur)
            cur = b""
        cur += enc
    parts.append(cur)
    return "\r\n ".join(p.decode() for p in parts)


def make_ics(bins: dict, stamp: str, kl: dt.time) -> str:
    before = 24 * 60 - (kl.hour * 60 + kl.minute)  # minuter före midnatt på tömningsdagen
    trigger = f"-PT{before // 60}H{before % 60}M" if before % 60 else f"-PT{before // 60}H"
    events = []
    for wt, b in bins.items():
        events += [(d, wt, False) for d in b["datum"]]
        events += [(d, wt, True) for d in b["prognos"] if d not in b["datum"]]
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//sophamtning//SV", "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH", "X-WR-CALNAME:Sophämtning", "X-WR-TIMEZONE:Europe/Stockholm",
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H", "X-PUBLISHED-TTL:PT6H",
    ]
    for d, wt, prel in sorted(events):
        b = bins[wt]
        summary = f"Sophämtning: {wt}" + (" (prel.)" if prel else "")
        placement = placement_text(b)
        desc = [x for x in [
            f"{b['karl']}." if b.get("karl") else "",
            placement[:1].upper() + placement[1:],
            f"Tömningsregel: {b['regel']}." if b.get("regel") else "",
            "Preliminärt datum, framräknat från tömningsregeln. Ersätts när Nacka vatten och "
            "avfall publicerar schemat." if prel else "",
        ] if x]
        lines += [
            "BEGIN:VEVENT",
            f"UID:{d:%Y%m%d}-{slug(wt)}@sophamtning",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{d:%Y%m%d}",
            f"DTEND;VALUE=DATE:{d + dt.timedelta(1):%Y%m%d}",
            f"SUMMARY:{ics_escape(summary)}",
            f"DESCRIPTION:{ics_escape(chr(10).join(desc))}",
            f"URL:{INFO_URL}",
            "TRANSP:TRANSPARENT",
            "BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{ics_escape(summary)}",
            f"TRIGGER:{trigger}", "END:VALARM",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(ics_fold(x) for x in lines) + "\r\n"


def without_stamps(ics: str) -> str:
    return re.sub(r"^DTSTAMP:.*\r?\n", "", ics, flags=re.M)


# --- Tillstånd ---------------------------------------------------------------

def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    for b in raw.get("karl", {}).values():
        b["datum"] = {dt.date.fromisoformat(d) for d in b.get("datum", [])}
        b["prognos"] = {dt.date.fromisoformat(d) for d in b.get("prognos", [])}
    return raw


def dump_state(state: dict) -> str:
    out = dict(state)
    out["karl"] = {wt: {**b, "datum": sorted(d.isoformat() for d in b["datum"]),
                        "prognos": sorted(d.isoformat() for d in b["prognos"])}
                   for wt, b in sorted(state.get("karl", {}).items())}
    return json.dumps(out, ensure_ascii=False, indent=2) + "\n"


def carry_over(old_bins: dict, today: dt.date) -> dict:
    """Förra körningens schema, utan tömningar som ligger för långt bak."""
    cutoff = today - dt.timedelta(BEHALL_PASSERADE)
    return {wt: {**b, "datum": {d for d in b["datum"] if d >= cutoff},
                 "prognos": {d for d in b["prognos"] if d >= today}}
            for wt, b in old_bins.items()}


# --- Huvudprogram ------------------------------------------------------------

def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def run_fetch(args, today: dt.date, kl: dt.time, send) -> int:
    """Hämta schemat, notis vid ändringar och fel, skriv state.json och kalenderfilen."""
    search = os.environ.get("SOPHAMTNING_ADRESS", "").strip()
    if not search:
        print("SOPHAMTNING_ADRESS saknas.", file=sys.stderr)
        return 2
    state = load_state()
    old_bins = state.get("karl", {})
    first_run = not old_bins
    fetched = True
    try:
        bins, warnings = fetch(search, old_bins, today)
        problem = "; ".join(warnings) or None
    except Exception as e:
        if first_run:
            print(f"Första hämtningen misslyckades: {type(e).__name__}: {e}", file=sys.stderr)
            return 1
        bins, problem, fetched = carry_over(old_bins, today), f"{type(e).__name__}: {e}", False

    if fetched:
        state["hamtat"] = today.isoformat()
    if problem:
        state["fel_i_rad"] = state.get("fel_i_rad", 0) + 1
        state.setdefault("fel_sedan", today.isoformat())
        print(f"VARNING ({state['fel_i_rad']} i rad): {problem}")
        if state["fel_i_rad"] == LARM_EFTER_FEL:
            send("Sophämtning: schemat kunde inte läsas",
                 f"De senaste {LARM_EFTER_FEL} körningarna har misslyckats (sedan {state['fel_sedan']}). "
                 f"Kalendern och påminnelserna bygger på senast hämtade schema.\n\nFel: {problem}",
                 tags=["warning"], priority=4)
    else:
        if state.get("fel_i_rad", 0) >= LARM_EFTER_FEL:
            send("Sophämtning: schemat läses igen", "Hämtningen fungerar igen.", tags=["white_check_mark"])
        state["fel_i_rad"] = 0
        state.pop("fel_sedan", None)

    if first_run:
        send("Sophämtning: påminnelser igång", "Nästa tömning:\n" + next_pickups(bins, today))
    elif fetched:
        msgs = changes(old_bins, bins, today)
        if msgs:
            send("Sophämtning: schemat har ändrats", "\n".join(msgs), tags=["warning"], priority=4)

    state["karl"] = bins
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ics = make_ics(bins, stamp, kl)
    print("Nästa tömning:\n  " + next_pickups(bins, today).replace("\n", "\n  "))
    if args.torrkorning:
        print(f"(torrkörning: {ics.count('BEGIN:VEVENT')} händelser, inga filer skrivna)")
    else:
        old_ics = ICS_FILE.read_text(encoding="utf-8") if ICS_FILE.exists() else ""
        if without_stamps(old_ics) != without_stamps(ics):
            ICS_FILE.write_text(ics, encoding="utf-8", newline="")
            print(f"Skrev {ICS_FILE.name} ({ics.count('BEGIN:VEVENT')} händelser)")
        STATE_FILE.write_text(dump_state(state), encoding="utf-8")
    return 1 if state["fel_i_rad"] >= LARM_EFTER_FEL else 0


def run_remind(args, today: dt.date, now: dt.datetime, kl: dt.time, send) -> int:
    """Påminnelse kvällen före utifrån sparat schema, och larm om schemat är gammalt.
    Behöver ingen adress och ingen kontakt med sajten."""
    state = load_state()
    bins = state.get("karl", {})
    if not bins:
        print("Inget sparat schema än (state.json saknas).")
        return 0
    log = load_json(PAMINDA_FILE)
    ok = True

    hamtat = state.get("hamtat")
    if hamtat and (today - dt.date.fromisoformat(hamtat)).days >= INAKTUELL_DAGAR:
        print(f"VARNING: schemat hämtades senast {hamtat}.")
        if log.get("inaktuell_larm") != hamtat:
            if send("Sophämtning: schemat har inte uppdaterats",
                    f"Schemat hämtades senast {hamtat}. Hämtningen körs på Macen, så den är "
                    "kanske avstängd. Påminnelserna fortsätter utifrån det senast hämtade schemat, "
                    "men ändringar upptäcks inte.", tags=["warning"], priority=4):
                log["inaktuell_larm"] = hamtat

    reminded = [d for d in log.get("paminda", [])
                if dt.date.fromisoformat(d) >= today - dt.timedelta(30)]
    r = reminder(bins, today, now, kl)
    key = (today + dt.timedelta(1)).isoformat()
    if r and key not in reminded:
        title, message, at = r
        if send(title, message, at=at):
            reminded.append(key)
        else:
            ok = False
    elif not r:
        print("Ingen tömning i morgon.")
    log["paminda"] = sorted(reminded)
    if not args.torrkorning:
        PAMINDA_FILE.write_text(json.dumps(log, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Hämtschema till kalender och mobilnotiser.")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--hamta", action="store_true", help="bara hämtning, ändringsnotiser och kalenderfil")
    mode.add_argument("--paminn", action="store_true", help="bara påminnelser utifrån sparat schema")
    ap.add_argument("--idag", type=dt.date.fromisoformat, help="låtsas att det är detta datum (test)")
    ap.add_argument("--torrkorning", action="store_true", help="skicka inga notiser och skriv inga filer")
    args = ap.parse_args(argv)

    kl = dt.time.fromisoformat(os.environ.get("PAMINNELSE_KL", "19:00"))
    now = dt.datetime.now(TZ)
    today = args.idag or now.date()
    if args.idag:
        now = dt.datetime.combine(today, dt.time(12), TZ)

    def send(*a, **k):
        try:
            (print_notice if args.torrkorning else notify)(*a, **k)
            return True
        except Exception as e:
            print(f"VARNING: notisen kunde inte skickas ({e})")
            return False

    code = 0
    if not args.paminn:
        code = run_fetch(args, today, kl, send)
        if code == 2 or (code == 1 and not STATE_FILE.exists()):
            return code
    if not args.hamta:
        code = max(code, run_remind(args, today, now, kl, send))
    return code


if __name__ == "__main__":
    sys.exit(main())
