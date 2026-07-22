"""
tools/comfyui.py — ComfyUI tools
Covers: queue, history, models, system info, interrupt
"""

import json
import httpx
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict
from mcp.server.fastmcp import FastMCP

from config import cfg
from client import fmt_json, fmt_size, TIMEOUT, _handle_error


def register_comfyui_tools(mcp: FastMCP):

    @mcp.tool(name="comfyui_status", annotations={"readOnlyHint": True})
    async def comfyui_status() -> str:
        """Get ComfyUI system status — GPU memory, queue size, running jobs."""
        if not cfg.is_configured("comfyui_url"):
            return "Error: ComfyUI not configured. Set COMFYUI_URL in .env"
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                sys_r = await client.get(f"{cfg.comfyui_url}/system_stats")
                queue_r = await client.get(f"{cfg.comfyui_url}/queue")
                sys_r.raise_for_status()
                queue_r.raise_for_status()
                sys = sys_r.json()
                queue = queue_r.json()

            result = "## ComfyUI Status\n\n"
            devices = sys.get("devices", [])
            for dev in devices:
                result += f"**{dev.get('name', 'GPU')}**\n"
                vram_total = dev.get("vram_total", 0)
                vram_free = dev.get("vram_free", 0)
                vram_used = vram_total - vram_free
                result += f"  VRAM: {fmt_size(vram_used)} / {fmt_size(vram_total)}\n"
                result += f"  Torch: {dev.get('torch_vram_total', 0) // 1024 // 1024}MB allocated\n\n"

            running = queue.get("queue_running", [])
            pending = queue.get("queue_pending", [])
            result += f"Queue: {len(running)} running, {len(pending)} pending\n"
            return result
        except Exception as e:
            return _handle_error(e, "ComfyUI")

    @mcp.tool(name="comfyui_queue", annotations={"readOnlyHint": True})
    async def comfyui_queue() -> str:
        """Get ComfyUI queue — running and pending prompts."""
        if not cfg.is_configured("comfyui_url"):
            return "Error: ComfyUI not configured."
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.get(f"{cfg.comfyui_url}/queue")
                r.raise_for_status()
                data = r.json()

            running = data.get("queue_running", [])
            pending = data.get("queue_pending", [])

            if not running and not pending:
                return "ComfyUI queue is empty — no jobs running."

            result = "## ComfyUI Queue\n\n"
            if running:
                result += f"### Running ({len(running)})\n"
                for item in running:
                    prompt_id = item[1] if len(item) > 1 else "?"
                    result += f"  - Prompt ID: `{prompt_id}`\n"
            if pending:
                result += f"\n### Pending ({len(pending)})\n"
                for item in pending[:10]:
                    prompt_id = item[1] if len(item) > 1 else "?"
                    result += f"  - Prompt ID: `{prompt_id}`\n"
            return result
        except Exception as e:
            return _handle_error(e, "ComfyUI")

    @mcp.tool(name="comfyui_history", annotations={"readOnlyHint": True})
    async def comfyui_history() -> str:
        """Get recent ComfyUI generation history with output filenames."""
        if not cfg.is_configured("comfyui_url"):
            return "Error: ComfyUI not configured."
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.get(f"{cfg.comfyui_url}/history", params={"max_items": 10})
                r.raise_for_status()
                data = r.json()

            if not data:
                return "No generation history found."

            result = f"## ComfyUI History ({len(data)} recent jobs)\n\n"
            for prompt_id, item in list(data.items())[:10]:
                outputs = item.get("outputs", {})
                status = item.get("status", {})
                completed = status.get("completed", False)
                result += f"**{prompt_id[:8]}...** — {'✓ Done' if completed else '⏳ Running'}\n"
                for node_id, output in outputs.items():
                    images = output.get("images", [])
                    for img in images:
                        result += f"  📸 {img.get('filename')} ({img.get('type')})\n"
                result += "\n"
            return result
        except Exception as e:
            return _handle_error(e, "ComfyUI")

    @mcp.tool(name="comfyui_get_models", annotations={"readOnlyHint": True})
    async def comfyui_get_models() -> str:
        """List available ComfyUI models — checkpoints, LoRAs, VAEs."""
        if not cfg.is_configured("comfyui_url"):
            return "Error: ComfyUI not configured."
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                r = await client.get(f"{cfg.comfyui_url}/object_info")
                r.raise_for_status()
                data = r.json()

            result = "## ComfyUI Available Models\n\n"

            # Checkpoints
            ckpt_loader = data.get("CheckpointLoaderSimple", {})
            checkpoints = ckpt_loader.get("input", {}).get("required", {}).get("ckpt_name", [None])[0] or []
            if checkpoints:
                result += f"### Checkpoints ({len(checkpoints)})\n"
                for ckpt in checkpoints[:20]:
                    result += f"  - {ckpt}\n"
                if len(checkpoints) > 20:
                    result += f"  ...and {len(checkpoints) - 20} more\n"

            # LoRAs
            lora_loader = data.get("LoraLoader", {})
            loras = lora_loader.get("input", {}).get("required", {}).get("lora_name", [None])[0] or []
            if loras:
                result += f"\n### LoRAs ({len(loras)})\n"
                for lora in loras[:20]:
                    result += f"  - {lora}\n"
                if len(loras) > 20:
                    result += f"  ...and {len(loras) - 20} more\n"

            # VAEs
            vae_loader = data.get("VAELoader", {})
            vaes = vae_loader.get("input", {}).get("required", {}).get("vae_name", [None])[0] or []
            if vaes:
                result += f"\n### VAEs ({len(vaes)})\n"
                for vae in vaes:
                    result += f"  - {vae}\n"

            return result
        except Exception as e:
            return _handle_error(e, "ComfyUI")

    @mcp.tool(name="comfyui_interrupt", annotations={"readOnlyHint": False, "destructiveHint": True})
    async def comfyui_interrupt() -> str:
        """Cancel the currently running ComfyUI generation."""
        if not cfg.is_configured("comfyui_url"):
            return "Error: ComfyUI not configured."
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.post(f"{cfg.comfyui_url}/interrupt")
                r.raise_for_status()
            return "✓ ComfyUI generation interrupted."
        except Exception as e:
            return _handle_error(e, "ComfyUI")

    class ComfyUIGenerateInput(BaseModel):
        model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
        positive_prompt: str = Field(..., description="Positive prompt for image generation", min_length=1, max_length=2000)
        negative_prompt: str = Field(default="blurry, bad quality, watermark", description="Negative prompt")
        checkpoint: str = Field(default="", description="Checkpoint model name from comfyui_get_models. Leave empty for default.")
        width: int = Field(default=512, description="Image width", ge=64, le=2048)
        height: int = Field(default=512, description="Image height", ge=64, le=2048)
        steps: int = Field(default=20, description="Sampling steps", ge=1, le=150)
        cfg_scale: float = Field(default=7.0, description="CFG scale", ge=1.0, le=30.0)
        seed: int = Field(default=-1, description="Seed (-1 for random)")

    @mcp.tool(name="comfyui_generate", annotations={"readOnlyHint": False, "destructiveHint": False})
    async def comfyui_generate(params: ComfyUIGenerateInput) -> str:
        """Submit a basic txt2img generation to ComfyUI.

        Uses a standard KSampler workflow. For complex workflows use the ComfyUI web UI.
        Returns a prompt_id to track with comfyui_history.
        """
        if not cfg.is_configured("comfyui_url"):
            return "Error: ComfyUI not configured."
        try:
            import random
            import uuid
            seed = params.seed if params.seed != -1 else random.randint(0, 2**32 - 1)
            client_id = str(uuid.uuid4())

            workflow = {
                "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": params.checkpoint or "v1-5-pruned-emaonly.ckpt"}},
                "5": {"class_type": "EmptyLatentImage", "inputs": {"width": params.width, "height": params.height, "batch_size": 1}},
                "6": {"class_type": "CLIPTextEncode", "inputs": {"text": params.positive_prompt, "clip": ["4", 1]}},
                "7": {"class_type": "CLIPTextEncode", "inputs": {"text": params.negative_prompt, "clip": ["4", 1]}},
                "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
                "3": {"class_type": "KSampler", "inputs": {
                    "seed": seed, "steps": params.steps, "cfg": params.cfg_scale,
                    "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0,
                    "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["5", 0]
                }},
                "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": "plutus_mcp"}},
            }

            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                r = await client.post(
                    f"{cfg.comfyui_url}/prompt",
                    json={"prompt": workflow, "client_id": client_id}
                )
                r.raise_for_status()
                data = r.json()

            prompt_id = data.get("prompt_id", "unknown")
            return f"✓ Generation submitted!\nPrompt ID: `{prompt_id}`\nSeed: {seed}\nSize: {params.width}×{params.height}\n\nCheck progress with comfyui_history or comfyui_queue."
        except Exception as e:
            return _handle_error(e, "ComfyUI")
