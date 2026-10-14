"""Client for the dessmonitor (Eybond) cloud.

Eybond WiFi modules, found in many hybrid inverters (Anenji, EASUN, PowMr, MUST and others),
push their data there; people watch it in the SmartESS and DESS Monitor apps.

Request signing follows the public API docs:
    login:      sign = SHA1(salt + SHA1(password) + query)
    the rest:   sign = SHA1(salt + secret + token + query)
The query string must go into the URL byte for byte, so parameters are not URL-encoded.
"""
import hashlib
import time

import requests

API_URL = "https://api.dessmonitor.com/public/"
# Company key of the dessmonitor.com web client: works for SmartESS / DESS Monitor accounts
DEFAULT_COMPANY_KEY = "VRZXQzNDGM4jkwOo"
APP_SUFFIX = "&_app_client_=web&_app_id_=dessmonitor.com&_app_version_=3.0"
TOKEN_ERRORS = {4, 5, 6, 10007, 10008}  # token expired or revoked — log in again

# Words that identify the grid input voltage. Names depend on the firmware.
GRID_HINTS = ("grid", "mains", "utility", "ac input", "line")
# Words that mean the parameter is definitely not the grid input
NOT_GRID_HINTS = ("pv", "solar", "batt", "output", "load", "inv", "bus",
                  "frequency", "power", "current", "charg")


class CloudRejected(Exception):
    """The cloud answered but refused: wrong login, key, token and so on."""

    def __init__(self, code, desc):
        # the bot builds the human text in the chosen language; here it's only the cloud's code and description
        super().__init__(f"code {code}: {desc}" if desc else f"code {code}")
        self.code = code
        self.desc = desc


class LoginRejected(CloudRejected):
    """The cloud refused the login and password — retrying right away is pointless and risks a lockout."""


def _sha1(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


class Client:
    def __init__(self, user, password, company_key=DEFAULT_COMPANY_KEY, timeout=20):
        self._user = user
        self._password = password
        self._company_key = company_key or DEFAULT_COMPANY_KEY
        self._timeout = timeout
        self._http = requests.Session()
        self._token = None
        self._secret = None
        self._expires = 0.0

    def _get(self, url):
        # never log the URL: it contains the token
        resp = self._http.get(url, timeout=self._timeout)
        resp.raise_for_status()
        data = resp.json()
        code = int(data.get("err", -1))
        if code != 0:
            raise CloudRejected(code, str(data.get("desc", "")))
        return data.get("dat") or {}

    def login(self):
        salt = str(int(time.time() * 1000))
        query = (f"&action=authSource&usr={self._user}&company-key={self._company_key}"
                 f"&source=1{APP_SUFFIX}")
        try:
            dat = self._get(f"{API_URL}?sign={_sha1(salt + _sha1(self._password) + query)}&salt={salt}{query}")
        except CloudRejected as e:
            raise LoginRejected(e.code, e.desc) from None
        if not dat.get("token") or not dat.get("secret"):
            raise LoginRejected(-1, "no token in response")
        self._token, self._secret = dat["token"], dat["secret"]
        # the token lives a week; refresh a day early so it never expires mid-flight
        self._expires = time.time() + max(int(dat.get("expire", 604800)) - 86400, 3600)

    def _call_once(self, action, params):
        salt = str(int(time.time() * 1000))
        query = f"&action={action}" + "".join(f"&{k}={v}" for k, v in params.items())
        sign = _sha1(salt + self._secret + self._token + query)
        return self._get(f"{API_URL}?sign={sign}&salt={salt}&token={self._token}{query}")

    def call(self, action, **params):
        if not self._token or time.time() >= self._expires:
            self.login()
        try:
            return self._call_once(action, params)
        except CloudRejected as e:
            if e.code not in TOKEN_ERRORS:
                raise
            self.login()  # the token was revoked early — one retry with a fresh one
            return self._call_once(action, params)

    def devices(self):
        dat = self.call("webQueryDeviceEs", page=0, pagesize=50, i18n="en_US")
        return [d for d in (dat.get("device") or []) if isinstance(d, dict)]

    def last_data(self, device):
        return self.call("querySPDeviceLastData", i18n="en_US", pn=device.get("pn", ""),
                         devcode=device.get("devcode", ""), devaddr=device.get("devaddr", ""),
                         sn=device.get("sn", ""))


# ─── Parsing the response ─────────────────────────────────────
def parameters(dat):
    """Flat list of parameters [{id, par, val, unit}, ...]. In the response they sit in groups at varying depth."""
    found = []

    def walk(node):
        if isinstance(node, dict):
            if "par" in node and "val" in node:
                found.append(node)
                return
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(dat)
    return found


def to_float(value):
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _key(param):
    return f"{param.get('id', '')} {param.get('par', '')}".lower()


def find_grid_voltage(params, field=""):
    """(voltage, parameter name) or (None, "").

    field is an explicit parameter name or id from the config; without it we search by hint words.
    """
    for param in params:
        key = _key(param)
        if field:
            if field.lower() not in key:
                continue
        else:
            if not any(h in key for h in GRID_HINTS) or any(b in key for b in NOT_GRID_HINTS):
                continue
            if "volt" not in key and str(param.get("unit", "")).strip().upper() != "V":
                continue
        value = to_float(param.get("val"))
        if value is not None:
            return value, str(param.get("par") or param.get("id"))
    return None, ""


def find_value(params, *names):
    """First numeric value of a parameter whose name matches one of names (case-insensitive)."""
    wanted = {n.lower() for n in names}
    for param in params:
        if str(param.get("par", "")).strip().lower() in wanted:
            value = to_float(param.get("val"))
            if value is not None:
                return value
    return None
