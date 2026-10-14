"""Уведомления в Telegram, когда пропадает и появляется свет — по данным инвертора из облака dessmonitor.

WiFi-модуль инвертора сам отправляет точку в облако (обычно раз в ~5 минут), а бот
раз в poll_seconds забирает последнюю и смотрит напряжение сети на входе. К самому
модулю бот не обращается: веб-сервер у таких модулей слабый и от запросов виснет.

Сеть есть или нет — по напряжению на входе, а не по режиму работы: в приоритете SBU
инвертор питает дом от батарей и при живой сети.

Новая точка = в ответе облака сменился gts. С часами его не сравниваем: облако живёт
в своём часовом поясе, поэтому все моменты в сообщениях — наши, момент опроса.

    python power_bot.py           работать
    python power_bot.py --check   проверить ключи и показать, что отдаёт облако
    python power_bot.py --test    прислать тестовое сообщение в Telegram
"""
import argparse
import json
import logging
import os
import sys
import threading
import time

import requests

import dess

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE, "config.json")
STATE_FILE = os.path.join(BASE, "power_state.json")
LOG_FILE = os.path.join(BASE, "power_bot.log")

DEFAULTS = {
    "telegram_token": "",
    "telegram_chat_id": 0,
    "dess_user": "",
    "dess_password": "",
    "dess_company_key": dess.DEFAULT_COMPANY_KEY,
    "device_pn": "",
    "grid_voltage_field": "",
    "grid_min_volts": 50,
    "poll_seconds": 60,
    "stale_minutes": 20,
    "listen_commands": True,
}
LOGIN_RETRY_SEC = 30 * 60  # облако отказало во входе — не долбим его, иначе можно словить блокировку
PENDING_TTL = 6 * 3600     # недоставленное уведомление пробуем доставить ещё 6 часов

log = logging.getLogger("power_bot")


# ─── Форматирование ───────────────────────────────────────────
def hhmm(ts):
    fmt = "%H:%M" if time.time() - ts < 20 * 3600 else "%d.%m %H:%M"
    return time.strftime(fmt, time.localtime(ts))


def fmt_minutes(minutes):
    h, m = divmod(max(1, round(minutes)), 60)
    if not h:
        return f"{m} мин"
    return f"{h} ч" if not m else f"{h} ч {m} мин"


def fmt_num(value):
    return "?" if value is None else f"{value:g}"


def describe(error):
    """Человеческое описание ошибки. «Отказал» и «недоступен» — разные вещи, путать их нельзя."""
    if isinstance(error, dess.CloudRejected):
        return str(error)
    if isinstance(error, requests.Timeout):
        return "облако недоступно: таймаут"
    if isinstance(error, requests.ConnectionError):
        return "облако недоступно: нет связи"
    if isinstance(error, requests.HTTPError) and error.response is not None:
        return f"облако недоступно: HTTP {error.response.status_code}"
    return f"{type(error).__name__}: {error}"[:160]


# ─── Настройки ────────────────────────────────────────────────
def load_config(path=CONFIG_FILE):
    try:
        with open(path, encoding="utf-8-sig") as f:
            cfg = {**DEFAULTS, **json.load(f)}
    except FileNotFoundError:
        sys.exit("Нет config.json. Скопируй config.example.json в config.json и заполни — см. README.")
    except ValueError as e:
        sys.exit(f"config.json — не JSON: {e}")
    missing = [k for k in ("telegram_token", "dess_user", "dess_password") if not cfg[k]]
    if missing:
        sys.exit("В config.json не заполнено: " + ", ".join(missing))
    return cfg


