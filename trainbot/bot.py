import asyncio
import logging
from logging.handlers import RotatingFileHandler
import os
import random
import re
import signal

import discord
from discord.ext import commands

from .ai import AIError, AI_MODES, create_ai
from .catalog import COVERAGE, LEGACY_LINES, OPERATORS, TOBU_LINES
from .config import ROOT, Settings
from .messages import split_messages
from .odpt import ODPTError
from .services import RailwayService
from .attachments import prepare_attachments


async def send(ctx, text, *, code=False):
    for chunk in split_messages(text, code=code):
        await ctx.send(chunk, allowed_mentions=discord.AllowedMentions.none())


def conversation_key(ctx):
    return (ctx.guild.id if ctx.guild else 0, ctx.channel.id, ctx.author.id)


class TrainBot(commands.Bot):
    def __init__(self, settings):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="", intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.ai = create_ai(settings)
        self.railways = RailwayService()

    async def setup_hook(self):
        await asyncio.to_thread(self.ai.initialize)
        await self.ai.start()
        logging.getLogger(__name__).info("AI status: %s", self.ai.status)

    async def close(self):
        try:
            await self.ai.close()
        finally:
            await super().close()

    async def on_ready(self):
        logging.getLogger(__name__).info("Discord connected: user_id=%s", self.user.id)

    async def process_commands(self, message):
        # discord.py normally ignores all bot authors. Accept other bots while
        # retaining its command parsing and excluding our own replies.
        if self.user is None or message.author.id == self.user.id:
            return
        ctx = await self.get_context(message)
        content = message.content
        mention = re.compile(rf"<@!?{self.user.id}>")
        match = mention.search(content) if isinstance(content, str) else None
        if match:
            prompt = mention.sub("", content[match.end():]).strip()
            attachments = getattr(message, "attachments", [])
            attachments = attachments if isinstance(attachments, (list, tuple)) else []
            if not prompt and not attachments:
                await send(ctx, "こんにちは。私はApple Foundation Modelを用いたAIです。例のように質問を送信してください。例: @Bot こんにちは")
                return
            try:
                await self.reply_ai(ctx, prompt)
            except Exception as error:
                await self.on_command_error(ctx, error)
            return
        await self.invoke(ctx)

    async def reply_ai(self, ctx, prompt):
        if re.fullmatch(r"(?:会話履歴|履歴|コンテキスト(?:ウィンドウ)?)を(?:削除|リセット|クリア|空に)して(?:ください)?[。！!]?", prompt.strip()):
            await self.ai.reset(conversation_key(ctx))
            await send(ctx, "あなたのこのチャンネルでの会話履歴を消去しました。応答モードは維持しています。")
            return
        async with ctx.typing():
            files = getattr(getattr(ctx, "message", None), "attachments", [])
            files = files if isinstance(files, (list, tuple)) else []
            if files:
                if not self.ai.available:
                    raise AIError(self.ai.status)
                if len(prompt) > self.ai.settings.ai_max_input:
                    raise AIError(f"入力は{self.ai.settings.ai_max_input}文字以内にしてください。")
                async with prepare_attachments(files) as prepared:
                    result = await self.ai.respond(conversation_key(ctx), prompt or "添付の内容を説明してください。", images=prepared.images, document_text=prepared.text)
                    if prepared.notices:
                        result = "\n".join(prepared.notices) + "\n\n" + result
            else:
                result = await self.ai.respond(conversation_key(ctx), prompt)
        await send(ctx, result)

    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.CommandNotFound):
            return
        error = getattr(error, "original", error)
        if isinstance(error, (ODPTError, AIError)):
            await send(ctx, str(error))
        elif isinstance(error, commands.UserInputError):
            await send(ctx, "使い方: 列車位置 <路線名> / 運行情報 <事業者名> / AI <質問>")
        else:
            # Do not log authenticated URLs or Discord message content.
            logging.getLogger(__name__).error("Command failed: %s", type(error).__name__)
            await send(ctx, "処理に失敗しました。時間をおいて再試行してください。")


