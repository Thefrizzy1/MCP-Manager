"""tools/fal_tools.py — fal.ai cloud inference"""
import asyncio
import httpx
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict
from mcp.server.fastmcp import FastMCP
from config import cfg
from client import _handle_error

def register_fal_tools(mcp: FastMCP):
    def _h(): return {"Authorization":f"Key {cfg.fal_key}","Content-Type":"application/json"}

    @mcp.tool(name="fal_list_models_snippet",annotations={"readOnlyHint":True})
    async def fal_list_models_snippet()->str:
        """List popular fal.ai models for image/video generation."""
        if not cfg.is_configured("fal_key"): return "Error: fal.ai not configured. Set FAL_KEY in .env"
        return """## fal.ai Popular Models\n\n### Image\n- fal-ai/flux/schnell — fastest\n- fal-ai/flux/dev — high quality\n- fal-ai/flux-pro — best quality\n- fal-ai/stable-diffusion-v3-medium\n\n### Video\n- fal-ai/wan-t2v — WAN 2.1 text-to-video\n- fal-ai/wan-i2v — WAN 2.1 image-to-video\n- fal-ai/kling-video/v1/standard/text-to-video\n\n### Upscaling\n- fal-ai/esrgan\n- fal-ai/aura-sr"""

    class FalGenInput(BaseModel):
        model_config=ConfigDict(str_strip_whitespace=True,extra="forbid")
        prompt:str=Field(...,description="Generation prompt",min_length=1,max_length=2000)
        model_id:str=Field(default="fal-ai/flux/schnell",description="Model ID")
        width:int=Field(default=1024,ge=256,le=2048)
        height:int=Field(default=1024,ge=256,le=2048)
        num_images:int=Field(default=1,ge=1,le=4)
        seed:Optional[int]=Field(default=None)

    @mcp.tool(name="fal_generate_image",annotations={"readOnlyHint":False})
    async def fal_generate_image(params:FalGenInput)->str:
        """Generate an image using fal.ai cloud inference (FLUX Schnell by default)."""
        if not cfg.is_configured("fal_key"): return "Error: fal.ai not configured."
        try:
            body={"prompt":params.prompt,"image_size":{"width":params.width,"height":params.height},"num_images":params.num_images}
            if params.seed is not None: body["seed"]=params.seed
            async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
                sr=await client.post(f"https://queue.fal.run/{params.model_id}",headers=_h(),json=body)
                sr.raise_for_status()
                request_id=sr.json().get("request_id")
                if not request_id: return f"Error: no request_id. {sr.json()}"
                for _ in range(30):
                    await asyncio.sleep(3)
                    st=await client.get(f"https://queue.fal.run/{params.model_id}/requests/{request_id}/status",headers=_h())
                    s=st.json().get("status","")
                    if s=="COMPLETED":
                        rr=await client.get(f"https://queue.fal.run/{params.model_id}/requests/{request_id}",headers=_h())
                        imgs=rr.json().get("images",[])
                        out=f"## fal.ai Generated\nModel: {params.model_id}\nPrompt: {params.prompt[:80]}\n\n"
                        for i,img in enumerate(imgs): out+=f"Image {i+1}: {img.get('url')}\n"
                        return out
                    if s=="FAILED": return f"Error: generation failed. {st.json().get('error','')}"
            return "Error: timed out after 90s"
        except Exception as e: return _handle_error(e,"fal.ai")

    class FalProInput(BaseModel):
        model_config=ConfigDict(str_strip_whitespace=True,extra="forbid")
        prompt:str=Field(...,min_length=1)
        aspect_ratio:str=Field(default="1:1",description="1:1, 16:9, 9:16, 4:3")
        seed:Optional[int]=Field(default=None)

    @mcp.tool(name="fal_flux_pro",annotations={"readOnlyHint":False})
    async def fal_flux_pro(params:FalProInput)->str:
        """Generate a high-quality image using FLUX Pro on fal.ai."""
        if not cfg.is_configured("fal_key"): return "Error: fal.ai not configured."
        try:
            body={"prompt":params.prompt,"aspect_ratio":params.aspect_ratio,"output_format":"jpeg"}
            if params.seed is not None: body["seed"]=params.seed
            async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
                sr=await client.post("https://queue.fal.run/fal-ai/flux-pro",headers=_h(),json=body)
                sr.raise_for_status()
                rid=sr.json().get("request_id")
                if not rid:
                    # Without a request_id every status poll URL becomes
                    # `…/requests/None/status` which 404s 40 times in a row
                    # and then reports "timed out" — wildly misleading.
                    return f"Error: fal.ai did not return a request_id. Response: {sr.json()}"
                for _ in range(40):
                    await asyncio.sleep(3)
                    st=await client.get(f"https://queue.fal.run/fal-ai/flux-pro/requests/{rid}/status",headers=_h())
                    s=st.json().get("status","")
                    if s=="COMPLETED":
                        rr=await client.get(f"https://queue.fal.run/fal-ai/flux-pro/requests/{rid}",headers=_h())
                        imgs=rr.json().get("images",[])
                        out=f"## fal.ai FLUX Pro\nPrompt: {params.prompt[:80]}\nAspect: {params.aspect_ratio}\n\n"
                        for i,img in enumerate(imgs):
                            out+=f"Image {i+1}: {img.get('url','?')}\n"
                        return out
                    if s=="FAILED": return f"Error: {st.json().get('error','failed')}"
            return "Error: timed out after 120s"
        except Exception as e: return _handle_error(e,"fal.ai FLUX Pro")