# ─── Слежение за светом ───────────────────────────────────────
class Monitor:
    """Превращает поток точек из облака в уведомления. Сам в сеть не ходит — его легко проверять."""

    def __init__(self, cfg, state_file=STATE_FILE, started=None):
        self.grid_min = float(cfg["grid_min_volts"])
        self.stale_sec = float(cfg["stale_minutes"]) * 60
        self.field = cfg["grid_voltage_field"]
        self.state_file = state_file
        self.started = time.time() if started is None else started
        self.error = None
        self.lock = threading.Lock()
        self.state = {"grid": None, "since": None, "seen": None, "gts": None, "link_ok": True,
                      "volts": None, "hz": None, "batt": None, "load": None}

    def load(self):
        try:
            with open(self.state_file, encoding="utf-8") as f:
                saved = json.load(f)
        except FileNotFoundError:
            return
        except Exception as e:
            log.error("не прочитал %s: %s", self.state_file, e)
            return
        self.state.update({k: v for k, v in saved.items() if k in self.state})

    def save(self):
        try:
            tmp = self.state_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False)
            os.replace(tmp, self.state_file)
        except Exception as e:
            log.error("не сохранил %s: %s", self.state_file, e)

    def step(self, now, dat, error=None):
        """Результат опроса → список уведомлений. dat — ответ облака, None если опрос не удался."""
        st = self.state
        params = dess.parameters(dat) if dat else []
        volts, _ = dess.find_grid_voltage(params, self.field)
        gts = str(dat.get("gts") or "") if dat else ""
        if error is None and volts is None:
            error = "в ответе облака нет напряжения сети — запусти с --check и задай grid_voltage_field"
        self.error = error

        if error or gts == st["gts"]:
            # после перезапуска отсчитываем от запуска, а не от точки, увиденной до него
            quiet_from = max(st["seen"] or 0, self.started)
            if not st["link_ok"] or now - quiet_from < self.stale_sec:
                return []
            st["link_ok"] = False
            since = f"с {hhmm(st['seen'])}" if st["seen"] else "с запуска бота"
            text = f"⚠️ Нет данных о свете {since}: {error or 'инвертор не присылает новые точки'}"
            if st["grid"] is not None:
                text += f"\nПоследнее известное: {'свет был' if st['grid'] else 'света не было'}"
            return [text + "\nПока данные не пойдут, о пропаже света бот не узнает"]

        texts = []
        grid = volts >= self.grid_min
        batt = dess.find_value(params, "Battery Voltage")
        load = dess.find_value(params, "Output Active Power", "Load Active Power")
        blind_since = st["seen"] if not st["link_ok"] else None
        if st["grid"] is not None and grid != st["grid"]:
            when = f"замечено в {hhmm(now)}"
            if blind_since:
                when += f"; данных не было с {hhmm(blind_since)}, точный момент неизвестен"
            if grid:
                took = f" · не было {fmt_minutes((now - st['since']) / 60)}" if st["since"] else ""
                texts.append(f"🟢 Свет вернулся ({when})\nСеть {volts:.0f} В{took}")
            else:
                texts.append(f"🔴 Свет пропал ({when})\n"
                             f"Дом на аккумуляторе: {fmt_num(batt)} В, нагрузка {fmt_num(load)} Вт")
            st["since"] = now
        elif not st["link_ok"]:
            texts.append(f"✅ Данные о свете снова идут: {'свет есть' if grid else 'света нет'}, {volts:.0f} В")

        st.update(grid=grid, gts=gts, seen=now, link_ok=True, volts=volts, batt=batt, load=load,
                  hz=dess.find_value(params, "Grid Frequency", "AC Input Frequency"))
        return texts

    def status_text(self, now=None):
        now = time.time() if now is None else now
        with self.lock:
            st, err = dict(self.state), self.error
        if st["grid"] is None:
            return f"⚠️ Нет данных о свете: {err}" if err else "⏳ Данные о свете ещё загружаются, попробуй через минуту"
        if st["grid"]:
            lines = [f"🟢 Свет есть — {st['volts']:.0f} В" + (f", {st['hz']:g} Гц" if st["hz"] else "")]
        else:
            lines = [f"🔴 Света нет — на входе {st['volts']:.0f} В"]
        if st["since"]:
            lines.append(f"{'Есть' if st['grid'] else 'Нет'} с {hhmm(st['since'])}, "
                         f"уже {fmt_minutes((now - st['since']) / 60)}")
        if st["batt"] is not None or st["load"] is not None:
            lines.append(f"🔋 АКБ {fmt_num(st['batt'])} В · нагрузка {fmt_num(st['load'])} Вт")
        lines.append(f"Данные на {hhmm(st['seen'])}")
        if err or not st["link_ok"]:
            lines.append(f"⚠️ Новых данных нет: {err or 'инвертор молчит'}")
        return "\n".join(lines)


# ─── Telegram ─────────────────────────────────────────────────
class Telegram:
    def __init__(self, token):
        self.token = token
        self.api = f"https://api.telegram.org/bot{token}"

    def call(self, method, timeout=15, **payload):
        return requests.post(f"{self.api}/{method}", json=payload, timeout=timeout).json()

    def send(self, chat_id, text):
        if not chat_id:
            log.warning("telegram_chat_id не задан — уведомление некуда слать: %s", text.replace("\n", " | "))
            return False
        try:
            resp = self.call("sendMessage", chat_id=chat_id, text=text)
        except Exception as e:
            log.error("sendMessage: %s", type(e).__name__)
            return False
        if not resp.get("ok"):
            log.error("sendMessage: %s", resp.get("description"))
        return bool(resp.get("ok"))


