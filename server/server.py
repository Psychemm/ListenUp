"""ListenUp local server: Chatterbox TTS + Ollama text cleanup, streamed as WAV chunks.

Run:  python server.py            (see start-server.ps1 for env setup)
API:
  GET  /health                       -> engine/LLM status
  GET  /voices                       -> list of reference voices in ./voices
  POST /jobs {text,title,options}    -> {id}
  GET  /jobs/{id}                    -> status + segment texts
  GET  /jobs/{id}/chunks/{n}         -> audio/wav (long-polls up to 25 s; 202 = not ready yet, 204 = end)
  DELETE /jobs/{id}                  -> cancel
"""
from __future__ import annotations

import io
import logging
import os
import queue
import threading
import time
import uuid
import wave
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import textprep

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("listenup")

HERE = Path(__file__).resolve().parent
VOICES_DIR = HERE / "voices"
VOICES_DIR.mkdir(exist_ok=True)

ENGINE = os.environ.get("LISTENUP_ENGINE", "turbo")          # turbo | standard
DEVICE = os.environ.get("LISTENUP_DEVICE", "cuda")
OLLAMA_HOST = os.environ.get("LISTENUP_OLLAMA", "http://127.0.0.1:11434")
LLM_MODEL = os.environ.get("LISTENUP_LLM", "qwen2.5:3b")
PORT = int(os.environ.get("LISTENUP_PORT", "8765"))

app = FastAPI(title="ListenUp")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ---------------------------------------------------------------------------
# TTS engine wrapper
# ---------------------------------------------------------------------------
class Engine:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.model: Any = None
        self.sr = 24000
        self.kind = ENGINE
        self.current_voice: str | None = None

    def load(self) -> None:
        import torch
        t0 = time.time()
        if self.kind == "turbo":
            from chatterbox.tts_turbo import ChatterboxTurboTTS
            self.model = ChatterboxTurboTTS.from_pretrained(device=DEVICE)
        else:
            from chatterbox.tts import ChatterboxTTS
            self.model = ChatterboxTTS.from_pretrained(device=DEVICE)
        self.sr = self.model.sr
        log.info("Loaded Chatterbox %s on %s in %.1fs (sr=%d)", self.kind, DEVICE, time.time() - t0, self.sr)
        # Warm-up so the first real request is not paying for CUDA init.
        try:
            with torch.inference_mode():
                self.model.generate("Warming up.")
        except Exception as e:  # noqa: BLE001
            log.warning("warm-up failed: %s", e)

    def synth(self, text: str, opts: dict) -> np.ndarray:
        import torch
        voice = opts.get("voice") or ""
        kwargs: dict[str, Any] = {"temperature": float(opts.get("temperature", 0.8))}
        if voice and voice != "default":
            path = VOICES_DIR / voice
            if not path.exists():
                raise FileNotFoundError(f"voice not found: {voice}")
            if self.current_voice != voice:
                self.model.prepare_conditionals(str(path), exaggeration=float(opts.get("exaggeration", 0.5)))
                self.current_voice = voice
        elif self.current_voice not in (None, "default"):
            # Switch back to the built-in voice by reloading conds.
            self._reload_default_conds()
        if self.kind == "standard":
            kwargs["exaggeration"] = float(opts.get("exaggeration", 0.5))
            kwargs["cfg_weight"] = float(opts.get("cfg_weight", 0.5))
        with torch.inference_mode():
            wav = self.model.generate(text, **kwargs)
        return wav.squeeze(0).cpu().numpy().astype(np.float32)

    def _reload_default_conds(self) -> None:
        from huggingface_hub import snapshot_download
        repo = "ResembleAI/chatterbox-turbo" if self.kind == "turbo" else "ResembleAI/chatterbox"
        local = Path(snapshot_download(repo, allow_patterns=["conds.pt"]))
        mod = __import__("chatterbox.tts_turbo" if self.kind == "turbo" else "chatterbox.tts", fromlist=["Conditionals"])
        self.model.conds = mod.Conditionals.load(local / "conds.pt").to(DEVICE)
        self.current_voice = "default"


engine = Engine()


