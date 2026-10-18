"""Adaptive ensemble: serve waiting questions first, spend spare slots on reviews."""
import asyncio
from dataclasses import dataclass, field
import json
import uuid

from .ai import AIChat, AIError
from .cluster_protocol import InferenceEngine, MAX_BODY, encoded_images, validate_job
from .worker_client import LocalClient, Node, RemoteClient, WorkerError


@dataclass(eq=False)
class Question:
    payload: dict
    future: asyncio.Future
    used: set = field(default_factory=set)
    running: int = 0
    drafts: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    primary_started: bool = False
    synthesizing: bool = False
    cancelled: bool = False

    @property
    def needs_images(self):
        return any(turn.get("images") for turn in [*self.payload["history"], self.payload])


class Scheduler:
    def __init__(self, nodes, *, batching_delay=0.05):
        self.nodes = nodes
        self.questions = []
        self.tasks = set()
        self.event = asyncio.Event()
        self.dispatcher = self.monitor = None
        self.batching_delay = batching_delay
        self.closed = False
        self.review_cursor = 0
        self.start_lock = asyncio.Lock()

    async def start(self):
        async with self.start_lock:
            if self.closed:
                raise AIError("AI処理は終了しています。")
            if self.dispatcher:
                return
            await self.probe()
            self.dispatcher = asyncio.create_task(self._dispatch())
            self.monitor = asyncio.create_task(self._monitor())

    async def probe(self):
        async def check(node):
            if node.busy:
                return
            try:
                state = await node.client.health()
                node.healthy = state.get("protocol") == 1 and state.get("available") is True and state.get("busy") is False
                node.images = state.get("images") is True
            except Exception:
                node.healthy = False
            node.probed = True
        await asyncio.gather(*(check(node) for node in self.nodes))
        self.event.set()

    async def _monitor(self):
        while True:
            await asyncio.sleep(5)
            await self.probe()

    async def submit(self, payload):
        await self.start()
        question = Question(payload, asyncio.get_running_loop().create_future())
        self.questions.append(question)
        self.event.set()
        try:
            return await question.future
        finally:
            question.cancelled = True
            if question in self.questions:
                self.questions.remove(question)
            # Running work retains its worker slot, but cannot publish or commit.
            self.event.set()

    def _eligible(self, question, *, unused=False):
        return [node for node in self.nodes if node.healthy and not node.busy
                and (not question.needs_images or node.images)
                and (not unused or node not in question.used)]

    def _launch(self, question, node, task):
        node.busy = True
        question.used.add(node)
        question.running += 1
        payload = dict(question.payload, request_id=str(uuid.uuid4()), task=task)
        if task == "synthesis":
            payload["drafts"] = list(question.drafts)
            question.synthesizing = True
        else:
            question.primary_started = True
        runner = asyncio.create_task(self._run(question, node, task, payload))
        self.tasks.add(runner)
        runner.add_done_callback(self.tasks.discard)

    async def _run(self, question, node, task, payload):
        try:
            reply = await node.client.generate(payload)
            if not question.cancelled:
                if task == "synthesis":
                    self._finish(question, reply)
                else:
                    question.drafts.append({"task": task, "reply": reply})
        except Exception as exc:
            code = str(exc) if isinstance(exc, WorkerError) else "unavailable"
            question.errors.append(code)
            if code == "images_unavailable":
                node.images = False
            if code not in {"context_limit", "guardrail", "images_unavailable"}:
                node.healthy = False
            if task == "synthesis" and not question.cancelled:
                self._finish(question, self._fallback(question))
        finally:
            question.running -= 1
            node.busy = False
            self.event.set()

    @staticmethod
    def _fallback(question):
        for role in ("primary", "alternative"):
            for draft in question.drafts:
                if draft["task"] == role:
                    return draft["reply"]
        return None

    def _finish(self, question, reply=None):
        if question.future.done():
            return
        if reply:
            question.future.set_result(reply)
        else:
            if "context_limit" in question.errors:
                message = "コンテキスト上限に達しました。履歴は保持しています。必要なら AIリセット を実行してください。"
            elif "guardrail" in question.errors:
                message = "この質問にはモデルの制約により回答できません。"
            else:
                message = "利用できるAI端末がありません。端末の起動・接続・画像対応状況を確認して再試行してください。履歴は保持しています。"
            question.future.set_exception(AIError(message))

    def _tick(self):
        active = [q for q in self.questions if not q.cancelled and not q.future.done()]
        # A first answer for every waiting question takes precedence over reviews.
        for question in active:
            if not question.primary_started:
                nodes = self._eligible(question, unused=True)
                if nodes:
                    self._launch(question, nodes[0], "primary")
        for question in active:
            if question.running or question.synthesizing or not question.primary_started:
                continue
            fallback = self._fallback(question)
            if fallback is None:
                nodes = self._eligible(question, unused=True)
                if nodes:
                    self._launch(question, nodes[0], "primary")
                elif not any(node.busy and node not in question.used and node.healthy
                             and (not question.needs_images or node.images) for node in self.nodes):
                    self._finish(question)
            elif len(question.drafts) == 1:
                self._finish(question, fallback)
            else:
                nodes = self._eligible(question)
                if nodes:
                    self._launch(question, nodes[0], "synthesis")
        waiting = [q for q in active if not q.future.done() and not q.primary_started]
        for question in waiting:
            if not any(node.healthy and (not question.needs_images or node.images) for node in self.nodes):
                self._finish(question)
        # No new speculative jobs while primary questions or ready syntheses wait.
        if waiting or any(not q.future.done() and not q.running and self._fallback(q) for q in active):
            return
        if active:
            offset = self.review_cursor % len(active)
            ordered = active[offset:] + active[:offset]
            for role in ("alternative", "critique"):
                for question in ordered:
                    if question.future.done() or question.synthesizing or not question.running or len(question.used) >= 3:
                        continue
                    # One independent draft and one checklist per question.
                    if role == "alternative" and len(question.used) != 1:
                        continue
                    if role == "critique" and len(question.used) != 2:
                        continue
                    nodes = self._eligible(question, unused=True)
                    if nodes:
                        self._launch(question, nodes[0], role)
                        self.review_cursor += 1

    async def _dispatch(self):
        while True:
            await self.event.wait()
            self.event.clear()
            # Coalesce requests arriving in the same burst before reserving extras.
            await asyncio.sleep(self.batching_delay)
            self._tick()

    async def close(self):
        self.closed = True
        for question in self.questions:
            if not question.future.done():
                question.future.cancel()
        tasks = [task for task in [self.dispatcher, self.monitor, *self.tasks] if task]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.gather(*(node.client.close() for node in self.nodes))