class HideSecrets(logging.Filter):
    # requests кладёт в текст ошибки полный URL — без фильтра токен оседал бы в логе
    def __init__(self, secrets):
        super().__init__()
        self.secrets = [s for s in secrets if s]

    def filter(self, record):
        msg = record.getMessage()
        for secret in self.secrets:
            msg = msg.replace(secret, "<скрыто>")
        record.msg, record.args = msg, ()
        return True


# ─── Опрос облака ─────────────────────────────────────────────
def pick_device(devices, pn=""):
    if not devices:
        raise dess.CloudRejected(-1, "в аккаунте нет ни одного устройства")
    pn = str(pn or "")
    if pn:
        for d in devices:
            if str(d.get("pn")) == pn:
                return d
        raise dess.CloudRejected(-1, f"устройства с device_pn={pn} нет в аккаунте")
    if len(devices) > 1:
        log.warning("в аккаунте %d устройств, взято первое; другое — через device_pn", len(devices))
    return devices[0]


class Poller:
    def __init__(self, cfg, monitor, telegram):
        self.cfg = cfg
        self.monitor = monitor
        self.telegram = telegram
        self.client = dess.Client(cfg["dess_user"], cfg["dess_password"], cfg["dess_company_key"])
        self.device = None
        self.login_after = 0.0
        self.login_error = None  # последний отказ во входе
        self.pending = []  # [(ts, текст)]

    def fetch(self, now):
        if now < self.login_after:
            e = self.login_error
            raise dess.LoginRejected(e.code, f"{e.desc}; следующая попытка в {hhmm(self.login_after)}")
        try:
            if self.device is None:
                self.device = pick_device(self.client.devices(), self.cfg["device_pn"])
            return self.client.last_data(self.device)
        except dess.LoginRejected as e:
            self.login_after, self.login_error = now + LOGIN_RETRY_SEC, e
            raise

    def poll(self):
        now = time.time()
        dat, error = None, None
        try:
            dat = self.fetch(now)
        except Exception as e:
            error = describe(e)
        with self.monitor.lock:
            before = self.monitor.error
            texts = self.monitor.step(now, dat, error)
            # в лог — только смену состояния связи, иначе без сети он пухнет на строку в минуту
            if self.monitor.error and self.monitor.error != before:
                log.error("облако: %s", self.monitor.error)
            elif before and not self.monitor.error:
                log.info("облако: снова работает")
            for text in texts:
                log.info("уведомление: %s", text.replace("\n", " | "))
                self.pending.append((now, text))
            self.monitor.save()
        self.deliver(now)

    def deliver(self, now):
        left = []
        for ts, text in self.pending:
            if now - ts > PENDING_TTL:
                log.error("не доставлено за 6 ч, выброшено: %s", text.replace("\n", " | "))
                continue
            late = f"🕓 с опозданием, это было в {hhmm(ts)}\n" if now - ts > self.cfg["poll_seconds"] * 2 else ""
            if not self.telegram.send(self.cfg["telegram_chat_id"], late + text):
                left.append((ts, text))
        self.pending[:] = left

    def loop(self):
        while True:
            try:
                self.poll()
            except Exception as e:
                log.error("опрос: %s", describe(e))
            time.sleep(self.cfg["poll_seconds"])


# ─── Команды в Telegram ───────────────────────────────────────
HELP = ("Слежу за светом по данным инвертора и пишу, когда сеть пропадает и возвращается.\n"
        "/power — есть ли свет сейчас")


def listen_commands(cfg, telegram, monitor):
    """Long polling: /power, /start. Отвечаем только в настроенный чат; без него — подсказываем chat id."""
    telegram.call("deleteWebhook")
    telegram.call("setMyCommands", commands=[{"command": "power", "description": "Есть ли сейчас свет"}])
    chat_id, offset = cfg["telegram_chat_id"], None
    while True:
        try:
            resp = requests.get(f"{telegram.api}/getUpdates",
                                params={"offset": offset, "timeout": 30}, timeout=40).json()
            if not resp.get("ok"):
                log.error("getUpdates: %s", resp.get("description"))
                if resp.get("error_code") == 409:
                    log.error("этот токен уже слушает другая программа — выключи её или поставь listen_commands: false")
                time.sleep(10)
                continue
            for upd in resp.get("result", []):
                offset = upd["update_id"] + 1
                post = upd.get("message") or upd.get("channel_post")
                if not post or not (post.get("text") or "").startswith("/"):
                    continue
                here = post["chat"]["id"]
                cmd = post["text"].split()[0].lstrip("/").split("@")[0].lower()
                if not chat_id:
                    telegram.send(here, f"Chat id этого чата: {here}\nВпиши его в config.json "
                                        f"(telegram_chat_id) и перезапусти бота.")
                elif here != chat_id:
                    continue  # чужие чаты молча игнорируем
                elif cmd == "power":
                    telegram.send(here, monitor.status_text())
                elif cmd in ("start", "help"):
                    telegram.send(here, HELP)
        except Exception as e:
            log.error("команды: %s", describe(e))
            time.sleep(5)


