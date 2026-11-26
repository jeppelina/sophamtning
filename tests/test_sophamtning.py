import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sophamtning as s  # noqa: E402

D = dt.date
PDF_TEXT = (
    "Fastighet X\nAnläggning Y\nGlas OF/FG\ntor 26 nov 2026\n"
    "Fastighet X\nAnläggning Y\nMat/Rest\nons 02 dec 2026 ons 16 dec 2026 ons 30 dec 2026\n"
    "Fastighet X\nAnläggning Y\nPlast/Papper\nons 09 dec 2026 ons 23 dec 2026"
)
SERVICES = [
    {"WasteType": "Mat/Rest", "NextWastePickup": "2026-12-02", "WastePickupsPerYear": 26,
     "WastePickupFrequency": "Onsdag udda vecka ", "BinDirection": "Handtag och hjul vänt mot fastighet",
     "NumberOfBins": 1.0, "BinType": {"Size": 240.0, "Unit": "L", "ContainerType": "Tvåfackskärl"}},
    {"WasteType": "Plast/Papper", "NextWastePickup": "2026-12-09", "WastePickupsPerYear": 26,
     "WastePickupFrequency": "Onsdag jämn vecka ", "BinDirection": "Handtag och hjul ut mot vägen",
     "NumberOfBins": 1.0, "BinType": {"Size": 370.0, "Unit": "L", "ContainerType": "Tvåfackskärl"}},
    {"WasteType": "Glas OF/FG", "NextWastePickup": "2026-11-26", "WastePickupsPerYear": 4,
     "WastePickupFrequency": "Glas, Vecka 9 Torsdag", "BinDirection": "Handtag och hjul ut mot vägen",
     "NumberOfBins": 1.0, "BinType": {"Size": 140.0, "Unit": "L", "ContainerType": "Tvåfackskärl"}},
]
WT = [x["WasteType"] for x in SERVICES]


class Rules(unittest.TestCase):
    def test_parity_rule_matches_published_dates(self):
        self.assertEqual(s.rule_dates("Onsdag udda vecka", 26, D(2026, 11, 26), D(2026, 12, 31)),
                         [D(2026, 12, 2), D(2026, 12, 16), D(2026, 12, 30)])
        self.assertEqual(s.rule_dates("Onsdag jämn vecka", 26, D(2026, 11, 26), D(2026, 12, 31)),
                         [D(2026, 12, 9), D(2026, 12, 23)])

    def test_parity_rule_is_literal_across_week_53(self):
        # 2026 har vecka 53 (udda), och vecka 1 2027 är också udda.
        self.assertEqual(s.rule_dates("Onsdag udda vecka", 26, D(2026, 12, 28), D(2027, 1, 21)),
                         [D(2026, 12, 30), D(2027, 1, 6), D(2027, 1, 20)])

    def test_glass_rule_quarterly(self):
        self.assertEqual(s.rule_dates("Glas, Vecka 9 Torsdag", 4, D(2026, 1, 1), D(2027, 12, 31)),
                         [D(2026, 2, 26), D(2026, 5, 28), D(2026, 8, 27), D(2026, 11, 26),
                          D(2027, 3, 4), D(2027, 6, 3), D(2027, 9, 2), D(2027, 12, 2)])

    def test_unknown_rule(self):
        self.assertIsNone(s.rule_dates("Enligt överenskommelse", 26, D(2026, 1, 1), D(2026, 2, 1)))

    def test_projection_only_when_rule_reproduces_schedule(self):
        today = D(2026, 11, 26)
        dates, ok = s.project("Onsdag udda vecka", 26, {D(2026, 12, 2), D(2026, 12, 16)}, today, False)
        self.assertTrue(ok)
        self.assertEqual(dates[0], D(2026, 12, 30))
        dates, ok = s.project("Onsdag udda vecka", 26, {D(2026, 12, 2), D(2026, 12, 17)}, today, False)
        self.assertFalse(ok)
        self.assertEqual(dates, [])


