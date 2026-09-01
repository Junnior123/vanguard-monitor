import json
import os
import urllib.request

API_URL = "https://clientconfig.rpg.riotgames.com/api/v1/config/public"
STATE_FILE = "last_version.txt"

webhook = os.environ["DISCORD_WEBHOOK"]


def get_current_version():
    req = urllib.request.Request(
        API_URL,
        headers={"User-Agent": "Vanguard-Monitor"}
    )

    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.load(r)

    return data["anticheat.vanguard.version"]


def send_discord(old, new):
    message = {
        "content":
            "🚨 **Riot Vanguard 업데이트 감지**\n\n"
            f"이전 버전: `{old}`\n"
            f"새 버전: `{new}`"
    }

    req = urllib.request.Request(
        webhook,
        data=json.dumps(message).encode(),
        headers={"Content-Type": "application/json"}
    )

    urllib.request.urlopen(req).read()


current = get_current_version()

try:
    with open(STATE_FILE, "r") as f:
        previous = f.read().strip()
except FileNotFoundError:
    previous = ""


if previous and previous != current:
    send_discord(previous, current)


if previous != current:
    with open(STATE_FILE, "w") as f:
        f.write(current)

    print(f"Vanguard version: {previous} -> {current}")
else:
    print(f"No change: {current}")
