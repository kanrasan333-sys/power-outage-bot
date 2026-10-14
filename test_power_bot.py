"""Проверка логики без сети и без Telegram: python -m unittest -v"""
import logging
import os
import tempfile
import unittest
from unittest import mock

import requests

import dess
import power_bot

T0 = 1_800_000_000.0
logging.disable(logging.CRITICAL)  # ошибки облака в сценариях — ожидаемые, не засоряем вывод


def point(gts, volts, batt=52.0, load=400, volt_name="Grid Voltage"):
    """Ответ облака в том виде, в каком его отдаёт querySPDeviceLastData."""
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


class ParsingTest(unittest.TestCase):
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

    def test_rejected_and_unavailable_are_different(self):
        self.assertIn("отказало", power_bot.describe(dess.LoginRejected(10003, "wrong password")))
        self.assertIn("недоступно", power_bot.describe(requests.ConnectionError()))
        self.assertIn("недоступно", power_bot.describe(requests.ConnectTimeout()))


class MonitorTest(unittest.TestCase):
    def setUp(self):
        self.m = make_monitor()

    def step(self, minute, dat=None, error=None):
        return self.m.step(at(minute), dat, error)

    def test_outage_and_restore(self):
        self.assertEqual(self.step(0, point(1, 224)), [], "первая точка — без уведомления")
        self.assertEqual(self.step(1, point(1, 224)), [], "та же точка")
        self.assertEqual(self.step(5, point(2, 219)), [], "SBU: режим Off-Grid при живой сети — не авария")
        lost = self.step(10, point(3, 0))
        self.assertEqual(len(lost), 1)
        self.assertTrue(lost[0].startswith("🔴 Свет пропал"))
        self.assertIn("52 В", lost[0])
        self.assertEqual(self.step(15, point(4, 0)), [])
        back = self.step(47, point(5, 225))
        self.assertTrue(back[0].startswith("🟢 Свет вернулся"))
        self.assertIn("не было 37 мин", back[0])

    def test_link_loss_once_then_restore(self):
        self.step(0, point(1, 224))
        self.assertEqual(self.step(5, error="облако недоступно: нет связи"), [])
        lost = self.step(21, error="облако недоступно: нет связи")
        self.assertTrue(lost[0].startswith("⚠️ Нет данных о свете с"))
        self.assertIn("свет был", lost[0])
        self.assertEqual(self.step(40, error="облако недоступно: нет связи"), [], "не повторяем")
        self.assertTrue(self.step(45, point(2, 223))[0].startswith("✅"))

    def test_stuck_point_counts_as_link_loss(self):
        self.step(0, point(1, 224))
        self.assertTrue(self.step(21, point(1, 224))[0].startswith("⚠️"))

    def test_change_while_blind_says_moment_unknown(self):
        self.step(0, point(1, 224))
        self.step(21, error="таймаут")
        text = self.step(30, point(2, 0))[0]
        self.assertTrue(text.startswith("🔴"))
        self.assertIn("точный момент неизвестен", text)

    def test_missing_voltage_is_an_error_not_an_outage(self):
        self.step(0, point(1, 224))
        self.assertEqual(self.step(5, {"gts": "2", "pars": {}}), [])
        self.assertIn("grid_voltage_field", self.m.error)

    def test_restart_does_not_alert_on_old_point(self):
        self.step(0, point(1, 224))
        self.m.save()
        fresh = power_bot.Monitor({**power_bot.DEFAULTS}, state_file=self.m.state_file, started=at(600))
        fresh.load()
        self.assertEqual(fresh.step(at(605), None, "таймаут"), [], "отсчёт тишины — от перезапуска")
        self.assertEqual(fresh.step(at(606), point(2, 230)), [], "состояние то же — молчим")
        self.assertTrue(fresh.step(at(610), point(3, 0))[0].startswith("🔴"))

    def test_status_text(self):
        self.assertIn("загружаются", self.m.status_text())
        self.step(0, point(1, 224))
        self.step(10, point(2, 0))
        text = self.m.status_text(now=at(40))
        self.assertTrue(text.startswith("🔴 Света нет"))
        self.assertIn("уже 30 мин", text)


class FakeTelegram:
    def __init__(self):
        self.online, self.sent = False, []

    def send(self, chat_id, text):
        if self.online:
            self.sent.append(text)
        return self.online


class PollerTest(unittest.TestCase):
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
            poller.poll()  # свет пропал, а Telegram недоступен
            self.assertEqual(len(poller.pending), 1)
            tg.online, clock["t"] = True, at(10)
            poller.poll()
        self.assertEqual(len(tg.sent), 1)
        self.assertTrue(tg.sent[0].startswith("🕓 с опозданием"))
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
            poller.poll()  # через минуту — не пытаемся
            self.assertEqual(len(calls), 1)
            clock["t"] = at(31)
            poller.poll()  # через 30 мин — снова пробуем
        self.assertEqual(len(calls), 2)
        self.assertEqual(poller.monitor.error.count("отказало"), 1)


if __name__ == "__main__":
    unittest.main()
