import argparse
import asyncio

from .ai import AIChat, AIError
from .catalog import OPERATORS
from .config import Settings
from .odpt import ODPTClient, ODPTError


def main():
    parser = argparse.ArgumentParser(description="Check configuration without starting Discord")
    parser.add_argument("--api", action="store_true", help="Read-only live ODPT checks")
    parser.add_argument("--discord", action="store_true", help="Validate token via a read-only Discord request; no gateway login")
    parser.add_argument("--ai-prompt", action="store_true", help="Generate one local test response")
    args = parser.parse_args()
    settings = Settings.load()
    for label, value in [("Discord", settings.discord_token), ("ODPT challenge", settings.challenge_key), ("ODPT standard", settings.standard_key)]:
        print(f"{label}: {'設定済み（未認証）' if value else '未設定'}")
    if args.discord and settings.discord_token:
        import requests
        try:
            response = requests.get("https://discord.com/api/v10/users/@me", headers={"Authorization": "Bot " + settings.discord_token}, timeout=(5, 15))
            print("Discord token:", "認証成功" if response.status_code == 200 else f"認証未確認 HTTP {response.status_code}")
        except requests.RequestException:
            print("Discord token: 接続に失敗しました。")
    ai = AIChat(settings)
    async def initialize():
        await asyncio.to_thread(ai.initialize)
    asyncio.run(initialize())
    print("AI:", ai.status)
    if args.ai_prompt and ai.available:
        try:
            asyncio.run(ai.respond((0, 0, 0), "日本語で短く挨拶してください。"))
            print("AI: 推論成功")
        except AIError as error:
            print("AI:", str(error))
    if args.api:
        client = ODPTClient(settings)
        for operator in OPERATORS.values():
            for kind, identifier in [("Railway", operator.id), ("Train", operator.id), ("TrainInformation", operator.information_id)]:
                try:
                    rows = client.fetch(kind, identifier)
                    print(operator.name, kind, f"取得成功: {len(rows)}件")
                except ODPTError as error:
                    print(operator.name, kind, str(error))


if __name__ == "__main__":
    main()
