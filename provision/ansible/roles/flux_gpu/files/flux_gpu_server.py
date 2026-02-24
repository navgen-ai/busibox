#!/usr/bin/env python3
"""
OpenAI-compatible image generation server for FLUX.2 on NVIDIA GPUs.

Mirrors the API contract of mlx-openai-server so that LiteLLM can route
/v1/images/generations requests identically on both MLX and GPU backends.

Usage:
    python flux_gpu_server.py --model black-forest-labs/FLUX.2-klein-4B \
                              --port 8008 --quantize 8bit
"""

import argparse
import base64
import io
import logging
import time
from contextlib import asynccontextmanager
from typing import Optional

import torch
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from PIL import Image
from pydantic import BaseModel, Field

logger = logging.getLogger("flux-gpu")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

pipe = None
model_id = None


def _parse_size(size: str) -> tuple[int, int]:
    """Parse 'WxH' size string, default 1024x1024."""
    try:
        w, h = size.lower().split("x")
        return int(w), int(h)
    except Exception:
        return 1024, 1024


def _load_pipeline(model: str, quantize: str):
    from diffusers import FluxPipeline

    logger.info("Loading FLUX pipeline: %s (quantize=%s)", model, quantize)
    kwargs = {"torch_dtype": torch.bfloat16}

    if quantize == "8bit":
        from diffusers import BitsAndBytesConfig
        kwargs["quantization_config"] = BitsAndBytesConfig(load_in_8bit=True)
    elif quantize == "4bit":
        from diffusers import BitsAndBytesConfig
        kwargs["quantization_config"] = BitsAndBytesConfig(load_in_4bit=True)

    pipeline = FluxPipeline.from_pretrained(model, **kwargs)

    if quantize not in ("8bit", "4bit"):
        pipeline = pipeline.to("cuda")

    pipeline.set_progress_bar_config(disable=True)
    logger.info("FLUX pipeline loaded successfully")
    return pipeline


class ImageRequest(BaseModel):
    model: str = "flux2-klein-4b"
    prompt: str
    size: str = "1024x1024"
    n: int = Field(default=1, ge=1, le=4)
    response_format: Optional[str] = "b64_json"
    num_inference_steps: Optional[int] = None
    guidance_scale: Optional[float] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pipe, model_id
    pipe = _load_pipeline(model_id, app.state.quantize)
    yield
    del pipe
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


app = FastAPI(title="Flux GPU Image Server", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok", "model": model_id, "device": "cuda"}


@app.get("/v1/models")
async def list_models():
    return {
        "object": "list",
        "data": [{
            "id": model_id,
            "object": "model",
            "owned_by": "black-forest-labs",
        }],
    }


@app.post("/v1/images/generations")
async def generate_images(request: ImageRequest):
    if pipe is None:
        raise HTTPException(status_code=503, detail="Model not loaded")

    width, height = _parse_size(request.size)

    gen_kwargs = {
        "prompt": request.prompt,
        "width": width,
        "height": height,
        "num_images_per_prompt": request.n,
    }
    if request.num_inference_steps is not None:
        gen_kwargs["num_inference_steps"] = request.num_inference_steps
    if request.guidance_scale is not None:
        gen_kwargs["guidance_scale"] = request.guidance_scale

    t0 = time.time()
    try:
        with torch.inference_mode():
            result = pipe(**gen_kwargs)
    except Exception as e:
        logger.error("Generation failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    elapsed = time.time() - t0

    data = []
    for img in result.images:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        data.append({"b64_json": b64})

    logger.info(
        "Generated %d image(s) in %.1fs (%dx%d, prompt=%.60s...)",
        len(data), elapsed, width, height, request.prompt,
    )

    return JSONResponse(content={
        "created": int(time.time()),
        "data": data,
    })


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Flux GPU Image Server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8008)
    parser.add_argument("--model", default="black-forest-labs/FLUX.2-klein-4B")
    parser.add_argument("--quantize", default="8bit", choices=["none", "8bit", "4bit"])
    args = parser.parse_args()

    model_id = args.model
    app.state.quantize = args.quantize
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