def create_bot(settings):
    bot = TrainBot(settings)

    async def positions(ctx, name):
        async with ctx.typing():
            result = await bot.railways.run(bot.railways.positions, name)
        await send(ctx, result, code=True)

    for line in {**LEGACY_LINES, **TOBU_LINES}:
        def make_callback(name):
            async def callback(ctx):
                await positions(ctx, name)
            return callback
        bot.add_command(commands.Command(make_callback(line), name=line))

    @bot.command(name="列車位置")
    async def train_positions(ctx, *, line: str):
        await positions(ctx, line.strip())

    @bot.command(name="運行情報")
    async def information(ctx, *, operator: str = "JR"):
        async with ctx.typing():
            result = await bot.railways.run(bot.railways.information, operator.strip())
        if result is not None:
            await send(ctx, result)

    @bot.command(name="対応路線")
    async def supported(ctx):
        await send(ctx, "列車位置の対応路線:\n" + "\n".join({**LEGACY_LINES, **TOBU_LINES}) + "\n\n" + COVERAGE)

    @bot.command(name="運行情報対応路線")
    async def supported_information(ctx):
        await send(ctx, "運行情報: " + "・".join(o.name for o in OPERATORS.values()))

    @bot.command(name="サイコロ")
    async def dice(ctx):
        await send(ctx, f"サイコロの出目は: {random.randint(1, 6)}")

    @bot.command(name="乱数")
    async def random_number(ctx):
        await send(ctx, f"乱数は: {random.randint(1, 50)}")

    @bot.command(name="えっち")
    async def original_joke(ctx):
        await send(ctx, f"お前の掛け金の{random.randint(1, 10)}倍の金額を頂く")

    @bot.command(name="おみくじ")
    async def fortune(ctx):
        await send(ctx, "あなたのおみくじの結果は: " + random.choice(["大吉", "中吉", "小吉", "吉", "末吉", "凶", "大凶"]))

    @bot.command(name="AI", aliases=["ai"])
    async def ai(ctx, *, prompt: str = ""):
        await bot.reply_ai(ctx, prompt)

    @bot.command(name="AIリセット", aliases=["aiリセット", "AI履歴削除", "AIコンテキストクリア"])
    async def ai_reset(ctx):
        await bot.ai.reset(conversation_key(ctx))
        await send(ctx, "あなたのこのチャンネルでのAI会話をリセットしました。")

    @bot.command(name="AI状態")
    async def ai_status(ctx):
        await send(ctx, bot.ai.status + "\n応答モード: " + AI_MODES[bot.ai.mode_for(conversation_key(ctx))][0])

    @bot.command(name="AIモード", aliases=["aiモード"])
    async def ai_mode(ctx, *, mode: str = ""):
        key = conversation_key(ctx)
        if not mode.strip():
            current = AI_MODES[bot.ai.mode_for(key)][0]
            await send(ctx, f"現在のモード: {current}\nLite: 短文 / Flash: 要点と必要な補足・Markdown対応 / Plus: 必要に応じて詳しい説明・Markdown対応\n変更例: AIモード Lite")
            return
        selected = await bot.ai.set_mode(key, mode)
        message = f"このチャンネルでのあなたの応答を {selected} に切り替えました。"
        message += "\n会話履歴はそのまま引き継ぎます。"
        await send(ctx, message)

    return bot


def configure_logging():
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    handler = RotatingFileHandler(logs / "bot.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[handler, logging.StreamHandler()])
    # urllib3 debug logs can include credential-bearing request URLs.
    logging.getLogger("urllib3").setLevel(logging.WARNING)


async def run(settings):
    bot = create_bot(settings)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(bot.close()))
    async with bot:
        await bot.start(settings.discord_token, reconnect=True)


def main():
    settings = Settings.load()
    configure_logging()
    if not settings.discord_token:
        raise SystemExit("DISCORD_BOT_TOKEN が未設定です。")
    try:
        asyncio.run(run(settings))
    except discord.LoginFailure:
        raise SystemExit("Discordトークンが無効です。再発行して設定してください。") from None


if __name__ == "__main__":
    main()