# ─── Проверки ─────────────────────────────────────────────────
def check(cfg):
    """Проверка ключей и того, что отдаёт облако. «Работает» значит «ключ принят» — не больше."""
    ok = True
    print("Telegram-токен: ", end="", flush=True)
    try:
        me = Telegram(cfg["telegram_token"]).call("getMe")
        if me.get("ok"):
            print(f"работает (@{me['result'].get('username')})")
        else:
            ok = False
            print(f"отклонён: {me.get('description')}")
    except Exception as e:
        ok = False
        print(f"Telegram недоступен: {type(e).__name__}")

    if cfg["telegram_chat_id"]:
        try:
            chat = Telegram(cfg["telegram_token"]).call("getChat", chat_id=cfg["telegram_chat_id"])
            print("Чат: " + ("найден" if chat.get("ok") else f"бот не может писать туда ({chat.get('description')}) "
                                                               f"— сначала напиши боту /start"))
        except Exception as e:
            print(f"Чат: не проверен, Telegram недоступен ({type(e).__name__})")
    else:
        print("Чат: telegram_chat_id не задан — запусти бота и напиши ему /start, он подскажет")

    print("Облако dessmonitor: ", end="", flush=True)
    client = dess.Client(cfg["dess_user"], cfg["dess_password"], cfg["dess_company_key"])
    try:
        devices = client.devices()
        print(f"вход работает, устройств: {len(devices)}")
        device = pick_device(devices, cfg["device_pn"])
        params = dess.parameters(client.last_data(device))
    except Exception as e:
        print(describe(e))
        return False

    print(f"\nПараметры устройства {device.get('pn')}:")
    for p in params:
        print(f"  {str(p.get('par', ''))[:34]:<34} {str(p.get('val', ''))[:16]:>16} {p.get('unit', '')}")
    volts, name = dess.find_grid_voltage(params, cfg["grid_voltage_field"])
    if volts is None:
        print("\nНапряжение сети не найдено. Выбери строку из списка выше и впиши её название "
              "в config.json как grid_voltage_field.")
        return False
    verdict = "свет есть" if volts >= cfg["grid_min_volts"] else "света нет"
    print(f"\nНапряжение сети: «{name}» = {volts:g} В → {verdict}")
    return ok


def main():
    parser = argparse.ArgumentParser(description="Уведомления в Telegram о пропаже и появлении света")
    parser.add_argument("--check", action="store_true", help="проверить ключи и показать данные облака")
    parser.add_argument("--test", action="store_true", help="прислать тестовое сообщение")
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    cfg = load_config()
    handlers = [logging.FileHandler(LOG_FILE, encoding="utf-8")]
    if sys.stdout and not args.check:
        handlers.append(logging.StreamHandler(sys.stdout))
    for handler in handlers:
        handler.addFilter(HideSecrets([cfg["telegram_token"], cfg["dess_password"]]))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", handlers=handlers)

    if args.check:
        sys.exit(0 if check(cfg) else 1)

    telegram = Telegram(cfg["telegram_token"])
    monitor = Monitor(cfg)
    monitor.load()
    poller = Poller(cfg, monitor, telegram)

    if args.test:
        poller.poll()
        sent = telegram.send(cfg["telegram_chat_id"], "🧪 Тестовое сообщение power-outage-bot\n" + monitor.status_text())
        print("отправлено" if sent else "не отправлено — смотри power_bot.log")
        sys.exit(0 if sent else 1)

    log.info("=== power-outage-bot запущен ===")
    if cfg["listen_commands"]:
        threading.Thread(target=poller.loop, daemon=True, name="poll").start()
        listen_commands(cfg, telegram, monitor)
    else:
        poller.loop()


if __name__ == "__main__":
    main()
