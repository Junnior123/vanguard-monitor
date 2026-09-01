import json
import os
import urllib.request
import urllib.error

API_URL = "https://clientconfig.rpg.riotgames.com/api/v1/config/public"
STATE_FILE = "last_version.txt"

WEBHOOK = os.environ["DISCORD_WEBHOOK"]


def get_current_version():
    req = urllib.request.Request(
        API_URL,
        headers={
            "User-Agent": "Mozilla/5.0"
        }
    )

    with urllib.request.urlopen(req, timeout=20) as response:
        data = json.load(response)

    return data["anticheat.vanguard.version"]


def send_discord(old_version, new_version):
    message = {
        "content": (
            "🚨 **Riot Vanguard 업데이트 감지**\n\n"
            f"이전 버전: `{old_version}`\n"
            f"새 버전: `{new_version}`"
        )
    }

    data = json.dumps(message).encode("utf-8")

    req = urllib.request.Request(
        WEBHOOK,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "DiscordBot (VanguardMonitor, 1.0)"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            response.read()

        print("Discord notification sent.")

    except urllib.error.HTTPError as e:
        print("Discord HTTP Error:", e.code)
        print(e.read().decode("utf-8", errors="ignore"))
        raise


def load_previous_version():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


def save_version(version):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        f.write(version)


def main():
    current = get_current_version()
    previous = load_previous_version()

    print("Previous:", previous)
    print("Current :", current)

    # 최초 실행
    if not previous:
        print("First run. Saving current version.")
        save_version(current)
        return

    # 버전 변경
    if previous != current:
        print("Vanguard update detected!")

        send_discord(
            previous,
            current
        )

        save_version(current)

    else:
        print("No Vanguard update.")


if __name__ == "__main__":
    main()
