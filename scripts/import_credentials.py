"""Copy only the ODPT key from JRE_app into this project's ignored env file."""
import argparse
import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--jre-app", type=Path, default=ROOT.parent / "JRE_app")
    args = parser.parse_args()
    source = args.jre_app / "JRE_app" / "APIManager.swift"
    text = source.read_text()
    match = re.search(r'private\s+let\s+apiKey\s*=\s*"([^"\n]+)"', text)
    if not match:
        raise SystemExit("JRE_appにODPTキーが見つかりません。環境変数で設定してください。")
    env = ROOT / ".env"
    existing = env.read_text() if env.exists() else (ROOT / ".env.example").read_text()
    key = match.group(1)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", key):
        raise SystemExit("ODPTキー形式を確認してください。")
    pattern = r"(?m)^ODPT_CHALLENGE_CONSUMER_KEY=.*$"
    if re.search(pattern, existing):
        existing = re.sub(pattern, "ODPT_CHALLENGE_CONSUMER_KEY=" + key, existing)
    else:
        existing += "\nODPT_CHALLENGE_CONSUMER_KEY=" + key + "\n"
    fd = os.open(env, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as file:
        file.write(existing)
    print("JRE_appのODPTチャレンジキーを.envへ設定しました（値は非表示）。通常API・Discordのキーは別設定です。")


if __name__ == "__main__":
    main()
