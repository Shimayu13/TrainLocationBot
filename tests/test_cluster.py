import asyncio
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
import uuid

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

from trainbot.ai import AIError, create_ai
from trainbot.cluster import ClusterChat, Scheduler
from trainbot.cluster_protocol import InferenceEngine, encoded_images, model_input, validate_job
from trainbot.worker import create_app
from trainbot.worker_client import LocalClient, Node, RemoteClient, WorkerError
from test_bot import settings, FakeSDK


def payload(prompt="question"):
    return {"request_id": str(uuid.uuid4()), "task": "primary", "mode": "plus",
            "history": [], "prompt": prompt, "document": "", "images": []}


class ControlledClient:
    def __init__(self):
        self.calls = asyncio.Queue()
        self.available = True
        self.completed = []

    async def health(self):
        return {"available": self.available, "busy": False, "images": True, "protocol": 1}

    async def generate(self, job):
        future = asyncio.get_running_loop().create_future()
        await self.calls.put((job, future))
        result = await future
        self.completed.append(job)
        return result

    async def close(self):
        pass


async def next_call(node):
    return await asyncio.wait_for(node.client.calls.get(), 1)


class SchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.nodes = [Node(str(i), ControlledClient()) for i in range(3)]
        self.scheduler = Scheduler(self.nodes, batching_delay=0.001)
        await self.scheduler.start()

    async def asyncTearDown(self):
        await self.scheduler.close()

    async def test_one_question_uses_three_parallel_roles_then_synthesis(self):
        task = asyncio.create_task(self.scheduler.submit(payload()))
        calls = await asyncio.gather(*(next_call(node) for node in self.nodes))
        self.assertEqual([job["task"] for job, _ in calls], ["primary", "alternative", "critique"])
        self.assertFalse(task.done())
        for job, future in calls:
            future.set_result("draft " + job["task"])
        job, future = await next_call(self.nodes[0])
        self.assertEqual(job["task"], "synthesis")
        self.assertEqual(len(job["drafts"]), 3)
        future.set_result("final answer")
        self.assertEqual(await task, "final answer")

    async def test_three_questions_get_one_worker_each(self):
        tasks = [asyncio.create_task(self.scheduler.submit(payload(str(i)))) for i in range(3)]
        calls = await asyncio.gather(*(next_call(node) for node in self.nodes))
        self.assertEqual([job["task"] for job, _ in calls], ["primary"] * 3)
        self.assertEqual({job["prompt"] for job, _ in calls}, {"0", "1", "2"})
        for job, future in calls:
            future.set_result("answer " + job["prompt"])
        self.assertEqual(await asyncio.gather(*tasks), ["answer 0", "answer 1", "answer 2"])

    async def test_two_questions_get_two_primary_and_one_spare_review(self):
        tasks = [asyncio.create_task(self.scheduler.submit(payload(str(i)))) for i in range(2)]
        calls = await asyncio.gather(*(next_call(node) for node in self.nodes))
        self.assertEqual([job["task"] for job, _ in calls], ["primary", "primary", "alternative"])
        for job, future in calls:
            future.set_result("draft " + job["prompt"])
        _, future = await next_call(self.nodes[0])
        future.set_result("merged")
        self.assertEqual(await asyncio.gather(*tasks), ["merged", "draft 1"])

    async def test_new_question_gets_first_released_worker_before_more_reviews(self):
        first = asyncio.create_task(self.scheduler.submit(payload("first")))
        calls = await asyncio.gather(*(next_call(node) for node in self.nodes))
        second = asyncio.create_task(self.scheduler.submit(payload("second")))
        await asyncio.sleep(0.01)
        calls[1][1].set_result("alternative")
        job, future = await next_call(self.nodes[1])
        self.assertEqual((job["prompt"], job["task"]), ("second", "primary"))
        future.set_result("second answer")
        self.assertEqual(await second, "second answer")
        calls[0][1].set_result("first answer")
        calls[2][1].set_result("checklist")
        _, future = await next_call(self.nodes[0])
        future.set_result("first merged")
        self.assertEqual(await first, "first merged")

    async def test_failed_synthesis_returns_primary_and_failed_worker_is_disabled(self):
        task = asyncio.create_task(self.scheduler.submit(payload()))
        calls = await asyncio.gather(*(next_call(node) for node in self.nodes))
        for job, future in calls:
            future.set_result(job["task"])
        _, future = await next_call(self.nodes[0])
        future.set_exception(WorkerError("unavailable"))
        self.assertEqual(await task, "primary")
        self.assertFalse(self.nodes[0].healthy)
        await self.scheduler.probe()
        self.assertTrue(self.nodes[0].healthy)

    async def test_all_workers_down_fails_without_hanging(self):
        for node in self.nodes:
            node.client.available = False
        await self.scheduler.probe()
        with self.assertRaisesRegex(AIError, "端末"):
            await asyncio.wait_for(self.scheduler.submit(payload()), 1)

    async def test_primary_failure_can_use_independent_answer(self):
        task = asyncio.create_task(self.scheduler.submit(payload()))
        calls = await asyncio.gather(*(next_call(node) for node in self.nodes))
        calls[0][1].set_exception(WorkerError("unavailable"))
        calls[1][1].set_result("independent answer")
        calls[2][1].set_exception(WorkerError("unavailable"))
        self.assertEqual(await task, "independent answer")

    async def test_image_conversation_only_uses_image_capable_workers(self):
        self.nodes[1].images = self.nodes[2].images = False
        job = dict(payload(), images=[{"name": "test", "data": "unused-by-mock"}])
        task = asyncio.create_task(self.scheduler.submit(job))
        _, future = await next_call(self.nodes[0])
        self.assertTrue(self.nodes[1].client.calls.empty())
        self.assertTrue(self.nodes[2].client.calls.empty())
        future.set_result("image answer")
        self.assertEqual(await task, "image answer")

    async def test_cancelled_question_discards_late_results_and_keeps_slots_reserved(self):
        task = asyncio.create_task(self.scheduler.submit(payload()))
        calls = await asyncio.gather(*(next_call(node) for node in self.nodes))
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(all(node.busy for node in self.nodes))
        for _, future in calls:
            future.set_result("late")
        await asyncio.sleep(0.01)
        self.assertTrue(all(not node.busy for node in self.nodes))
        self.assertEqual(self.scheduler.questions, [])


class ClusterHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = ControlledClient()
        self.ai = ClusterChat(replace(settings(), ai_cluster_enabled=True, ai_cluster_timeout=2), FakeSDK,
                              nodes=[Node("test", self.client)])
        self.ai.initialize()
        await self.ai.start()

    async def asyncTearDown(self):
        await self.ai.close()

    async def answer(self, text):
        job, future = await next_call(self.ai.scheduler.nodes[0])
        future.set_result(text)
        return job

    async def test_modes_preserve_final_history_and_same_conversation_is_serial(self):
        first = asyncio.create_task(self.ai.respond((1, 2, 3), "first"))
        second = asyncio.create_task(self.ai.respond((1, 2, 3), "second"))
        await self.answer("first final")
        self.assertEqual(await first, "first final")
        job = await self.answer("second final")
        self.assertEqual(job["history"], [{"prompt": "first", "document": "", "images": [], "answer": "first final"}])
        await second
        original = self.ai.sessions[(1, 2, 3)]
        await self.ai.set_mode((1, 2, 3), "Flash")
        self.assertIs(self.ai.sessions[(1, 2, 3)], original)
        third = asyncio.create_task(self.ai.respond((1, 2, 3), "third"))
        job = await self.answer("third final")
        self.assertEqual(job["mode"], "flash")
        self.assertEqual(len(job["history"]), 2)
        await third

    async def test_reset_discards_old_answer_and_preserves_mode(self):
        key = (1, 2, 3)
        await self.ai.set_mode(key, "Lite")
        task = asyncio.create_task(self.ai.respond(key, "old"))
        _, future = await next_call(self.ai.scheduler.nodes[0])
        await self.ai.reset(key)
        future.set_result("late answer")
        with self.assertRaisesRegex(AIError, "破棄"):
            await task
        self.assertNotIn(key, self.ai.sessions)
        self.assertEqual(self.ai.mode_for(key), "lite")

    async def test_failed_generation_does_not_commit_user_turn(self):
        task = asyncio.create_task(self.ai.respond((1, 2, 3), "failed question"))
        _, future = await next_call(self.ai.scheduler.nodes[0])
        future.set_exception(WorkerError("context_limit"))
        with self.assertRaisesRegex(AIError, "コンテキスト"):
            await task
        self.assertEqual(self.ai.sessions[(1, 2, 3)].history, [])

    async def test_invalid_cluster_configuration_keeps_bot_available(self):
        ai = create_ai(replace(settings(), ai_cluster_enabled=True,
                               ai_worker_urls=("http://example.local:8000",), ai_worker_token=""))
        ai.initialize()
        await ai.start()
        self.assertFalse(ai.available)
        self.assertIn("設定", ai.status)
        await ai.close()

    async def test_total_deadline_does_not_commit_or_free_running_worker(self):
        self.ai.settings = replace(self.ai.settings, ai_cluster_timeout=0.12)
        task = asyncio.create_task(self.ai.respond((1, 2, 3), "slow"))
        _, future = await next_call(self.ai.scheduler.nodes[0])
        with self.assertRaisesRegex(AIError, "時間切れ"):
            await task
        self.assertEqual(self.ai.sessions[(1, 2, 3)].history, [])
        self.assertEqual(self.ai.pending, 0)
        self.assertTrue(self.ai.scheduler.nodes[0].busy)
        future.set_result("too late")

    async def test_factory_is_opt_in_and_input_and_pending_limits_apply(self):
        self.assertIsInstance(create_ai(replace(settings(), ai_cluster_enabled=True)), ClusterChat)
        self.assertNotIsInstance(create_ai(settings()), ClusterChat)
        with self.assertRaises(AIError):
            await self.ai.respond((1, 1, 1), "x" * 101)
        self.ai.pending = 2
        with self.assertRaisesRegex(AIError, "待機"):
            await self.ai.respond((1, 1, 1), "question")
        self.ai.pending = 0

    async def test_images_survive_source_file_cleanup_and_reach_followup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "image.png"
            Image.new("RGB", (8, 8), "red").save(path)
            task = asyncio.create_task(self.ai.respond((1, 2, 3), "image", images=[(path, "red.png")]))
            job = await self.answer("red")
            await task
        task = asyncio.create_task(self.ai.respond((1, 2, 3), "what color?"))
        followup = await self.answer("red again")
        await task
        self.assertEqual(followup["history"][0]["images"], job["images"])
        self.assertFalse(path.exists())


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_engine_uses_final_history_and_immutable_roles(self):
        engine = InferenceEngine(settings(), FakeSDK)
        engine.initialize()
        job = payload()
        job["history"] = [{"prompt": "before", "answer": "final", "document": "external", "images": []}]
        self.assertEqual(await engine.generate(job), "応答")
        self.assertFalse(engine.busy)

    async def test_validation_and_image_decode(self):
        with self.assertRaises(ValueError):
            validate_job(dict(payload(), task="system"))
        with self.assertRaises(ValueError):
            validate_job(dict(payload(), prompt="x" * 60001))
        with self.assertRaises(ValueError):
            validate_job(dict(payload(), images=[{"name": "x", "data": "invalid!"}]))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.png"
            Image.new("RGB", (8, 8), "blue").save(path)
            job = dict(payload(), images=encoded_images([(path, "blue.png")]))
            self.assertEqual(validate_job(job), 1)
            class SDK:
                ImageAttachment = staticmethod(lambda path, label: (path, label))
            model = model_input(job, SDK, directory)
            self.assertIn("turn-1-image-1", model[0])
            self.assertTrue(model[1][0].exists())

    async def test_image_capability_can_be_disabled_without_disabling_text(self):
        engine = InferenceEngine(replace(settings(), ai_images_enabled=False), FakeSDK)
        engine.initialize()
        self.assertFalse(engine.health()["images"])
        self.assertEqual(await engine.generate(payload()), "応答")

    async def test_local_timeout_keeps_engine_busy_until_generation_finishes(self):
        engine = InferenceEngine(settings(), FakeSDK)
        engine.initialize()
        gate = asyncio.Event()
        class Session:
            def __init__(self, **kwargs): pass
            async def respond(self, *args, **kwargs):
                await gate.wait()
                return "done"
        class SDK(FakeSDK):
            LanguageModelSession = Session
        engine.ai.sdk = SDK
        client = LocalClient(engine, 0.01)
        with self.assertRaisesRegex(WorkerError, "timeout"):
            await client.generate(payload())
        self.assertTrue(engine.health()["busy"])
        gate.set()
        await asyncio.sleep(0.01)
        self.assertFalse(engine.health()["busy"])
        await client.close()


class WorkerHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = InferenceEngine(settings(), FakeSDK)
        self.engine.initialize()
        self.token = "test-token-" * 4
        self.http = TestClient(TestServer(create_app(self.engine, self.token)))
        await self.http.start_server()
        self.headers = {"Authorization": "Bearer " + self.token}

    async def asyncTearDown(self):
        await self.http.close()

    async def test_authentication_validation_and_no_raw_errors(self):
        self.assertEqual((await self.http.get("/health")).status, 401)
        response = await self.http.get("/health", headers=self.headers)
        self.assertTrue((await response.json())["available"])
        response = await self.http.post("/generate", json={}, headers=self.headers)
        self.assertEqual(response.status, 400)
        self.assertEqual(await response.json(), {"error": "invalid_request"})

    async def test_idempotent_request_and_conflicting_reuse(self):
        original = self.engine.generate
        count = 0
        async def generate(job):
            nonlocal count
            count += 1
            return await original(job)
        self.engine.generate = generate
        job = payload()
        first = await self.http.post("/generate", json=job, headers=self.headers)
        second = await self.http.post("/generate", json=job, headers=self.headers)
        self.assertEqual(await first.json(), await second.json())
        self.assertEqual(count, 1)
        job["prompt"] = "different"
        self.assertEqual((await self.http.post("/generate", json=job, headers=self.headers)).status, 409)

    async def test_worker_rejects_second_job_while_first_is_running(self):
        gate = asyncio.Event()
        started = asyncio.Event()
        async def generate(job):
            started.set()
            await gate.wait()
            return "done"
        self.engine.generate = generate
        first = asyncio.create_task(self.http.post("/generate", json=payload(), headers=self.headers))
        await started.wait()
        response = await self.http.post("/generate", json=payload(), headers=self.headers)
        self.assertEqual(response.status, 423)
        gate.set()
        self.assertEqual((await first).status, 200)

    async def test_remote_client_round_trip(self):
        remote = RemoteClient(str(self.http.make_url("/")), self.token, 1)
        try:
            self.assertTrue((await remote.health())["available"])
            self.assertEqual(await remote.generate(payload()), "応答")
        finally:
            await remote.close()

    async def test_remote_client_rejects_credential_urls_and_weak_token(self):
        with self.assertRaises(ValueError):
            RemoteClient("http://user:secret@localhost:8000", self.token, 1)
        with self.assertRaises(ValueError):
            RemoteClient("http://localhost:8000", "short", 1)

    async def test_remote_client_reads_fragmented_response_to_end(self):
        async def generate(request):
            response = web.StreamResponse(headers={"Content-Type": "application/json"})
            await response.prepare(request)
            await response.write(b'{"reply":')
            await asyncio.sleep(0.01)
            await response.write(b'"fragmented"}')
            await response.write_eof()
            return response
        app = web.Application()
        app.router.add_post("/generate", generate)
        async with TestServer(app) as server:
            remote = RemoteClient(str(server.make_url("/")), self.token, 1)
            try:
                self.assertEqual(await remote.generate(payload()), "fragmented")
            finally:
                await remote.close()

    async def test_ip_allowlist_rejects_even_correct_token_from_other_ip(self):
        app = create_app(self.engine, self.token, ("192.0.2.1/32",))
        async with TestClient(TestServer(app)) as client:
            response = await client.get("/health", headers=self.headers)
            self.assertEqual(response.status, 403)


if __name__ == "__main__":
    unittest.main()
