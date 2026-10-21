"""Modal GPU side of generated footage: weights Volume, FLUX.1-schnell keyframes, LTX-2.5 image-to-video.

    uv run modal run celery_man/gen_modal.py::check_access     # HF auth on both repos
    uv run modal run celery_man/gen_modal.py::smoke   # one keyframe + one clip -> smoke/

Draft clips run only stage 1 of the LTX-2.5 distilled recipe and decode at 960x544, the cut's own size.
(paper-machine's footage/gen_modal.py has the hq stage-2 path if it is ever needed.)
One small GPU, one container: the pipeline offloads to CPU RAM between components, so it does not need an 80 GB card.
"""

import io
import logging
import tempfile
import time
from pathlib import Path

import modal

LTX = "Lightricks/LTX-2.5-Diffusers"
FLUX = "black-forest-labs/FLUX.1-schnell"
# diffusers folders only; skips the 23 GB single-file flux1-schnell.safetensors at the repo root
FLUX_PATTERNS = [
    "model_index.json",
    *(f"{d}/*" for d in ("scheduler", "text_encoder", "text_encoder_2", "tokenizer", "tokenizer_2", "transformer", "vae")),
]
# Only what image-to-video needs; skips transformer_full/ and diffusion_decoder/ (the conv VAE decodes).
LTX_PATTERNS = [
    "model_index.json",
    *(
        f"{d}/*"
        for d in (
            "scheduler",
            "text_encoder",
            "tokenizer",
            "vae",
            "transformer",
            "latent_upsampler",
            "audio_vae",
            "vocoder",
            "connectors",
            "duration_head",
        )
    ),
]
W, H, FPS, FRAMES = 960, 544, 24, 121
ANIMATOR_GPU = "L4"  # smallest card that works with group offload: 960x544 draft in ~175 s (2026-10-17)
ANIMATOR_MEMORY_GIB = 88  # CPU RAM holds the offloaded weights (77 GiB peak measured); Modal bills $0.008/GiB/h

DIFFUSERS_SHA = "b800f828f3951c4d206e154411bcbfa693cd322e"  # huggingface/diffusers main on 2026-10-17; LTX-2.5 is not in a release
image = modal.Image.debian_slim(python_version="3.12").pip_install(
    "torch",
    "transformers",
    "accelerate",
    "sentencepiece",
    "av",  # diffusers encode_video and LTX-2.5 image CRF re-compression use PyAV
    "imageio[ffmpeg]",
    f"diffusers @ https://github.com/huggingface/diffusers/archive/{DIFFUSERS_SHA}.zip",
)
app = modal.App("celery-man", image=image)
weights = modal.Volume.from_name("celery-man-weights", create_if_missing=True)
hf = modal.Secret.from_name("huggingface")
log = logging.getLogger(__name__)


@app.function(secrets=[hf])
def check_access() -> dict[str, str]:
    from huggingface_hub import auth_check
    from huggingface_hub.errors import HfHubHTTPError

    out = {}
    for repo in (LTX, FLUX):
        try:
            auth_check(repo)
            out[repo] = "OK"
        except HfHubHTTPError as e:  # GatedRepoError / RepositoryNotFoundError carry the reason
            out[repo] = f"{type(e).__name__}: {e}"
    return out


@app.function(secrets=[hf], volumes={"/weights": weights}, cpu=4, memory=8192, timeout=3 * 3600)
def download_weights() -> None:
    from huggingface_hub import snapshot_download

    for repo, patterns in ((FLUX, FLUX_PATTERNS), (LTX, LTX_PATTERNS)):
        dest = Path("/weights") / repo
        if (dest / "model_index.json").exists():
            log.info("%s already present", repo)
            continue
        snapshot_download(repo, local_dir=dest, allow_patterns=patterns)
        weights.commit()


