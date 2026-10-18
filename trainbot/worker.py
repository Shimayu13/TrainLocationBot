"""Authenticated AFM worker. Run on laptops, never with a Discord token."""
import asyncio
from collections import OrderedDict
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import uuid

from aiohttp import web

from .cluster_protocol import InferenceEngine, MAX_BODY, error_code, validate_job
from .config import Settings


def create_app(engine, token, allowed_ips=()):
    if len(token) < 32:
        raise ValueError("AI_WORKER_TOKEN must contain at least 32 characters")
    networks = [ipaddress.ip_network(value.strip()) for value in allowed_ips if value.strip()]
    jobs = OrderedDict()

    @web.middleware
    async def authenticate(request, handler):
        try:
            address = ipaddress.ip_address(request.remote or "")
        except ValueError:
            raise web.HTTPForbidden() from None
        if networks and not any(address in network for network in networks):
            raise web.HTTPForbidden()
        supplied = request.headers.get("Authorization", "").encode()
        if not hmac.compare_digest(supplied, ("Bearer " + token).encode()):
            raise web.HTTPUnauthorized()
        return await handler(request)

    app = web.Application(client_max_size=MAX_BODY, middlewares=[authenticate])

    async def health(request):
        return web.json_response(engine.health())

    async def generate(request):
        if request.content_type != "application/json":
            raise web.HTTPUnsupportedMediaType()
        try:
            raw = await request.read()
            job = json.loads(raw)
            validate_job(job)
            uuid.UUID(job["request_id"])
        except (ValueError, TypeError, KeyError):
            return web.json_response({"error": "invalid_request"}, status=400)
        digest = hashlib.sha256(raw).digest()
        identifier = job["request_id"]
        if identifier in jobs:
            previous_digest, task = jobs[identifier]
            if previous_digest != digest:
                return web.json_response({"error": "request_id_conflict"}, status=409)
        else:
            if engine.busy or any(not entry[1].done() for entry in jobs.values()):
                return web.json_response({"error": "busy"}, status=423)
            while len(jobs) >= 32:
                jobs.popitem(last=False)

            async def run():
                try:
                    return {"reply": await engine.generate(job)}, 200
                except Exception as exc:
                    return {"error": error_code(exc)}, 503

            task = asyncio.create_task(run())
            jobs[identifier] = digest, task
        # A disconnected/timed out client must not free a still-running engine.
        result, status = await asyncio.shield(task)
        return web.json_response(result, status=status)

    async def cleanup(app):
        tasks = [entry[1] for entry in jobs.values() if not entry[1].done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        jobs.clear()

    app.router.add_get("/health", health)
    app.router.add_post("/generate", generate)
    app.on_cleanup.append(cleanup)
    return app


def main():
    # Separate environment file prevents accidentally distributing Bot/API keys.
    settings = Settings.load(env_file=os.getenv("TRAINBOT_WORKER_ENV_FILE", Path(__file__).resolve().parents[1] / ".env.worker"))
    engine = InferenceEngine(settings)
    engine.initialize()
    host = os.getenv("AI_WORKER_HOST", "127.0.0.1")
    allowed = tuple(value.strip() for value in os.getenv("AI_WORKER_ALLOWED_IPS", "").split(",") if value.strip())
    if host not in {"127.0.0.1", "::1", "localhost"} and not allowed:
        raise ValueError("LAN binding requires AI_WORKER_ALLOWED_IPS (Mac mini IP/CIDR)")
    app = create_app(engine, settings.ai_worker_token, allowed)
    web.run_app(app, host=host, port=int(os.getenv("AI_WORKER_PORT", "8000")),
                access_log=None, handler_cancellation=True)


if __name__ == "__main__":
    main()
