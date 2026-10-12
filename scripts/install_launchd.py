"""Generate a reviewable LaunchAgent; install it only with --install."""
import argparse
from pathlib import Path
import plistlib
import shutil

ROOT = Path(__file__).resolve().parent.parent
LABEL = "jp.trainlocationbot.discord"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--install", action="store_true")
    args = parser.parse_args()
    (ROOT / "logs").mkdir(exist_ok=True)
    config = {
        "Label": LABEL,
        "ProgramArguments": ["/bin/sh", str(ROOT / "scripts" / "run_bot.sh")],
        "WorkingDirectory": str(ROOT),
        "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 60,
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1"},
        "StandardOutPath": "/dev/null", "StandardErrorPath": "/dev/null",
    }
    destination = ROOT / "launchd" / (LABEL + ".plist")
    destination.write_bytes(plistlib.dumps(config))
    print("生成:", destination)
    if args.install:
        directory = Path.home() / "Library" / "LaunchAgents"
        directory.mkdir(parents=True, exist_ok=True)
        shutil.copy2(destination, directory / destination.name)
        print("配置:", directory / destination.name)
        print("launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/" + destination.name)


if __name__ == "__main__":
    main()
