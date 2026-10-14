"""Logic tests without network or Telegram: python -m unittest -v"""
import logging
import os
import string
import tempfile
import unittest
from unittest import mock

import requests

import dess
import power_bot
import texts

T0 = 1_800_000_000.0
logging.disable(logging.CRITICAL)  # cloud errors in these scenarios are expected; keep the output clean


def point(gts, volts, batt=52.0, load=400, volt_name="Grid Voltage"):
    """A cloud response shaped like querySPDeviceLastData returns it."""
    return {"gts": str(gts), "pars": {
        "gd_": [{"id": "gd_1", "par": volt_name, "val": str(volts), "unit": "V"},
                {"id": "gd_2", "par": "Grid Frequency", "val": "50.0", "unit": "Hz"}],
        "pv_": [{"id": "pv_1", "par": "PV Voltage", "val": "310", "unit": "V"}],
        "bt_": [{"id": "bt_1", "par": "Battery Voltage", "val": str(batt), "unit": "V"}],
        "bc_": [{"id": "bc_1", "par": "Output Voltage", "val": "230", "unit": "V"},
                {"id": "bc_2", "par": "Output Active Power", "val": str(load), "unit": "W"}],
        "sy_": [{"id": "sy_1", "par": "Operating mode", "val": "Off-Grid Mode"}],
    }}


def make_monitor(**overrides):
    cfg = {**power_bot.DEFAULTS, **overrides}
    state = os.path.join(tempfile.mkdtemp(), "state.json")
    return power_bot.Monitor(cfg, state_file=state, started=T0)


def at(minute):
    return T0 + minute * 60


class LanguageCase(unittest.TestCase):
    lang = "uk"

    def setUp(self):
        texts.set_language(self.lang)

    def tearDown(self):
        texts.set_language(texts.DEFAULT_LANGUAGE)


class TextsTest(unittest.TestCase):
    def test_only_ukrainian_and_english(self):
        self.assertEqual(set(texts.TEXTS), {"uk", "en"})
        self.assertEqual(texts.DEFAULT_LANGUAGE, "uk")
        with self.assertRaises(ValueError):
            texts.set_language("xx")

    def test_languages_have_same_keys_and_placeholders(self):
        def fields(template):
            return {name for _, name, _, _ in string.Formatter().parse(template) if name}

        uk, en = texts.TEXTS["uk"], texts.TEXTS["en"]
        self.assertEqual(set(uk), set(en))
        for key in uk:
            self.assertEqual(fields(uk[key]), fields(en[key]), key)


class ParsingTest(LanguageCase):
    def test_finds_grid_voltage_not_pv_or_output(self):
        volts, name = dess.find_grid_voltage(dess.parameters(point(1, 224)))
        self.assertEqual((volts, name), (224.0, "Grid Voltage"))

    def test_other_firmware_names(self):
        for name in ("AC Input Voltage", "Mains Voltage", "Utility voltage", "Line Voltage"):
            volts, _ = dess.find_grid_voltage(dess.parameters(point(1, 221, volt_name=name)))
            self.assertEqual(volts, 221.0, name)

    def test_explicit_field_wins(self):
        params = dess.parameters(point(1, 0, volt_name="Something odd"))
        self.assertEqual(dess.find_grid_voltage(params)[0], None)
        self.assertEqual(dess.find_grid_voltage(params, "something odd")[0], 0.0)

    def test_refused_and_unreachable_are_different(self):
        self.assertIn("відмовила", power_bot.describe(dess.LoginRejected(10003, "wrong password")))
        self.assertIn("недоступна", power_bot.describe(requests.ConnectionError()))
        self.assertIn("недоступна", power_bot.describe(requests.ConnectTimeout()))


