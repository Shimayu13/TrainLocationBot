"""Small transport adapters; no credentials or model exception strings in errors."""
import asyncio
from dataclasses import dataclass
from urllib.parse import urlsplit

import aiohttp

from .cluster_protocol import MAX_REPLY


class WorkerError(RuntimeError):
    pass


@dataclass(eq=False)
class Node:
    name: str
    client: object
    healthy: bool = False
    images: bool = False
    busy: bool = False
    probed: bool = False


class LocalClient:
    def __init__(self, engine, timeout):
        self.engine = engine
        self.timeout = timeout
        self.tasks = set()

    async def health(self):
        return self.engine.health()

    async def generate(self, job):
        task = asyncio.create_task(self.engine.generate(job))
        self.tasks.add(task)

        def completed(done):
            self.tasks.discard(done)
            if not done.cancelled():
                done.exception()

        task.add_done_callback(completed)
        try:
            return await asyncio.wait_for(asyncio.shield(task), self.timeout)
        except TimeoutError:
            raise WorkerError("timeout") from None
        except Exception as exc:
            from .cluster_protocol import error_code
            raise WorkerError(error_code(exc)) from None

    async def close(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)


class RemoteClient:
    def __init__(self, url, token, timeout):
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
            raise ValueError("AI_WORKER_URLS must contain http(s) origins without credentials")
        if len(token) < 32:
            raise ValueError("AI_WORKER_TOKEN must contain at least 32 characters")
        self.url, self.token, self.timeout = url.rstrip("/"), token, timeout
        self.session = None

    def _session(self):
        if self.session is None:
            self.session = aiohttp.ClientSession(
                headers={"Authorization": "Bearer " + self.token},
                timeout=aiohttp.ClientTimeout(total=self.timeout, connect=3),
                trust_env=False,
            )
        return self.session

    async def _json(self, method, endpoint, **kwargs):
        try:
            async with self._session().request(method, self.url + endpoint, allow_redirects=False, **kwargs) as response:
                chunks = []
                size = 0
                async for chunk in response.content.iter_chunked(8192):
                    size += len(chunk)
                    if size > MAX_REPLY * 6 + 4096:
                        raise WorkerError("invalid_response")
                    chunks.append(chunk)
                raw = b"".join(chunks)
                import json
                value = json.loads(raw)
                if response.status != 200 or not isinstance(value, dict):
                    code = value.get("error", "unavailable") if isinstance(value, dict) else "unavailable"
                    raise WorkerError(code if code in {"busy", "context_limit", "guardrail", "images_unavailable"} else "unavailable")
                return value
        except WorkerError:
            raise
        except Exception:
            raise WorkerError("unavailable") from None

    async def health(self):
        return await self._json("GET", "/health", timeout=aiohttp.ClientTimeout(total=3))

    async def generate(self, job):
        result = await self._json("POST", "/generate", json=job)
        reply = result.get("reply")
        if not isinstance(reply, str) or not reply.strip() or len(reply) > MAX_REPLY:
            raise WorkerError("invalid_response")
        return reply

    async def close(self):
        if self.session:
            await self.session.close()
