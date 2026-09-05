import json
import os
import hashlib
import time
import urllib.request
from datetime import datetime, timezone, timedelta

CONFIG_URL = "https://clientconfig.rpg.riotgames.com/api/v1/config/public"
STATE_FILE = "vanguard_state.json"
STATUS_FILE = "status.json"
HISTORY_FILE = "history.json"

WEBHOOK = os.environ["DISCORD_WEBHOOK"]
MODE = os.environ.get("MODE", "check").lower()

RIOT_USER_AGENT = "VanguardMonitor/4.0"
DISCORD_USER_AGENT = "DiscordBot (VanguardMonitor, 4.0)"
KST = timezone(timedelta(hours=9))


# 시간
def now_kst():
    return datetime.now(KST)


def now_text():
    return now_kst().strftime("%Y-%m-%d %H:%M KST")


def now_iso():
    return now_kst().isoformat(timespec="seconds")


# JSON
def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)


# 요청
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


def retry(name, func, attempts=3):
    last_error = None

    for attempt in range(1, attempts + 1):
        try:
            return func()
        except Exception as error:
            last_error = error
            print(f"{name} failed ({attempt}/{attempts}): {error}")

            if attempt < attempts:
                time.sleep(attempt * 5)

    raise last_error


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# 설정
def get_vanguard_config():
    with urllib.request.urlopen(riot_request(CONFIG_URL), timeout=30) as response:
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
        with urllib.request.urlopen(riot_request(url, method="HEAD"), timeout=30) as response:
            return {
                "etag": response.headers.get("ETag", ""),
                "last_modified": response.headers.get("Last-Modified", ""),
                "content_length": response.headers.get("Content-Length", ""),
            }
    except Exception as error:
        print("HEAD failed:", error)

    with urllib.request.urlopen(
        riot_request(url, headers={"Range": "bytes=0-0"}),
        timeout=30,
    ) as response:
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
    digest = hashlib.sha256()
    size = 0

    print("Checking setup.exe SHA-256...")

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


# Discord
def short_hash(value):
    if not value:
        return "확인 불가"
    return value[:12] + "..."


def format_size(value):
    try:
        size = int(value)
    except (TypeError, ValueError):
        return "확인 불가"

    return f"{size / 1024 / 1024:.2f} MB"


def post_discord(content, ping=False):
    payload = {
        "content": content,
        "allowed_mentions": {
            "parse": ["everyone"] if ping else []
        },
    }

    data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        WEBHOOK,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": DISCORD_USER_AGENT,
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=30) as response:
        response.read()


def send_change(previous, current, items):
    old_version = previous.get("version", "?")
    new_version = current.get("version", "?")

    if old_version != new_version:
        version_text = f"`{old_version}` → `{new_version}`"
    else:
        version_text = f"`{new_version}` (동일)"

    item_lines = []

    for item in items:
        item_lines.append(f"• **{item}**")

        if item == "배포 파일":
            old_hash = short_hash(previous.get("file_sha256"))
            new_hash = short_hash(current.get("file_sha256"))
            old_size = format_size(previous.get("content_length"))
            new_size = format_size(current.get("content_length"))

            item_lines.append(f"  SHA-256: `{old_hash}` → `{new_hash}`")
            item_lines.append(f"  크기: `{old_size}` → `{new_size}`")

    item_text = "\n".join(item_lines)

    content = (
        ">>> 🚨 **Riot Vanguard 변경 감지**\n\n"
        "📦 **버전**\n"
        f"{version_text}\n\n"
        "🔎 **감지 항목**\n"
        f"{item_text}\n\n"
        f"🕒 `{now_text()}`\n"
        "@everyone\n"
        "-# made by jnior"
    )

    post_discord(content, ping=True)


def send_test():
    content = (
        ">>> 🧪 **Vanguard Monitor 테스트**\n\n"
        "✅ 알림이 정상적으로 작동합니다.\n"
        f"🕒 `{now_text()}`\n"
        "@everyone\n"
        "-# made by jnior"
    )

    post_discord(content, ping=True)


def send_error(error_count, error_text):
    short_error = error_text.replace("\n", " ")[:180]

    content = (
        ">>> ⚠️ **Vanguard Monitor 오류**\n\n"
        f"연속 `{error_count}회` 검사에 실패했습니다.\n"
        f"`{short_error}`\n\n"
        f"🕒 `{now_text()}`\n"
        "@everyone\n"
        "-# made by jnior"
    )

    post_discord(content, ping=True)


# 기록
def load_history():
    data = load_json(HISTORY_FILE, [])

    if isinstance(data, list):
        return data

    return []


