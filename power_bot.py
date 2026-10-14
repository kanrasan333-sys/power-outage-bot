"""Telegram alerts when grid power goes out and comes back, based on inverter data from the dessmonitor cloud.

The inverter's WiFi module pushes a data point to the cloud by itself (usually every ~5 minutes);
the bot fetches the latest one every poll_seconds and looks at the grid input voltage. The bot never
talks to the module directly: its web server is weak and hangs under requests.

Power on/off is decided by the grid input voltage, not by the operating mode: with SBU priority
the inverter runs the house from batteries even while the grid is up.

A new point = the cloud's gts value changed. It is never compared with a clock: the cloud lives in
its own time zone, so every moment in the messages is ours — the time of the poll.

All human-facing texts live in texts.py; the language is set with the `language` setting.

    python power_bot.py           run
    python power_bot.py --check   check the keys and show what the cloud returns
    python power_bot.py --test    send a test message to Telegram
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
import texts
from texts import t

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE, "config.json")
STATE_FILE = os.path.join(BASE, "power_state.json")
LOG_FILE = os.path.join(BASE, "power_bot.log")

DEFAULTS = {
    "language": texts.DEFAULT_LANGUAGE,
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
LOGIN_RETRY_SEC = 30 * 60  # after a refused login don't hammer the cloud, or the account may get locked
PENDING_TTL = 6 * 3600     # keep trying to deliver an undelivered alert for 6 more hours

log = logging.getLogger("power_bot")


class SetupError(Exception):
    """A setup problem the bot describes in the chosen language: a texts.py key plus its values."""

    def __init__(self, key, **values):
        super().__init__(key)
        self.key, self.values = key, values


class LoginDeferred(Exception):
    """The cloud recently refused the login — wait until retry_at so the account doesn't get locked."""

    def __init__(self, original, retry_at):
        super().__init__(str(original))
        self.original, self.retry_at = original, retry_at


# ─── Formatting ───────────────────────────────────────────────
def hhmm(ts):
    fmt = "%H:%M" if time.time() - ts < 20 * 3600 else "%d.%m %H:%M"
    return time.strftime(fmt, time.localtime(ts))


def fmt_minutes(minutes):
    h, m = divmod(max(1, round(minutes)), 60)
    if not h:
        return t("min", m=m)
    return t("hours", h=h) if not m else t("hours_min", h=h, m=m)


def fmt_num(value):
    return "?" if value is None else f"{value:g}"


def describe(error):
    """Human description of an error. "Refused" and "unreachable" are different things — never mix them up."""
    if isinstance(error, LoginDeferred):
        return t("retry_at", error=describe(error.original), time=hhmm(error.retry_at))
    if isinstance(error, SetupError):
        return t(error.key, **error.values)
    if isinstance(error, dess.CloudRejected):
        if error.desc:
            return t("cloud_rejected", code=error.code, desc=error.desc)
        return t("cloud_rejected_bare", code=error.code)
    if isinstance(error, requests.Timeout):
        return t("cloud_timeout")
    if isinstance(error, requests.ConnectionError):
        return t("cloud_offline")
    if isinstance(error, requests.HTTPError) and error.response is not None:
        return t("cloud_http", status=error.response.status_code)
    return f"{type(error).__name__}: {error}"[:160]


# ─── Config ───────────────────────────────────────────────────
def load_config(path=CONFIG_FILE):
    try:
        with open(path, encoding="utf-8-sig") as f:
            cfg = {**DEFAULTS, **json.load(f)}
    except FileNotFoundError:
        sys.exit(t("cfg_missing"))
    except ValueError as e:
        sys.exit(t("cfg_not_json", error=e))
    try:
        texts.set_language(str(cfg["language"]).strip().lower())
    except ValueError as e:
        sys.exit(str(e))
    missing = [k for k in ("telegram_token", "dess_user", "dess_password") if not cfg[k]]
    if missing:
        sys.exit(t("cfg_empty", fields=", ".join(missing)))
    return cfg


