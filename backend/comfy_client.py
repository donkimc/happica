"""Thin async client for the ComfyUI HTTP + WebSocket API."""
import json
import uuid
from pathlib import Path
from typing import Callable

import httpx
import websockets

from config import COMFY_URL


class ComfyClient:
    def __init__(self, base_url: str = COMFY_URL):
        self.base_url = base_url.rstrip("/")
        self.client_id = uuid.uuid4().hex
        self.http = httpx.AsyncClient(base_url=self.base_url, timeout=30)

    async def system_stats(self) -> dict:
        r = await self.http.get("/system_stats")
        r.raise_for_status()
        return r.json()

    async def queue_prompt(self, workflow: dict) -> str:
        """Submit an API-format workflow; returns ComfyUI's prompt_id."""
        r = await self.http.post("/prompt", json={"prompt": workflow, "client_id": self.client_id})
        if r.status_code == 400:  # ComfyUI explains invalid workflows in the body
            raise RuntimeError(f"ComfyUI rejected the workflow: {r.text[:1000]}")
        r.raise_for_status()
        return r.json()["prompt_id"]

    async def history(self, prompt_id: str) -> dict:
        r = await self.http.get(f"/history/{prompt_id}")
        r.raise_for_status()
        return r.json().get(prompt_id, {})

    async def download(self, filename: str, subfolder: str = "", type_: str = "output") -> bytes:
        r = await self.http.get("/view", params={"filename": filename, "subfolder": subfolder, "type": type_})
        r.raise_for_status()
        return r.content

    async def upload_image(self, path: Path, name: str) -> str:
        """Copy a local image into ComfyUI's input folder; returns the name LoadImage should use."""
        files = {"image": (name, path.read_bytes(), "image/png")}
        r = await self.http.post("/upload/image", files=files, data={"subfolder": "happica", "overwrite": "true"})
        r.raise_for_status()
        info = r.json()
        return f"{info['subfolder']}/{info['name']}" if info.get("subfolder") else info["name"]

    async def free(self) -> None:
        """Unload all models so the next stage gets the whole 16 GB."""
        r = await self.http.post("/free", json={"unload_models": True, "free_memory": True})
        r.raise_for_status()

    async def interrupt(self) -> None:
        await self.http.post("/interrupt")

    async def run(self, workflow: dict, on_progress: Callable[[int, int, str | None], None] | None = None) -> dict:
        """Queue a workflow, wait for it to finish, and return its outputs keyed by node id.

        on_progress(value, total, node_id) fires for every sampler step.

        The WebSocket is opened before queueing so no progress events are missed.
        """
        ws_url = self.base_url.replace("http", "ws", 1) + f"/ws?clientId={self.client_id}"
        async with websockets.connect(ws_url, max_size=None) as ws:
            prompt_id = await self.queue_prompt(workflow)
            async for raw in ws:
                if isinstance(raw, bytes):  # binary preview frames
                    continue
                msg = json.loads(raw)
                data = msg.get("data", {})
                if data.get("prompt_id") != prompt_id:
                    continue
                if msg["type"] == "progress" and on_progress:
                    on_progress(data["value"], data["max"], data.get("node"))
                elif msg["type"] == "executing" and data.get("node") is None:
                    break
                elif msg["type"] == "execution_error":
                    raise RuntimeError(data.get("exception_message", "ComfyUI execution error").strip())
                elif msg["type"] == "execution_interrupted":
                    raise RuntimeError("Cancelled")
        return (await self.history(prompt_id)).get("outputs", {})

    async def aclose(self) -> None:
        await self.http.aclose()