@app.cls(gpu="L40S", volumes={"/weights": weights}, timeout=900, scaledown_window=30, max_containers=1)
class Keyframer:
    @modal.enter()
    def load(self) -> None:
        import torch
        from diffusers import FluxPipeline

        self.pipe = FluxPipeline.from_pretrained(f"/weights/{FLUX}", torch_dtype=torch.bfloat16).to("cuda")

    @modal.method()
    def still(self, prompt: str, seed: int) -> bytes:
        import torch

        # schnell is timestep-distilled: 4 steps, no guidance, T5 capped at 256 tokens; 960x544 is a multiple of 16
        img = self.pipe(
            prompt=prompt,
            height=H,
            width=W,
            num_inference_steps=4,
            max_sequence_length=256,
            guidance_scale=0.0,
            generator=torch.Generator("cuda").manual_seed(seed),
        ).images[0]
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()


@app.cls(
    gpu=ANIMATOR_GPU, volumes={"/weights": weights}, memory=ANIMATOR_MEMORY_GIB * 1024, timeout=3600, scaledown_window=30, max_containers=1
)
class Animator:
    @modal.enter()
    def load(self) -> None:
        import torch
        from diffusers import LTX2ImageToVideoPipeline

        path = f"/weights/{LTX}"
        # model_index lists processor + prompt_enhancer but the repo ships neither; both are optional
        self.pipe = LTX2ImageToVideoPipeline.from_pretrained(path, processor=None, prompt_enhancer=None, dtype=torch.bfloat16)
        # stream weights through the GPU layer by layer: the transformer alone is bigger than a 48 GB card
        self.pipe.enable_group_offload(
            onload_device=torch.device("cuda"),
            offload_device=torch.device("cpu"),
            offload_type="leaf_level",
            use_stream=True,
            low_cpu_mem_usage=True,
        )
        self.pipe.vae.enable_tiling()  # keeps the 121-frame decode inside L4 memory

    @modal.method()
    def clip(self, keyframe: bytes, prompt: str, seed: int) -> dict:
        import resource

        import PIL.Image
        import torch
        from diffusers.pipelines.ltx2.utils import DEFAULT_NEGATIVE_PROMPT, DISTILLED_SIGMA_VALUES
        from diffusers.utils import encode_video

        t0 = time.monotonic()
        shared = {
            "image": PIL.Image.open(io.BytesIO(keyframe)).convert("RGB"),
            "prompt": prompt,
            "negative_prompt": DEFAULT_NEGATIVE_PROMPT,
            "num_frames": FRAMES,
            "frame_rate": float(FPS),
            "guidance_scale": 1.0,  # distilled: unguided
            "audio_guidance_scale": 1.0,
            "stg_scale": 0.0,
            "audio_stg_scale": 0.0,
            "modality_scale": 1.0,
            "audio_modality_scale": 1.0,
            "generator": torch.Generator("cuda").manual_seed(seed),
            "return_dict": False,
        }
        video, _audio = self.pipe(height=H, width=W, sigmas=DISTILLED_SIGMA_VALUES, output_type="np", **shared)
        out = Path(tempfile.mkdtemp()) / "clip.mp4"
        encode_video(video[0], fps=FPS, output_path=str(out))  # video only; assembly drops audio anyway
        rss_gib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20  # ru_maxrss is KiB on Linux
        return {"mp4": out.read_bytes(), "wall_s": time.monotonic() - t0, "max_rss_gib": rss_gib}


@app.local_entrypoint()
def access() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for repo, status in check_access.remote().items():
        log.info("%s: %s", repo, status)


@app.local_entrypoint()
def smoke(seed: int = 7) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    prompt = "a paper cup on a wooden table, soft daylight, locked camera"
    outdir = Path("smoke")
    outdir.mkdir(exist_ok=True)
    download_weights.remote()
    t0 = time.monotonic()
    png = Keyframer().still.remote(prompt, seed)
    (outdir / "key.png").write_bytes(png)
    log.info("keyframe: %.1f s round trip incl. cold start", time.monotonic() - t0)
    t0 = time.monotonic()
    res = Animator().clip.remote(png, prompt, seed)
    (outdir / f"clip-{ANIMATOR_GPU}.mp4").write_bytes(res["mp4"])
    log.info(
        "clip on %s: wall_s=%.1f, round trip %.1f s, max RSS %.1f GiB",
        ANIMATOR_GPU,
        res["wall_s"],
        time.monotonic() - t0,
        res["max_rss_gib"],
    )