# ─── Watching the grid ────────────────────────────────────────
class Monitor:
    """Turns the stream of cloud data points into alerts. Does no networking itself, so it's easy to test."""

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
            log.error(t("log_state_read", file=self.state_file, error=e))
            return
        self.state.update({k: v for k, v in saved.items() if k in self.state})

    def save(self):
        try:
            tmp = self.state_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False)
            os.replace(tmp, self.state_file)
        except Exception as e:
            log.error(t("log_state_write", file=self.state_file, error=e))

    def step(self, now, dat, error=None):
        """Poll result → list of alerts. dat is the cloud response, None if the poll failed."""
        st = self.state
        params = dess.parameters(dat) if dat else []
        volts, _ = dess.find_grid_voltage(params, self.field)
        gts = str(dat.get("gts") or "") if dat else ""
        if error is None and volts is None:
            error = t("no_voltage")
        self.error = error

        if error or gts == st["gts"]:
            # after a restart count the silence from the start, not from a point seen before it
            quiet_from = max(st["seen"] or 0, self.started)
            if not st["link_ok"] or now - quiet_from < self.stale_sec:
                return []
            st["link_ok"] = False
            since = t("since_time", time=hhmm(st["seen"])) if st["seen"] else t("since_start")
            text = t("link_lost", since=since, why=error or t("no_new_points"))
            if st["grid"] is not None:
                text += t("last_known_on") if st["grid"] else t("last_known_off")
            return [text + t("blind_tail")]

        found = []
        grid = volts >= self.grid_min
        batt = dess.find_value(params, "Battery Voltage")
        load = dess.find_value(params, "Output Active Power", "Load Active Power")
        blind_since = st["seen"] if not st["link_ok"] else None
        if st["grid"] is not None and grid != st["grid"]:
            when = t("noticed", time=hhmm(now))
            if blind_since:
                when += t("blind_part", time=hhmm(blind_since))
            if grid:
                took = t("took", dur=fmt_minutes((now - st["since"]) / 60)) if st["since"] else ""
                found.append(t("back", when=when, volts=f"{volts:.0f}", took=took))
            else:
                found.append(t("lost", when=when, batt=fmt_num(batt), load=fmt_num(load)))
            st["since"] = now
        elif not st["link_ok"]:
            found.append(t("link_back", state=t("state_on") if grid else t("state_off"), volts=f"{volts:.0f}"))

        st.update(grid=grid, gts=gts, seen=now, link_ok=True, volts=volts, batt=batt, load=load,
                  hz=dess.find_value(params, "Grid Frequency", "AC Input Frequency"))
        return found

    def status_text(self, now=None):
        now = time.time() if now is None else now
        with self.lock:
            st, err = dict(self.state), self.error
        if st["grid"] is None:
            return t("no_data", error=err) if err else t("loading")
        if st["grid"]:
            lines = [t("status_on", volts=f"{st['volts']:.0f}") + (t("status_hz", hz=f"{st['hz']:g}") if st["hz"] else "")]
        else:
            lines = [t("status_off", volts=f"{st['volts']:.0f}")]
        if st["since"]:
            key = "status_since_on" if st["grid"] else "status_since_off"
            lines.append(t(key, time=hhmm(st["since"]), dur=fmt_minutes((now - st["since"]) / 60)))
        if st["batt"] is not None or st["load"] is not None:
            lines.append(t("status_batt", batt=fmt_num(st["batt"]), load=fmt_num(st["load"])))
        lines.append(t("status_at", time=hhmm(st["seen"])))
        if err or not st["link_ok"]:
            lines.append(t("status_stale", error=err or t("inverter_silent")))
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
            log.warning(t("log_no_chat", text=text.replace("\n", " | ")))
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
    # requests puts the full URL into its error text — without this filter the token would end up in the log
    def __init__(self, secrets):
        super().__init__()
        self.secrets = [s for s in secrets if s]

    def filter(self, record):
        msg = record.getMessage()
        for secret in self.secrets:
            msg = msg.replace(secret, "<***>")
        record.msg, record.args = msg, ()
        return True


# ─── Polling the cloud ────────────────────────────────────────
def pick_device(devices, pn=""):
    if not devices:
        raise SetupError("no_devices")
    pn = str(pn or "")
    if pn:
        for d in devices:
            if str(d.get("pn")) == pn:
                return d
        raise SetupError("no_device_pn", pn=pn)
    if len(devices) > 1:
        log.warning(t("log_many_devices", n=len(devices)))
    return devices[0]


class Poller:
    def __init__(self, cfg, monitor, telegram):
        self.cfg = cfg
        self.monitor = monitor
        self.telegram = telegram
        self.client = dess.Client(cfg["dess_user"], cfg["dess_password"], cfg["dess_company_key"])
        self.device = None
        self.login_after = 0.0
        self.login_error = None  # the last refused login
        self.pending = []  # [(ts, text)]

    def fetch(self, now):
        if now < self.login_after:
            raise LoginDeferred(self.login_error, self.login_after)
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
            found = self.monitor.step(now, dat, error)
            # log only changes of the link state, otherwise without network the log grows by a line a minute
            if self.monitor.error and self.monitor.error != before:
                log.error(t("log_cloud_error", error=self.monitor.error))
            elif before and not self.monitor.error:
                log.info(t("log_cloud_ok"))
            for text in found:
                log.info(t("log_alert", text=text.replace("\n", " | ")))
                self.pending.append((now, text))
            self.monitor.save()
        self.deliver(now)

    def deliver(self, now):
        left = []
        for ts, text in self.pending:
            if now - ts > PENDING_TTL:
                log.error(t("log_dropped", text=text.replace("\n", " | ")))
                continue
            late = t("late", time=hhmm(ts)) if now - ts > self.cfg["poll_seconds"] * 2 else ""
            if not self.telegram.send(self.cfg["telegram_chat_id"], late + text):
                left.append((ts, text))
        self.pending[:] = left

    def loop(self):
        while True:
            try:
                self.poll()
            except Exception as e:
                log.error(t("log_poll_error", error=describe(e)))
            time.sleep(self.cfg["poll_seconds"])