def to_wav_bytes(audio: np.ndarray, sr: int) -> bytes:
    audio = np.clip(audio, -1.0, 1.0)
    pcm = (audio * 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------
class JobOptions(BaseModel):
    use_llm: bool = True
    llm_model: str | None = None
    voice: str | None = None
    temperature: float = 0.8
    exaggeration: float = 0.5
    cfg_weight: float = 0.5
    max_chars: int = 260


class JobRequest(BaseModel):
    text: str
    title: str | None = None
    options: JobOptions = JobOptions()


class Job:
    def __init__(self, req: JobRequest) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.req = req
        self.segments: list[str] = []
        self.chunks: list[bytes] = []
        self.durations: list[float] = []
        self.done_prep = False
        self.done = False
        self.error: str | None = None
        self.cancel = threading.Event()
        self.cond = threading.Condition()
        self.created = time.time()
        self.llm_used = False
        self.llm_note = ""

    def status(self) -> dict:
        return {
            "id": self.id,
            "title": self.req.title,
            "segments": self.segments,
            "chunks_ready": len(self.chunks),
            "durations": self.durations,
            "prep_done": self.done_prep,
            "done": self.done,
            "error": self.error,
            "llm_used": self.llm_used,
            "llm_note": self.llm_note,
        }


JOBS: dict[str, Job] = {}
JOBS_LOCK = threading.Lock()
ACTIVE: Job | None = None


def _prep_thread(job: Job, seg_queue: "queue.Queue[str | None]") -> None:
    """Clean text batch by batch and push TTS segments as soon as each batch is ready."""
    opts = job.req.options
    text = job.req.text
    if job.req.title:
        text = job.req.title.strip().rstrip(".!?") + ".\n\n" + text
    llm_ok, note = (False, "disabled")
    model = opts.llm_model or LLM_MODEL
    if opts.use_llm:
        llm_ok, note = textprep.ollama_available(OLLAMA_HOST, model)
    job.llm_used = llm_ok
    job.llm_note = note
    try:
        for batch in textprep.batch_paragraphs(text):
            if job.cancel.is_set():
                break
            cleaned = textprep.llm_clean(batch, model, OLLAMA_HOST) if llm_ok else None
            if cleaned is None:
                cleaned = regex = textprep.regex_clean(batch)
            else:
                cleaned = textprep.regex_clean(cleaned)   # tidy any leftovers from the LLM
            first_max = 120 if not job.segments else opts.max_chars
            for seg in textprep.pack_segments(cleaned, max_chars=opts.max_chars, first_max=first_max):
                with job.cond:
                    job.segments.append(seg)
                    job.cond.notify_all()
                seg_queue.put(seg)
    except Exception as e:  # noqa: BLE001
        log.exception("prep failed")
        job.error = f"prep: {e}"
    finally:
        with job.cond:
            job.done_prep = True
            job.cond.notify_all()
        seg_queue.put(None)


def _tts_thread(job: Job, seg_queue: "queue.Queue[str | None]") -> None:
    opts = job.req.options.model_dump()
    try:
        while not job.cancel.is_set():
            seg = seg_queue.get()
            if seg is None:
                break
            t0 = time.time()
            with engine.lock:
                if job.cancel.is_set():
                    break
                audio = engine.synth(seg, opts)
            dur = len(audio) / engine.sr
            log.info("job %s chunk %d: %.1fs audio in %.1fs (%d chars)", job.id, len(job.chunks), dur, time.time() - t0, len(seg))
            with job.cond:
                job.chunks.append(to_wav_bytes(audio, engine.sr))
                job.durations.append(dur)
                job.cond.notify_all()
    except Exception as e:  # noqa: BLE001
        log.exception("tts failed")
        job.error = f"tts: {e}"
    finally:
        with job.cond:
            job.done = True
            job.cond.notify_all()


def start_job(req: JobRequest) -> Job:
    global ACTIVE
    job = Job(req)
    with JOBS_LOCK:
        if ACTIVE is not None and not ACTIVE.done:
            ACTIVE.cancel.set()
            with ACTIVE.cond:
                ACTIVE.cond.notify_all()
        ACTIVE = job
        JOBS[job.id] = job
        # Drop old finished jobs.
        for jid in [j for j, v in JOBS.items() if v.done and time.time() - v.created > 1800]:
            del JOBS[jid]
    q: "queue.Queue[str | None]" = queue.Queue()
    threading.Thread(target=_prep_thread, args=(job, q), daemon=True, name=f"prep-{job.id}").start()
    threading.Thread(target=_tts_thread, args=(job, q), daemon=True, name=f"tts-{job.id}").start()
    return job


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.on_event("startup")
def _startup() -> None:
    threading.Thread(target=engine.load, daemon=True, name="engine-load").start()


@app.get("/health")
def health() -> dict:
    llm_ok, note = textprep.ollama_available(OLLAMA_HOST, LLM_MODEL)
    gpu = None
    try:
        import torch
        if torch.cuda.is_available():
            free, total = torch.cuda.mem_get_info()
            gpu = {"name": torch.cuda.get_device_name(0), "free_mb": free // 2**20, "total_mb": total // 2**20}
    except Exception:  # noqa: BLE001
        pass
    return {
        "ok": True,
        "engine": engine.kind,
        "engine_loaded": engine.model is not None,
        "device": DEVICE,
        "llm_model": LLM_MODEL,
        "llm_ok": llm_ok,
        "llm_note": note,
        "gpu": gpu,
    }


@app.get("/voices")
def voices() -> dict:
    files = sorted(p.name for p in VOICES_DIR.iterdir() if p.suffix.lower() in (".wav", ".mp3", ".flac"))
    return {"voices": ["default", *files]}


@app.post("/jobs")
def create_job(req: JobRequest) -> dict:
    if not req.text or not req.text.strip():
        raise HTTPException(400, "empty text")
    if engine.model is None:
        raise HTTPException(503, "TTS engine still loading")
    job = start_job(req)
    return {"id": job.id}


@app.get("/jobs/{jid}")
def job_status(jid: str) -> dict:
    job = JOBS.get(jid)
    if not job:
        raise HTTPException(404, "no such job")
    return job.status()


@app.get("/jobs/{jid}/chunks/{n}")
def job_chunk(jid: str, n: int) -> Response:
    job = JOBS.get(jid)
    if not job:
        raise HTTPException(404, "no such job")
    deadline = time.time() + 25
    with job.cond:
        while n >= len(job.chunks):
            if job.done or job.cancel.is_set():
                if job.error:
                    raise HTTPException(500, job.error)
                return Response(status_code=204)
            remaining = deadline - time.time()
            if remaining <= 0:
                return Response(status_code=202, content=b'{"status":"pending"}', media_type="application/json")
            job.cond.wait(timeout=remaining)
        data = job.chunks[n]
    return Response(content=data, media_type="audio/wav", headers={"X-Segment-Index": str(n)})


@app.delete("/jobs/{jid}")
def cancel_job(jid: str) -> dict:
    job = JOBS.get(jid)
    if job:
        job.cancel.set()
        with job.cond:
            job.cond.notify_all()
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="info")