class MonitorTest(LanguageCase):
    def setUp(self):
        super().setUp()
        self.m = make_monitor()

    def step(self, minute, dat=None, error=None):
        return self.m.step(at(minute), dat, error)

    def test_outage_and_restore(self):
        self.assertEqual(self.step(0, point(1, 224)), [], "first point — no alert")
        self.assertEqual(self.step(1, point(1, 224)), [], "same point")
        self.assertEqual(self.step(5, point(2, 219)), [], "SBU: Off-Grid mode with grid up is not an outage")
        lost = self.step(10, point(3, 0))
        self.assertEqual(len(lost), 1)
        self.assertTrue(lost[0].startswith("🔴 Світло зникло"))
        self.assertIn("52 В", lost[0])
        self.assertEqual(self.step(15, point(4, 0)), [])
        back = self.step(47, point(5, 225))
        self.assertTrue(back[0].startswith("🟢 Світло повернулося"))
        self.assertIn("не було 37 хв", back[0])

    def test_link_loss_once_then_restore(self):
        self.step(0, point(1, 224))
        self.assertEqual(self.step(5, error="no connection"), [])
        lost = self.step(21, error="no connection")
        self.assertTrue(lost[0].startswith("⚠️ Немає даних про світло з"))
        self.assertIn("світло було", lost[0])
        self.assertEqual(self.step(40, error="no connection"), [], "not repeated")
        self.assertTrue(self.step(45, point(2, 223))[0].startswith("✅"))

    def test_stuck_point_counts_as_link_loss(self):
        self.step(0, point(1, 224))
        self.assertTrue(self.step(21, point(1, 224))[0].startswith("⚠️"))

    def test_change_while_blind_says_moment_unknown(self):
        self.step(0, point(1, 224))
        self.step(21, error="timeout")
        text = self.step(30, point(2, 0))[0]
        self.assertTrue(text.startswith("🔴"))
        self.assertIn("точний момент невідомий", text)

    def test_missing_voltage_is_an_error_not_an_outage(self):
        self.step(0, point(1, 224))
        self.assertEqual(self.step(5, {"gts": "2", "pars": {}}), [])
        self.assertIn("grid_voltage_field", self.m.error)

    def test_restart_does_not_alert_on_old_point(self):
        self.step(0, point(1, 224))
        self.m.save()
        fresh = power_bot.Monitor({**power_bot.DEFAULTS}, state_file=self.m.state_file, started=at(600))
        fresh.load()
        self.assertEqual(fresh.step(at(605), None, "timeout"), [], "silence is counted from the restart")
        self.assertEqual(fresh.step(at(606), point(2, 230)), [], "same state — no alert")
        self.assertTrue(fresh.step(at(610), point(3, 0))[0].startswith("🔴"))

    def test_status_text(self):
        self.assertIn("завантажуються", self.m.status_text())
        self.step(0, point(1, 224))
        self.step(10, point(2, 0))
        text = self.m.status_text(now=at(40))
        self.assertTrue(text.startswith("🔴 Світла немає"))
        self.assertIn("уже 30 хв", text)


class EnglishTest(LanguageCase):
    lang = "en"

    def test_alerts_and_status_in_english(self):
        m = make_monitor()
        m.step(at(0), point(1, 224))
        self.assertTrue(m.step(at(10), point(2, 0))[0].startswith("🔴 Power is out"))
        self.assertIn("Off since", m.status_text(now=at(40)))
        back = m.step(at(95), point(3, 226))[0]
        self.assertTrue(back.startswith("🟢 Power is back"))
        self.assertIn("was out for 1 h 25 min", back)
        self.assertIn("unreachable", power_bot.describe(requests.ConnectionError()))


class FakeTelegram:
    def __init__(self):
        self.online, self.sent = False, []

    def send(self, chat_id, text):
        if self.online:
            self.sent.append(text)
        return self.online


class PollerTest(LanguageCase):
    def make(self):
        cfg = {**power_bot.DEFAULTS, "telegram_chat_id": 1, "dess_user": "u", "dess_password": "p"}
        tg = FakeTelegram()
        return power_bot.Poller(cfg, make_monitor(), tg), tg

    def test_undelivered_alert_is_retried_with_late_mark(self):
        poller, tg = self.make()
        answers = iter([point(1, 224), point(2, 0), point(2, 0)])
        poller.fetch = lambda now: next(answers)
        clock = {"t": at(0)}
        with mock.patch("power_bot.time.time", lambda: clock["t"]):
            poller.poll()
            clock["t"] = at(5)
            poller.poll()  # power went out while Telegram is unreachable
            self.assertEqual(len(poller.pending), 1)
            tg.online, clock["t"] = True, at(10)
            poller.poll()
        self.assertEqual(len(tg.sent), 1)
        self.assertTrue(tg.sent[0].startswith("🕓 із запізненням"))
        self.assertEqual(poller.pending, [])

    def test_login_rejection_backs_off(self):
        poller, _ = self.make()
        calls = []

        def devices():
            calls.append(1)
            raise dess.LoginRejected(10003, "wrong password")

        poller.client.devices = devices
        clock = {"t": at(0)}
        with mock.patch("power_bot.time.time", lambda: clock["t"]):
            poller.poll()
            clock["t"] = at(1)
            poller.poll()  # a minute later — no attempt
            self.assertEqual(len(calls), 1)
            clock["t"] = at(31)
            poller.poll()  # after 30 minutes — try again
        self.assertEqual(len(calls), 2)
        self.assertEqual(poller.monitor.error.count("відмовила"), 1)


if __name__ == "__main__":
    unittest.main()