@dataclass
class ClusterConversation:
    mode: str = "plus"
    history: list = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class ClusterChat(AIChat):
    @property
    def status(self):
        scheduler = getattr(self, "scheduler", None)
        if scheduler and self.available:
            count = sum(node.healthy for node in scheduler.nodes)
            images = sum(node.healthy and node.images for node in scheduler.nodes)
            return (f"協調AI: 利用可能な端末 {count}/{len(scheduler.nodes)}台（画像対応 {images}台）、受付中 {self.pending}件。"
                    "待機質問を優先し、余力があれば最大3台で比較・統合します。")
        return self._status

    @status.setter
    def status(self, value):
        self._status = value

    def __init__(self, settings, sdk=None, *, nodes=None):
        super().__init__(settings, sdk)
        self.engine = InferenceEngine(settings, sdk)
        self.scheduler = None
        self.injected_nodes = nodes

    def initialize(self):
        if not self.settings.ai_enabled:
            return
        self.engine.initialize()
        self.available = True  # Remote workers may work even without local AFM.
        self.status = "協調AI: 待機中の質問を優先し、余力があれば最大3台で比較・統合します。"

    async def start(self):
        if not self.available:
            return
        if self.scheduler is None:
            nodes = self.injected_nodes
            if nodes is None:
                nodes = [Node("mini", LocalClient(self.engine, self.settings.ai_timeout))]
                try:
                    for index, url in enumerate(dict.fromkeys(self.settings.ai_worker_urls)):
                        nodes.append(Node(f"worker-{index + 1}", RemoteClient(url, self.settings.ai_worker_token, self.settings.ai_timeout)))
                except ValueError:
                    self.available = False
                    self.status = "協調AIの設定が不正です。AI_WORKER_URLSと32文字以上のAI_WORKER_TOKENを確認してください。鉄道機能は利用できます。"
                    return
            self.scheduler = Scheduler(nodes)
        await self.scheduler.start()

    async def respond(self, key, prompt, *, images=None, document_text=""):
        if not self.available:
            raise AIError(self.status)
        if not prompt.strip() or len(prompt) > self.settings.ai_max_input:
            raise AIError(f"質問は1〜{self.settings.ai_max_input}文字で入力してください。")
        if self.pending >= self.settings.ai_max_pending:
            raise AIError("AIの待機件数が上限です。少し待って再試行してください。")
        self.pending += 1
        try:
            conversation = self.sessions.get(key)
            if conversation is None:
                if len(self.sessions) >= self.settings.ai_max_sessions:
                    raise AIError("AIの会話保持件数が上限です。必要なら AIリセット を実行してください。")
                conversation = ClusterConversation(mode=self.mode_for(key))
                self.sessions[key] = conversation
            # Queue time, including earlier turns in the same conversation, is bounded.
            async with asyncio.timeout(self.settings.ai_cluster_timeout):
                async with conversation.lock:
                    if self.sessions.get(key) is not conversation:
                        raise AIError("会話履歴がリセットされたため、この質問を中止しました。")
                    await self.start()
                    if not self.available:
                        raise AIError(self.status)
                    payload = {"request_id": str(uuid.uuid4()), "task": "primary",
                               "mode": self.mode_for(key), "history": list(conversation.history),
                               "prompt": prompt, "document": document_text,
                               "images": encoded_images(images)}
                    try:
                        validate_job(payload)
                        if len(json.dumps(payload).encode()) > MAX_BODY:
                            raise ValueError("body limit")
                    except ValueError:
                        raise AIError("会話または保持画像の容量上限です。履歴は保持しています。必要なら AIリセット を実行してください。") from None
                    response = await self.scheduler.submit(payload)
                    if self.sessions.get(key) is not conversation:
                        raise AIError("会話履歴がリセットされたため、古い回答を破棄しました。")
                    conversation.history.append({"prompt": prompt, "document": document_text,
                                                 "images": payload["images"], "answer": response})
                    return response
        except TimeoutError:
            raise AIError("AIの待機・応答が時間切れになりました。履歴は保持しています。再試行してください。") from None
        finally:
            self.pending -= 1

    async def close(self):
        if self.scheduler:
            await self.scheduler.close()