class Parsing(unittest.TestCase):
    def test_pdf_text(self):
        out = s.parse_pdf_text(PDF_TEXT, WT)
        self.assertEqual(out["Glas OF/FG"], {D(2026, 11, 26)})
        self.assertEqual(out["Mat/Rest"], {D(2026, 12, 2), D(2026, 12, 16), D(2026, 12, 30)})
        self.assertEqual(out["Plast/Papper"], {D(2026, 12, 9), D(2026, 12, 23)})

    def test_pdf_wrong_weekday_is_rejected(self):
        with self.assertRaises(s.FetchError):
            s.parse_pdf_text("Mat/Rest\ntor 02 dec 2026", ["Mat/Rest"])

    def test_build_bins(self):
        today = D(2026, 11, 26)
        bins = s.build_bins(SERVICES, s.parse_pdf_text(PDF_TEXT, WT), {}, today)
        mr = bins["Mat/Rest"]
        self.assertEqual(mr["karl"], "1 st 240 L tvåfackskärl")
        self.assertTrue(mr["regel_ok"])
        self.assertEqual(min(mr["prognos"]), D(2027, 1, 6))
        self.assertEqual(bins["Glas OF/FG"]["prognos"], {D(2027, 3, 4)})

    def test_pdf_failure_keeps_previous_dates(self):
        today = D(2026, 11, 26)
        old = s.build_bins(SERVICES, s.parse_pdf_text(PDF_TEXT, WT), {}, today)
        new = s.build_bins(SERVICES, None, old, D(2026, 11, 27))
        self.assertEqual(new["Mat/Rest"]["datum"], old["Mat/Rest"]["datum"])


class Changes(unittest.TestCase):
    def setUp(self):
        self.today = D(2026, 11, 26)
        self.old = s.build_bins(SERVICES, s.parse_pdf_text(PDF_TEXT, WT), {}, self.today)

    def test_no_change(self):
        new = s.build_bins(SERVICES, s.parse_pdf_text(PDF_TEXT, WT), self.old, self.today)
        self.assertEqual(s.changes(self.old, new, self.today), [])

    def test_passing_pickup_is_not_a_change(self):
        # Dagen efter glastömningen: 26 nov finns inte längre i schemat.
        services = [x for x in SERVICES if x["WasteType"] != "Glas OF/FG"]
        services.append({**SERVICES[2], "NextWastePickup": ""})
        text = PDF_TEXT.replace("Glas OF/FG\ntor 26 nov 2026\n", "Glas OF/FG\n")
        new = s.build_bins(services, s.parse_pdf_text(text, WT), self.old, D(2026, 11, 27))
        self.assertEqual(s.changes(self.old, new, D(2026, 11, 27)), [])
        # Regeln stämde i går, så glaset ligger kvar som preliminärt datum.
        self.assertEqual(new["Glas OF/FG"]["prognos"], {D(2027, 3, 4)})

    def test_moved_date(self):
        text = PDF_TEXT.replace("ons 23 dec 2026", "tis 22 dec 2026")
        new = s.build_bins(SERVICES, s.parse_pdf_text(text, WT), self.old, self.today)
        self.assertEqual(s.changes(self.old, new, self.today),
                         ["Plast/Papper: flyttad från ons 23 dec till tis 22 dec."])

    def test_cancelled_date(self):
        text = PDF_TEXT.replace(" ons 16 dec 2026", "")
        new = s.build_bins(SERVICES, s.parse_pdf_text(text, WT), self.old, self.today)
        self.assertEqual(s.changes(self.old, new, self.today), ["Mat/Rest: ingen tömning ons 16 dec."])

    def test_cancelled_last_date_of_a_bin(self):
        text = PDF_TEXT.replace(" ons 23 dec 2026", "")
        new = s.build_bins(SERVICES, s.parse_pdf_text(text, WT), self.old, self.today)
        self.assertEqual(s.changes(self.old, new, self.today), ["Plast/Papper: ingen tömning ons 23 dec."])

    def test_published_dates_replacing_matching_prognosis_is_silent(self):
        text = PDF_TEXT.replace("ons 30 dec 2026", "ons 30 dec 2026 ons 06 jan 2027 ons 20 jan 2027")
        new = s.build_bins(SERVICES, s.parse_pdf_text(text, WT), self.old, self.today)
        self.assertEqual(s.changes(self.old, new, self.today), [])

    def test_published_dates_differing_from_prognosis(self):
        text = PDF_TEXT.replace("ons 30 dec 2026", "ons 30 dec 2026 ons 13 jan 2027")
        new = s.build_bins(SERVICES, s.parse_pdf_text(text, WT), self.old, self.today)
        self.assertEqual(s.changes(self.old, new, self.today),
                         ["Mat/Rest: flyttad från ons 6 jan 2027 till ons 13 jan 2027."])

    def test_new_rule_and_placement(self):
        services = [{**SERVICES[0], "WastePickupFrequency": "Torsdag udda vecka",
                     "BinDirection": "Handtag och hjul ut mot vägen"}] + SERVICES[1:]
        new = s.build_bins(services, s.parse_pdf_text(PDF_TEXT, WT), self.old, self.today)
        msgs = s.changes(self.old, new, self.today)
        self.assertIn("Mat/Rest: ny tömningsregel, Torsdag udda vecka (förut Onsdag udda vecka).", msgs)
        self.assertIn("Mat/Rest: kärlet ska nu ställas med handtag och hjul ut mot vägen "
                      "(förut handtag och hjul vänt mot fastighet).", msgs)


