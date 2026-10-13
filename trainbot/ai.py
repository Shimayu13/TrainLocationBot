import asyncio
from collections import OrderedDict
from dataclasses import dataclass
import time

AI_INSTRUCTIONS = (
    "あなたは日本語で自然に会話する汎用アシスタントです。利用者の質問と会話の文脈に直接答えてください。"
    "説明や相談では結論に加えて理由、具体例、必要なら手順を示し、通常は300〜700文字程度の数段落で十分に説明してください。"
    "これは目安です。挨拶や単純な質問は短く、複雑な質問は必要なだけ詳しく答えてください。利用者の長さや形式の指定を優先してください。"
    "毎回同じ導入や結び、自己紹介、決まり文句を繰り返さず、話題に合った自然な表現と構成を選んでください。"
    "同じ説明を言い換えて繰り返さず、具体例は理由が伝わるものを選んでください。複数の案を求められたら、特徴や方法が異なる案を挙げてください。"
    "文章を水増しせず、不確かなことや取得していない最新情報は断定しないでください。"
)


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
        return self.sdk.LanguageModelSession(model=self.model, instructions=AI_INSTRUCTIONS)

    def _generation_options(self):
        return self.sdk.GenerationOptions(
            temperature=self.settings.ai_temperature,
            sampling=self.sdk.SamplingMode.random(probability_threshold=self.settings.ai_top_p),
            maximum_response_tokens=self.settings.ai_max_response_tokens or None,
        )

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
                    response = await asyncio.wait_for(conversation.session.respond(prompt, options=self._generation_options()), timeout=self.settings.ai_timeout)
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
