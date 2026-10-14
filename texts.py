"""Human-facing texts. The language is chosen with the `language` setting in config.json.

To add a language, copy the "en" block under a new code and translate the values.
Keys and {placeholders} must stay the same — test_power_bot.py checks that.
"""

TEXTS = {
    "uk": {
        # alerts
        "lost": "🔴 Світло зникло ({when})\nБудинок на акумуляторі: {batt} В, навантаження {load} Вт",
        "back": "🟢 Світло повернулося ({when})\nМережа {volts} В{took}",
        "took": " · не було {dur}",
        "noticed": "помічено о {time}",
        "blind_part": "; даних не було з {time}, точний момент невідомий",
        "link_back": "✅ Дані про світло знову надходять: {state}, {volts} В",
        "link_lost": "⚠️ Немає даних про світло {since}: {why}",
        "since_time": "з {time}",
        "since_start": "з моменту запуску бота",
        "no_new_points": "інвертор не надсилає нових точок",
        "last_known_on": "\nОстаннє відоме: світло було",
        "last_known_off": "\nОстаннє відоме: світла не було",
        "blind_tail": "\nПоки дані не надходять, бот не дізнається про зникнення світла",
        "late": "🕓 із запізненням, це було о {time}\n",
        "state_on": "світло є",
        "state_off": "світла немає",
        # /power
        "loading": "⏳ Дані про світло ще завантажуються, спробуй за хвилину",
        "no_data": "⚠️ Немає даних про світло: {error}",
        "status_on": "🟢 Світло є — {volts} В",
        "status_hz": ", {hz} Гц",
        "status_off": "🔴 Світла немає — на вході {volts} В",
        "status_since_on": "Є з {time}, уже {dur}",
        "status_since_off": "Немає з {time}, уже {dur}",
        "status_batt": "🔋 АКБ {batt} В · навантаження {load} Вт",
        "status_at": "Дані станом на {time}",
        "status_stale": "⚠️ Нових даних немає: {error}",
        "inverter_silent": "інвертор мовчить",
        "help": "Стежу за світлом за даними інвертора і пишу, коли мережа зникає і повертається.\n"
                "/power — чи є світло зараз",
        "cmd_power": "Чи є зараз світло",
        "chat_id": "Chat id цього чату: {chat_id}\nВпиши його в config.json (telegram_chat_id) і перезапусти бота.",
        "test_head": "🧪 Тестове повідомлення power-outage-bot\n",
        # durations
        "min": "{m} хв",
        "hours": "{h} год",
        "hours_min": "{h} год {m} хв",
        # errors
        "cloud_rejected": "хмара відмовила (код {code}): {desc}",
        "cloud_rejected_bare": "хмара відмовила (код {code})",
        "retry_at": "{error}; наступна спроба о {time}",
        "cloud_timeout": "хмара недоступна: тайм-аут",
        "cloud_offline": "хмара недоступна: немає зв’язку",
        "cloud_http": "хмара недоступна: HTTP {status}",
        "no_voltage": "у відповіді хмари немає напруги мережі — запусти з --check і задай grid_voltage_field",
        "no_devices": "в акаунті немає жодного пристрою",
        "no_device_pn": "пристрою з device_pn={pn} немає в акаунті",
        # config
        "cfg_missing": "Немає config.json. Скопіюй config.example.json у config.json і заповни — див. README.",
        "cfg_not_json": "config.json — не JSON: {error}",
        "cfg_empty": "У config.json не заповнено: {fields}",
        # --check and --test
        "chk_token": "Telegram-токен: ",
        "chk_token_ok": "працює (@{name})",
        "chk_token_bad": "відхилено: {desc}",
        "chk_tg_offline": "Telegram недоступний: {error}",
        "chk_chat_ok": "Чат: знайдено",
        "chk_chat_bad": "Чат: бот не може туди писати ({desc}) — спершу напиши боту /start",
        "chk_chat_offline": "Чат: не перевірено, Telegram недоступний ({error})",
        "chk_chat_unset": "Чат: telegram_chat_id не задано — запусти бота й напиши йому /start, він підкаже",
        "chk_cloud": "Хмара dessmonitor: ",
        "chk_cloud_ok": "вхід працює, пристроїв: {n}",
        "chk_params": "\nПараметри пристрою {pn}:",
        "chk_no_voltage": "\nНапругу мережі не знайдено. Вибери рядок зі списку вище і впиши його назву "
                          "в config.json як grid_voltage_field.",
        "chk_voltage": "\nНапруга мережі: «{name}» = {volts} В → {verdict}",
        "test_sent": "надіслано",
        "test_failed": "не надіслано — дивись power_bot.log",
        # log
        "log_started": "=== power-outage-bot запущено ===",
        "log_state_read": "не прочитав {file}: {error}",
        "log_state_write": "не зберіг {file}: {error}",
        "log_no_chat": "telegram_chat_id не задано — сповіщення нікуди надсилати: {text}",
        "log_many_devices": "в акаунті {n} пристроїв, узято перший; інший — через device_pn",
        "log_cloud_error": "хмара: {error}",
        "log_cloud_ok": "хмара: знову працює",
        "log_alert": "сповіщення: {text}",
        "log_dropped": "не доставлено за 6 год, викинуто: {text}",
        "log_poll_error": "опитування: {error}",
        "log_conflict": "цей токен уже слухає інша програма — вимкни її або постав listen_commands: false",
        "log_commands_error": "команди: {error}",
    },
    "en": {
        "lost": "🔴 Power is out ({when})\nRunning on battery: {batt} V, load {load} W",
        "back": "🟢 Power is back ({when})\nGrid {volts} V{took}",
        "took": " · was out for {dur}",
        "noticed": "noticed at {time}",
        "blind_part": "; no data since {time}, exact moment unknown",
        "link_back": "✅ Power data is coming in again: {state}, {volts} V",
        "link_lost": "⚠️ No power data {since}: {why}",
        "since_time": "since {time}",
        "since_start": "since the bot started",
        "no_new_points": "the inverter is not sending new data points",
        "last_known_on": "\nLast known: power was on",
        "last_known_off": "\nLast known: power was off",
        "blind_tail": "\nUntil data comes back, the bot won't know about outages",
        "late": "🕓 delayed, this happened at {time}\n",
        "state_on": "power is on",
        "state_off": "power is off",
        "loading": "⏳ Power data is still loading, try again in a minute",
        "no_data": "⚠️ No power data: {error}",
        "status_on": "🟢 Power is on — {volts} V",
        "status_hz": ", {hz} Hz",
        "status_off": "🔴 Power is off — input {volts} V",
        "status_since_on": "On since {time}, for {dur}",
        "status_since_off": "Off since {time}, for {dur}",
        "status_batt": "🔋 Battery {batt} V · load {load} W",
        "status_at": "Data as of {time}",
        "status_stale": "⚠️ No new data: {error}",
        "inverter_silent": "the inverter is silent",
        "help": "I watch the grid using inverter data and message you when power goes out and comes back.\n"
                "/power — is there power right now",
        "cmd_power": "Is there power right now",
        "chat_id": "This chat's id: {chat_id}\nPut it into config.json (telegram_chat_id) and restart the bot.",
        "test_head": "🧪 power-outage-bot test message\n",
        "min": "{m} min",
        "hours": "{h} h",
        "hours_min": "{h} h {m} min",
        "cloud_rejected": "cloud refused (code {code}): {desc}",
        "cloud_rejected_bare": "cloud refused (code {code})",
        "retry_at": "{error}; next attempt at {time}",
        "cloud_timeout": "cloud unreachable: timeout",
        "cloud_offline": "cloud unreachable: no connection",
        "cloud_http": "cloud unreachable: HTTP {status}",
        "no_voltage": "the cloud response has no grid voltage — run with --check and set grid_voltage_field",
        "no_devices": "no devices in the account",
        "no_device_pn": "no device with device_pn={pn} in the account",
        "cfg_missing": "config.json not found. Copy config.example.json to config.json and fill it in — see README.",
        "cfg_not_json": "config.json is not valid JSON: {error}",
        "cfg_empty": "Not filled in config.json: {fields}",
        "chk_token": "Telegram token: ",
        "chk_token_ok": "works (@{name})",
        "chk_token_bad": "rejected: {desc}",
        "chk_tg_offline": "Telegram unreachable: {error}",
        "chk_chat_ok": "Chat: found",
        "chk_chat_bad": "Chat: the bot can't write there ({desc}) — send the bot /start first",
        "chk_chat_offline": "Chat: not checked, Telegram unreachable ({error})",
        "chk_chat_unset": "Chat: telegram_chat_id is not set — start the bot and send it /start, it will tell you",
        "chk_cloud": "dessmonitor cloud: ",
        "chk_cloud_ok": "login works, devices: {n}",
        "chk_params": "\nDevice {pn} parameters:",
        "chk_no_voltage": "\nGrid voltage not found. Pick a row from the list above and put its name "
                          "into config.json as grid_voltage_field.",
        "chk_voltage": "\nGrid voltage: “{name}” = {volts} V → {verdict}",
        "test_sent": "sent",
        "test_failed": "not sent — see power_bot.log",
        "log_started": "=== power-outage-bot started ===",
        "log_state_read": "couldn't read {file}: {error}",
        "log_state_write": "couldn't save {file}: {error}",
        "log_no_chat": "telegram_chat_id is not set — nowhere to send the alert: {text}",
        "log_many_devices": "{n} devices in the account, using the first one; pick another with device_pn",
        "log_cloud_error": "cloud: {error}",
        "log_cloud_ok": "cloud: working again",
        "log_alert": "alert: {text}",
        "log_dropped": "not delivered within 6 h, dropped: {text}",
        "log_poll_error": "polling: {error}",
        "log_conflict": "another program is already listening to this token — stop it or set listen_commands: false",
        "log_commands_error": "commands: {error}",
    },
}
DEFAULT_LANGUAGE = "uk"
_current = {"lang": DEFAULT_LANGUAGE}


def set_language(lang):
    if lang not in TEXTS:
        raise ValueError(f"language: {' / '.join(TEXTS)}")
    _current["lang"] = lang


def language():
    return _current["lang"]


def t(key, **values):
    return TEXTS[_current["lang"]][key].format(**values)