class Reminders(unittest.TestCase):
    def setUp(self):
        self.bins = s.build_bins(SERVICES, s.parse_pdf_text(PDF_TEXT, WT), {}, D(2026, 11, 26))

    def test_evening_before(self):
        today = D(2026, 12, 1)
        now = dt.datetime(2026, 12, 1, 15, 0, tzinfo=s.TZ)
        title, msg, at = s.reminder(self.bins, today, now, dt.time(19))
        self.assertEqual(title, "Sophämtning i morgon, ons 2 dec")
        self.assertEqual(msg, "Mat/Rest: ställ ut kärlet med handtag och hjul vänt mot fastighet.")
        self.assertEqual(at, dt.datetime(2026, 12, 1, 19, 0, tzinfo=s.TZ))

    def test_late_run_sends_immediately(self):
        now = dt.datetime(2026, 12, 1, 20, 30, tzinfo=s.TZ)
        self.assertIsNone(s.reminder(self.bins, D(2026, 12, 1), now, dt.time(19))[2])

    def test_no_pickup_tomorrow(self):
        now = dt.datetime(2026, 12, 2, 15, 0, tzinfo=s.TZ)
        self.assertIsNone(s.reminder(self.bins, D(2026, 12, 2), now, dt.time(19)))


class Calendar(unittest.TestCase):
    def test_ics(self):
        bins = s.build_bins(SERVICES, s.parse_pdf_text(PDF_TEXT, WT), {}, D(2026, 11, 26))
        ics = s.make_ics(bins, "20261126T120000Z", dt.time(19))
        self.assertTrue(ics.startswith("BEGIN:VCALENDAR\r\n"))
        self.assertTrue(all(len(line.encode()) <= 75 for line in ics.split("\r\n")))
        unfolded = ics.replace("\r\n ", "")
        self.assertIn("UID:20261202-mat-rest@sophamtning", unfolded)
        self.assertIn("SUMMARY:Sophämtning: Mat/Rest (prel.)", unfolded)  # jan 2027
        self.assertIn("TRIGGER:-PT5H", unfolded)
        self.assertIn("hjul vänt mot fastighet.", unfolded)

    def test_trigger_with_minutes(self):
        bins = s.build_bins(SERVICES, s.parse_pdf_text(PDF_TEXT, WT), {}, D(2026, 11, 26))
        self.assertIn("TRIGGER:-PT5H30M", s.make_ics(bins, "x", dt.time(18, 30)))


