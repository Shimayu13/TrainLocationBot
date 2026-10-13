import asyncio
from collections import OrderedDict
from dataclasses import dataclass
import time


class AIError(RuntimeError):
    pass


@dataclass
class Conversation:
    session: object
    touched: float
    turns: int = 0


class AIChat:
    def __init__(self, settings, sdk=None):
        self.settings = settings
        self.sdk = sdk
        self.sessions = OrderedDict()
        self.lock = asyncio.Lock()
        self.pending = 0
        self.available = False
        self.status = "AI機能は無効です。"

    def initialize(self):
        if not self.settings.ai_enabled:
            return
        try:
            if self.sdk is None:
                import apple_fm_sdk
                self.sdk = apple_fm_sdk
            self.model = self.sdk.SystemLanguageModel()
            self.available, reason = self.model.is_available()
            self.status = "ローカルAIを利用できます。" if self.available else f"ローカルモデルを利用できません: {reason}"
        except Exception:
            self.available = False
            self.status = "Apple公式SDK・OS・Apple Intelligenceの設定を確認してください。"

    def _new_session(self):
        return self.sdk.LanguageModelSession(model=self.model, instructions=(
            "日本語で簡潔に会話するDiscordアシスタントです。"
            "私が使用しているモデルはApple Foundation Model Core 3であり、Appleの利用規約に従って動作します。"
        ))

    async def respond(self, key, prompt):
        if not self.available:
            raise AIError(self.status)
        if not prompt.strip():
            raise AIError("AI の後に質問を入力してください。")
        if len(prompt) > self.settings.ai_max_input:
            raise AIError(f"入力は{self.settings.ai_max_input}文字以内にしてください。")
        if self.pending >= self.settings.ai_max_pending:
            raise AIError("AIの待機件数が上限です。少し待って再試行してください。")
        self.pending += 1
        try:
            async with self.lock:
                now = time.monotonic()
                for old_key in list(self.sessions):
                    if now - self.sessions[old_key].touched >= self.settings.ai_ttl:
                        del self.sessions[old_key]
                conversation = self.sessions.pop(key, None)
                if conversation is None or conversation.turns >= self.settings.ai_max_turns:
                    conversation = Conversation(self._new_session(), now)
                while len(self.sessions) >= self.settings.ai_max_sessions:
                    self.sessions.popitem(last=False)
                self.sessions[key] = conversation
                try:
                    response = await asyncio.wait_for(conversation.session.respond(prompt), timeout=self.settings.ai_timeout)
                except asyncio.CancelledError:
                    self.sessions.pop(key, None)
                    raise
                except Exception as exc:
                    self.sessions.pop(key, None)
                    kind = type(exc).__name__
                    if isinstance(exc, TimeoutError):
                        message = "AIの応答が時間切れになりました。会話をリセットしました。"
                    elif kind == "ExceededContextWindowSizeError":
                        message = "会話が長すぎるためリセットしました。質問を短くして再試行してください。"
                    elif kind == "GuardrailViolationError":
                        message = "この質問にはモデルの制約により回答できません。"
                    else:
                        message = "AIの生成に失敗しました。会話をリセットしました。"
                    raise AIError(message) from None
                conversation.turns += 1
                conversation.touched = time.monotonic()
                return str(response)
        finally:
            self.pending -= 1

    async def reset(self, key):
        async with self.lock:
            self.sessions.pop(key, None)