# ─── Telegram commands ────────────────────────────────────────
def listen_commands(cfg, telegram, monitor):
    """Long polling: /power, /start. Replies only in the configured chat; without one, tells the chat id."""
    telegram.call("deleteWebhook")
    telegram.call("setMyCommands", commands=[{"command": "power", "description": t("cmd_power")}])
    chat_id, offset = cfg["telegram_chat_id"], None
    while True:
        try:
            resp = requests.get(f"{telegram.api}/getUpdates",
                                params={"offset": offset, "timeout": 30}, timeout=40).json()
            if not resp.get("ok"):
                log.error("getUpdates: %s", resp.get("description"))
                if resp.get("error_code") == 409:
                    log.error(t("log_conflict"))
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
                    telegram.send(here, t("chat_id", chat_id=here))
                elif here != chat_id:
                    continue  # silently ignore other chats
                elif cmd == "power":
                    telegram.send(here, monitor.status_text())
                elif cmd in ("start", "help"):
                    telegram.send(here, t("help"))
        except Exception as e:
            log.error(t("log_commands_error", error=describe(e)))
            time.sleep(5)


# ─── Checks ───────────────────────────────────────────────────
def check(cfg):
    """Checks the keys and what the cloud returns. "Works" means "key accepted" — nothing more."""
    ok = True
    print(t("chk_token"), end="", flush=True)
    try:
        me = Telegram(cfg["telegram_token"]).call("getMe")
        if me.get("ok"):
            print(t("chk_token_ok", name=me["result"].get("username")))
        else:
            ok = False
            print(t("chk_token_bad", desc=me.get("description")))
    except Exception as e:
        ok = False
        print(t("chk_tg_offline", error=type(e).__name__))

    if cfg["telegram_chat_id"]:
        try:
            chat = Telegram(cfg["telegram_token"]).call("getChat", chat_id=cfg["telegram_chat_id"])
            print(t("chk_chat_ok") if chat.get("ok") else t("chk_chat_bad", desc=chat.get("description")))
        except Exception as e:
            print(t("chk_chat_offline", error=type(e).__name__))
    else:
        print(t("chk_chat_unset"))

    print(t("chk_cloud"), end="", flush=True)
    client = dess.Client(cfg["dess_user"], cfg["dess_password"], cfg["dess_company_key"])
    try:
        devices = client.devices()
        print(t("chk_cloud_ok", n=len(devices)))
        device = pick_device(devices, cfg["device_pn"])
        params = dess.parameters(client.last_data(device))
    except Exception as e:
        print(describe(e))
        return False

    print(t("chk_params", pn=device.get("pn")))
    for p in params:
        print(f"  {str(p.get('par', ''))[:34]:<34} {str(p.get('val', ''))[:16]:>16} {p.get('unit', '')}")
    volts, name = dess.find_grid_voltage(params, cfg["grid_voltage_field"])
    if volts is None:
        print(t("chk_no_voltage"))
        return False
    verdict = t("state_on") if volts >= cfg["grid_min_volts"] else t("state_off")
    print(t("chk_voltage", name=name, volts=f"{volts:g}", verdict=verdict))
    return ok


def main():
    parser = argparse.ArgumentParser(description="power-outage-bot")
    parser.add_argument("--check", action="store_true", help="check keys and show what the cloud returns")
    parser.add_argument("--test", action="store_true", help="send a test message")
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
        sent = telegram.send(cfg["telegram_chat_id"], t("test_head") + monitor.status_text())
        print(t("test_sent") if sent else t("test_failed"))
        sys.exit(0 if sent else 1)

    log.info(t("log_started"))
    if cfg["listen_commands"]:
        threading.Thread(target=poller.loop, daemon=True, name="poll").start()
        listen_commands(cfg, telegram, monitor)
    else:
        poller.loop()


if __name__ == "__main__":
    main()