class EndToEnd(unittest.TestCase):
    """Kör main() flera dagar i rad med låtsad hämtning och fångar notiserna."""

    def run_day(self, day, services=SERVICES, text=PDF_TEXT, fail=False, flags=(), env=None):
        def fake_fetch(search, old_bins, today):
            if fail:
                raise s.FetchError("sajten svarar inte")
            return s.build_bins(services, s.parse_pdf_text(text, WT), old_bins, today), []
        sent = []
        with mock.patch.object(s, "fetch", fake_fetch), \
             mock.patch.object(s, "notify", lambda title, msg, **k: sent.append((title, msg, k))), \
             mock.patch.dict("os.environ", env if env is not None else {"SOPHAMTNING_ADRESS": "Testvägen 1"},
                             clear=env is not None):
            code = s.main(["--idag", day.isoformat(), *flags])
        return code, sent

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.patches = [mock.patch.object(s, "STATE_FILE", d / "state.json"),
                        mock.patch.object(s, "ICS_FILE", d / "sophamtning.ics"),
                        mock.patch.object(s, "PAMINDA_FILE", d / "paminda.json")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_sequence(self):
        code, sent = self.run_day(D(2026, 11, 26))
        self.assertEqual(code, 0)
        self.assertEqual([t for t, *_ in sent], ["Sophämtning: påminnelser igång"])
        state = json.loads(s.STATE_FILE.read_text(encoding="utf-8"))
        self.assertNotIn("Testvägen", json.dumps(state, ensure_ascii=False))
        self.assertNotIn("Testvägen", s.ICS_FILE.read_text(encoding="utf-8"))

        _, sent = self.run_day(D(2026, 11, 30))
        self.assertEqual(sent, [])

        _, sent = self.run_day(D(2026, 12, 1))
        self.assertEqual([t for t, *_ in sent], ["Sophämtning i morgon, ons 2 dec"])
        _, sent = self.run_day(D(2026, 12, 1))  # andra körningen samma dag
        self.assertEqual(sent, [])

        moved = PDF_TEXT.replace("ons 16 dec 2026", "tor 17 dec 2026")
        _, sent = self.run_day(D(2026, 12, 3), text=moved)
        self.assertEqual(sent[0][0], "Sophämtning: schemat har ändrats")
        self.assertIn("flyttad från ons 16 dec till tor 17 dec", sent[0][1])

    def test_failures_alert_once_and_keep_reminding(self):
        self.run_day(D(2026, 11, 26))
        alerts = []
        for i, day in enumerate([D(2026, 11, 28), D(2026, 11, 28), D(2026, 11, 29), D(2026, 11, 29),
                                 D(2026, 11, 30), D(2026, 12, 1)]):
            code, sent = self.run_day(day, fail=True)
            alerts += [t for t, *_ in sent]
        self.assertEqual(alerts.count("Sophämtning: schemat kunde inte läsas"), 1)
        self.assertIn("Sophämtning i morgon, ons 2 dec", alerts)  # påminner från sparat schema
        self.assertEqual(code, 1)
        _, sent = self.run_day(D(2026, 12, 2))
        self.assertIn("Sophämtning: schemat läses igen", [t for t, *_ in sent])

    def test_split_modes(self):
        # Macen hämtar, GitHub påminner utan adress och utan kontakt med sajten.
        _, sent = self.run_day(D(2026, 11, 26), flags=["--hamta"])
        self.assertEqual([t for t, *_ in sent], ["Sophämtning: påminnelser igång"])
        self.assertFalse(s.PAMINDA_FILE.exists())
        _, sent = self.run_day(D(2026, 12, 1), flags=["--hamta"])
        self.assertEqual(sent, [])
        with mock.patch.object(s, "fetch", side_effect=AssertionError("ska inte hämta")):
            code, sent = self.run_day(D(2026, 12, 1), flags=["--paminn"], env={})
        self.assertEqual(code, 0)
        self.assertEqual([t for t, *_ in sent], ["Sophämtning i morgon, ons 2 dec"])
        self.assertEqual(json.loads(s.PAMINDA_FILE.read_text())["paminda"], ["2026-12-02"])

    def test_stale_schedule_alerts_once_and_keeps_reminding(self):
        self.run_day(D(2026, 11, 26), flags=["--hamta"])
        titles = []
        for day in [D(2026, 11, 28), D(2026, 11, 29), D(2026, 11, 30), D(2026, 12, 1)]:
            _, sent = self.run_day(day, flags=["--paminn"], env={})
            titles += [t for t, *_ in sent]
        self.assertEqual(titles, ["Sophämtning: schemat har inte uppdaterats",
                                  "Sophämtning i morgon, ons 2 dec"])


if __name__ == "__main__":
    unittest.main()
