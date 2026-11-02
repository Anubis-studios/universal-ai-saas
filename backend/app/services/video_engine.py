"""Video generation engine.

Text-to-video is a two-stage pipeline, because that is what the open models
actually provide:

  1. a still frame from Stable Diffusion (sd-webui API), then
  2. frame interpolation / motion from Stable Video Diffusion (ComfyUI graph).

Both stages are optional; the engine reports exactly which one is missing
instead of returning a still image and calling it a video.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import random
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from app.core.config import settings
from app.core.errors import ProviderError, ProviderNotConfigured

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(__file__).resolve().parents[2] / "outputs"
WORKFLOW_PATH = Path(__file__).resolve().parents[1] / "workflows" / "svd_img2vid.json"


class VideoEngine:
    def __init__(self) -> None:
        self.sd = settings.STABLE_DIFFUSION_URL.rstrip("/")
        self.comfy = settings.COMFYUI_BASE_URL.rstrip("/")
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------- health
    async def health(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=3.0) as client:

            async def up(url: str) -> bool:
                try:
                    r = await client.get(url)
                    return r.status_code < 500
                except httpx.HTTPError:
                    return False

            sd, comfy = await asyncio.gather(
                up(f"{self.sd}/sdapi/v1/options"),
                up(f"{self.comfy}/system_stats"),
            )
        return {
            "stable_diffusion": sd,
            "comfyui": comfy,
            "ffmpeg": shutil.which("ffmpeg") is not None,
            "output_dir": str(OUTPUT_DIR),
        }

    # -------------------------------------------------------------------- public
    async def text_to_video(
        self,
        prompt: str,
        *,
        negative_prompt: str = "",
        steps: int = 25,
        width: int = 576,
        height: int = 1024,
        fps: int = 8,
        motion_bucket_id: int = 127,
        seed: int | None = None,
    ) -> dict[str, Any]:
        still_b64 = await self._stable_frame(
            prompt, negative_prompt=negative_prompt, steps=steps, width=width, height=height, seed=seed
        )
        return await self.image_to_video(
            still_b64,
            fps=fps,
            motion_bucket_id=motion_bucket_id,
            seed=seed,
            steps=steps,
            width=width,
            height=height,
            prompt=prompt,
        )

    async def image_to_video(
        self,
        init_image_b64: str,
        *,
        fps: int = 8,
        motion_bucket_id: int = 127,
        seed: int | None = None,
        steps: int = 25,
        width: int = 576,
        height: int = 1024,
        prompt: str = "",
    ) -> dict[str, Any]:
        if not await self._comfy_ready():
            raise ProviderNotConfigured(
                "ComfyUI with the SVD checkpoint is required for video. Start ComfyUI at "
                f"{self.comfy} and place svd_xt.safetensors in models/checkpoints.",
                provider="comfyui",
            )
        if not init_image_b64:
            raise ProviderError("No initial frame available to animate.", provider="comfyui")

        job_id = uuid.uuid4().hex[:12]
        image_name = await self._upload_image(init_image_b64, job_id)
        workflow = self._build_workflow(
            image_name=image_name,
            width=width,
            height=height,
            fps=fps,
            motion_bucket_id=motion_bucket_id,
            steps=steps,
            seed=seed if seed is not None else random.randint(0, 2**31 - 1),
        )
        prompt_id = await self._queue_prompt(workflow)
        files = await self._await_history(prompt_id)
        frames_file = self._first_output(files)
        raw_url = f"{self.comfy}/view?filename={frames_file['filename']}&subfolder={frames_file.get('subfolder','')}&type={frames_file.get('type','output')}"
        frames_bytes = await self._download(raw_url)
        webp_path = OUTPUT_DIR / f"{job_id}.webp"
        webp_path.write_bytes(frames_bytes)
        mp4_path = await self._to_mp4(webp_path, fps)
        return {
            "status": "success",
            "prompt": prompt,
            "job_id": job_id,
            "video_url": f"/media/{mp4_path.name}" if mp4_path else None,
            "frames_url": f"/media/{webp_path.name}",
            "fps": fps,
            "width": width,
            "height": height,
            "frames": 25,
            "seed": seed,
        }

    # --------------------------------------------------------------- stage one
    async def _comfy_ready(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                r = await client.get(f"{self.comfy}/system_stats")
                return r.status_code < 500
        except httpx.HTTPError:
            return False

    async def _stable_frame(
        self, prompt: str, *, negative_prompt: str, steps: int, width: int, height: int, seed: int | None
    ) -> str:
        payload = {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "steps": max(10, min(steps, 60)),
            "cfg_scale": 6.5,
            "width": max(256, width - width % 8),
            "height": max(256, height - height % 8),
            "sampler_name": "DPM++ 2M Karras",
            "seed": seed if seed is not None else random.randint(0, 2**31 - 1),
        }
        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                r = await client.post(f"{self.sd}/sdapi/v1/txt2img", json=payload)
                r.raise_for_status()
                images = r.json().get("images") or []
                if not images:
                    raise ProviderError("Stable Diffusion returned no image.", provider="sdwebui")
                return images[0]
        except httpx.HTTPError as exc:
            raise ProviderNotConfigured(
                f"Stable Diffusion WebUI is not reachable at {self.sd} (run it with --api).",
                provider="sdwebui",
            ) from exc

    # --------------------------------------------------------------- stage two
    def _build_workflow(self, *, image_name: str, width: int, height: int, fps: int,
                        motion_bucket_id: int, steps: int, seed: int) -> dict[str, Any]:
        template = json.loads(WORKFLOW_PATH.read_text())
        raw = json.dumps(template)
        replacements = {
            "__INIT_IMAGE__": image_name,
            "__WIDTH__": width,
            "__HEIGHT__": height,
            "__FRAMES__": 25,
            "__MOTION__": motion_bucket_id,
            "__FPS__": fps,
            "__SEED__": seed,
            "__STEPS__": steps,
        }
        for key, value in replacements.items():
            raw = raw.replace(f'"{key}"', json.dumps(value))
        return json.loads(raw)

    async def _upload_image(self, b64: str, job_id: str) -> str:
        data = base64.b64decode(b64.split(",")[-1])
        files = {"image": (f"{job_id}.png", data, "image/png")}
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(f"{self.comfy}/upload/image", files=files, data={"overwrite": "true"})
            r.raise_for_status()
            body = r.json()
        name = body.get("name", f"{job_id}.png")
        subfolder = body.get("subfolder", "")
        return f"{subfolder}/{name}" if subfolder else name

    async def _queue_prompt(self, workflow: dict[str, Any]) -> str:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                f"{self.comfy}/prompt", json={"prompt": workflow, "client_id": uuid.uuid4().hex}
            )
            if r.status_code >= 400:
                raise ProviderError(
                    f"ComfyUI rejected the workflow: {r.text[:400]}", provider="comfyui"
                )
            return r.json()["prompt_id"]

    async def _await_history(self, prompt_id: str, timeout: float = 900.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        async with httpx.AsyncClient(timeout=20.0) as client:
            while time.monotonic() < deadline:
                r = await client.get(f"{self.comfy}/history/{prompt_id}")
                if r.status_code == 200:
                    history = r.json().get(prompt_id)
                    if history and history.get("outputs"):
                        return history["outputs"]
                    if history and history.get("status", {}).get("status_str") == "error":
                        raise ProviderError("ComfyUI reported an execution error.", provider="comfyui")
                await asyncio.sleep(2.0)
        raise ProviderError("Video generation timed out after 15 minutes.", provider="comfyui")

    @staticmethod
    def _first_output(outputs: dict[str, Any]) -> dict[str, Any]:
        for node in outputs.values():
            for key in ("images", "gifs", "videos"):
                if node.get(key):
                    return node[key][0]
        raise ProviderError("ComfyUI produced no media output.", provider="comfyui")

    async def _download(self, url: str) -> bytes:
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.get(url)
            r.raise_for_status()
            return r.content

    async def _to_mp4(self, source: Path, fps: int) -> Path | None:
        if not shutil.which("ffmpeg"):
            return None
        target = source.with_suffix(".mp4")
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-loglevel", "error", "-i", str(source),
            "-vf", f"fps={fps}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", str(target),
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        _, err = await proc.communicate()
        if proc.returncode != 0:
            logger.warning("ffmpeg failed: %s", err.decode(errors="replace")[:300])
            return None
        return target