def add_history(previous, current, items, fingerprint):
    history = load_history()

    history.insert(
        0,
        {
            "time": now_iso(),
            "old_version": previous.get("version", ""),
            "new_version": current.get("version", ""),
            "items": items,
            "old_sha256": previous.get("file_sha256", ""),
            "new_sha256": current.get("file_sha256", ""),
            "fingerprint": fingerprint,
        },
    )

    save_json(HISTORY_FILE, history[:100])


def save_status(state, healthy, error=""):
    status = {
        "healthy": healthy,
        "version": state.get("version", ""),
        "last_success": state.get("last_success", ""),
        "last_deep_check": state.get("last_deep_check", ""),
        "consecutive_failures": state.get("consecutive_failures", 0),
        "file_sha256": state.get("file_sha256", ""),
    }

    if error:
        status["error"] = error[:300]

    save_json(STATUS_FILE, status)


def make_fingerprint(current):
    data = {
        "version": current.get("version", ""),
        "setup_url": current.get("setup_url", ""),
        "config_hash": current.get("config_hash", ""),
        "file_sha256": current.get("file_sha256", ""),
    }

    return sha256_text(
        json.dumps(data, sort_keys=True, separators=(",", ":"))
    )


# 검사
def run_check():
    previous = load_json(STATE_FILE, {})
    initialized = bool(
        previous.get("version")
        and previous.get("file_sha256")
    )

    config = retry("Config", get_vanguard_config)
    metadata = retry(
        "Metadata",
        lambda: get_installer_metadata(config["setup_url"]),
    )

    current = {
        "version": config["version"],
        "setup_url": config["setup_url"],
        "config_hash": config["config_hash"],
        "vanguard_config": config["vanguard_config"],
        "etag": metadata["etag"],
        "last_modified": metadata["last_modified"],
        "content_length": metadata["content_length"],
        "file_sha256": previous.get("file_sha256", ""),
        "last_deep_check": previous.get("last_deep_check", ""),
        "last_success": now_iso(),
        "consecutive_failures": 0,
        "error_alerted": False,
        "last_notified_fingerprint": previous.get("last_notified_fingerprint", ""),
    }

    # 최초 실행
    if not initialized:
        file_hash, downloaded_size = retry(
            "SHA-256",
            lambda: download_and_hash(current["setup_url"]),
        )

        current["file_sha256"] = file_hash
        current["last_deep_check"] = now_iso()

        if not current["content_length"]:
            current["content_length"] = str(downloaded_size)

        save_json(STATE_FILE, current)
        save_status(current, True)

        if not os.path.exists(HISTORY_FILE):
            save_json(HISTORY_FILE, [])

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
    last_deep = str(previous.get("last_deep_check", ""))
    deep_check_due = last_deep[:10] != now_kst().strftime("%Y-%m-%d")
    should_hash = (
        version_changed
        or url_changed
        or config_changed
        or metadata_changed
        or deep_check_due
    )

    if should_hash:
        file_hash, downloaded_size = retry(
            "SHA-256",
            lambda: download_and_hash(current["setup_url"]),
        )

        current["file_sha256"] = file_hash
        current["last_deep_check"] = now_iso()

        if not current["content_length"]:
            current["content_length"] = str(downloaded_size)

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

    # 중복 방지
    if items:
        fingerprint = make_fingerprint(current)

        if fingerprint != previous.get("last_notified_fingerprint", ""):
            send_change(previous, current, items)
            add_history(previous, current, items, fingerprint)
            current["last_notified_fingerprint"] = fingerprint
            print("Change notification sent:", items)
        else:
            print("Duplicate notification skipped.")

    save_json(STATE_FILE, current)
    save_status(current, True)

    if not os.path.exists(HISTORY_FILE):
        save_json(HISTORY_FILE, [])

    print("Check completed.")


# 오류
def handle_error(error):
    state = load_json(STATE_FILE, {})

    failures = int(state.get("consecutive_failures", 0)) + 1
    state["consecutive_failures"] = failures

    error_text = f"{type(error).__name__}: {error}"
    already_alerted = bool(state.get("error_alerted", False))

    if failures >= 3 and not already_alerted:
        try:
            send_error(failures, error_text)
            state["error_alerted"] = True
        except Exception as discord_error:
            print("Error alert failed:", discord_error)

    save_json(STATE_FILE, state)
    save_status(state, False, error_text)

    if not os.path.exists(HISTORY_FILE):
        save_json(HISTORY_FILE, [])

    raise error


def main():
    if MODE == "test":
        print("Sending test notification...")
        retry("Discord test", send_test)
        return

    print("Checking Riot Vanguard...")

    try:
        run_check()
    except Exception as error:
        handle_error(error)


if __name__ == "__main__":
    main()
