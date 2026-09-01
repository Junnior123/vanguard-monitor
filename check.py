import json
import os
import hashlib
import urllib.request
from datetime import datetime, timezone, timedelta

CONFIG_URL = "https://clientconfig.rpg.riotgames.com/api/v1/config/public"
STATE_FILE = "vanguard_state.json"

WEBHOOK = os.environ["DISCORD_WEBHOOK"]

RIOT_USER_AGENT = "VanguardMonitor/3.3"
DISCORD_USER_AGENT = "DiscordBot (VanguardMonitor, 3.3)"
KST = timezone(timedelta(hours=9))


def riot_request(url, method="GET", headers=None):
    final_headers = {
        "User-Agent": RIOT_USER_AGENT,
        "Accept": "*/*",
    }

    if headers:
        final_headers.update(headers)

    return urllib.request.Request(
        url,
        headers=final_headers,
        method=method,
    )


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# 상태
def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)


# 설정
def get_vanguard_config():
    req = riot_request(CONFIG_URL)

    with urllib.request.urlopen(req, timeout=30) as response:
        data = json.load(response)

    vanguard_config = {
        key: value
        for key, value in data.items()
        if key.startswith("anticheat.vanguard.")
    }

    version = str(vanguard_config.get("anticheat.vanguard.version", ""))
    url_template = vanguard_config.get("anticheat.vanguard.url", "")

    if not version:
        raise RuntimeError("Vanguard version을 찾지 못했습니다.")

    if not url_template:
        raise RuntimeError("Vanguard setup URL을 찾지 못했습니다.")

    setup_url = url_template.replace("{version}", version)

    normalized = json.dumps(
        vanguard_config,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    return {
        "version": version,
        "setup_url": setup_url,
        "config_hash": sha256_text(normalized),
        "vanguard_config": vanguard_config,
    }


# 파일 정보
def get_installer_metadata(url):
    try:
        req = riot_request(url, method="HEAD")

        with urllib.request.urlopen(req, timeout=30) as response:
            return {
                "etag": response.headers.get("ETag", ""),
                "last_modified": response.headers.get("Last-Modified", ""),
                "content_length": response.headers.get("Content-Length", ""),
            }

    except Exception as head_error:
        print("HEAD failed, trying ranged GET:", head_error)

    req = riot_request(url, headers={"Range": "bytes=0-0"})

    with urllib.request.urlopen(req, timeout=30) as response:
        total_size = response.headers.get("Content-Range", "")

        if "/" in total_size:
            total_size = total_size.rsplit("/", 1)[-1]
        else:
            total_size = response.headers.get("Content-Length", "")

        return {
            "etag": response.headers.get("ETag", ""),
            "last_modified": response.headers.get("Last-Modified", ""),
            "content_length": total_size,
        }


# 파일 해시
def download_and_hash(url):
    print("Checking setup.exe SHA-256...")

    digest = hashlib.sha256()
    size = 0

    with urllib.request.urlopen(riot_request(url), timeout=300) as response:
        while True:
            chunk = response.read(1024 * 1024)

            if not chunk:
                break

            digest.update(chunk)
            size += len(chunk)

    return digest.hexdigest(), size


# 설정 비교
def get_extra_config_changes(old, new):
    old_config = old.get("vanguard_config", {})
    new_config = new.get("vanguard_config", {})

    ignored = {
        "anticheat.vanguard.version",
        "anticheat.vanguard.url",
    }

    changed = []

    for key in set(old_config) | set(new_config):
        if key in ignored:
            continue

        if old_config.get(key) != new_config.get(key):
            changed.append(key)

    return sorted(changed)


# 알림
def send_discord(previous, current, items, version_changed):
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")

    old_version = previous.get("version", "?")
    new_version = current.get("version", "?")

    if version_changed:
        version_text = f"`{old_version}` → `{new_version}`"
    else:
        version_text = f"`{new_version}` (동일)"

    item_text = " · ".join(items)

    content = (
        "> 🚨 **Riot Vanguard 변경 감지**\n"
        ">\n"
        "> 📦 **버전**\n"
        f"> {version_text}\n"
        ">\n"
        "> 🔎 **감지 항목**\n"
        f"> `{item_text}`\n"
        ">\n"
        f"> 🕒 `{now}`\n"
        "> @everyone\n"
        "> -# made by jnior"
    )

    payload = json.dumps(
        {
            "content": content,
            "allowed_mentions": {"parse": ["everyone"]},
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        WEBHOOK,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": DISCORD_USER_AGENT,
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=30) as response:
        response.read()

    print("Discord notification sent.")


def main():
    print("Checking Riot Vanguard...")

    previous = load_state()
    config = get_vanguard_config()
    metadata = get_installer_metadata(config["setup_url"])

    today = datetime.now(KST).strftime("%Y-%m-%d")

    current = {
        "version": config["version"],
        "setup_url": config["setup_url"],
        "config_hash": config["config_hash"],
        "vanguard_config": config["vanguard_config"],
        "etag": metadata["etag"],
        "last_modified": metadata["last_modified"],
        "content_length": metadata["content_length"],
        "file_sha256": "",
        "last_deep_check": "",
    }

    # 최초 실행
    if previous is None:
        file_hash, downloaded_size = download_and_hash(current["setup_url"])
        current["file_sha256"] = file_hash

        if not current["content_length"]:
            current["content_length"] = str(downloaded_size)

        current["last_deep_check"] = today
        save_state(current)

        print("Initial state saved.")
        return

    version_changed = previous.get("version") != current["version"]
    url_changed = previous.get("setup_url") != current["setup_url"]
    config_changed = previous.get("config_hash") != current["config_hash"]

    metadata_changed = any(
        str(previous.get(field, "")) != str(current.get(field, ""))
        for field in ("etag", "last_modified", "content_length")
    )

    # 정밀 검사
    deep_check_due = previous.get("last_deep_check") != today
    should_hash = version_changed or url_changed or metadata_changed or deep_check_due

    if should_hash:
        file_hash, downloaded_size = download_and_hash(current["setup_url"])
        current["file_sha256"] = file_hash

        if not current["content_length"]:
            current["content_length"] = str(downloaded_size)

        current["last_deep_check"] = today

    else:
        current["file_sha256"] = previous.get("file_sha256", "")
        current["last_deep_check"] = previous.get("last_deep_check", "")

    file_changed = (
        bool(previous.get("file_sha256"))
        and previous.get("file_sha256") != current.get("file_sha256")
    )

    extra_config_changes = get_extra_config_changes(previous, current)

    items = []

    if version_changed:
        items.append("버전")

    if file_changed:
        items.append("배포 파일")

    if url_changed and not version_changed:
        items.append("배포 경로")

    if config_changed and extra_config_changes:
        items.append("Vanguard 설정")

    # 변경 확인
    if items:
        print("Vanguard change detected:", items)
        send_discord(previous, current, items, version_changed)
    else:
        print("No meaningful Vanguard changes.")

    if current != previous:
        save_state(current)


if __name__ == "__main__":
    main()
