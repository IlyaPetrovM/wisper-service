# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Whisper-based microservice that transcribes **Russian-language** audio to SRT or JSON. It runs
in one of two modes from a single entrypoint, `app/main.py`:

- **Web server** (default): FastAPI app exposing REST endpoints + a Swagger UI.
- **RabbitMQ worker** (`--rabbit-worker`): consumes transcription jobs from a queue.

Both modes share the transcription engine in `app/core.py`.

## Architecture

- `app/main.py` — entrypoint; argparse selects web server vs. `--rabbit-worker`.
- `app/core.py` — the engine. Model registry (`models` dict), lazy loading, caching/downloading,
  `transcribe_audio_core`, file/URL ingestion, SRT generation, and the service-info helpers. All
  real logic lives here; the two front-ends are thin wrappers over it.
- `app/server.py` + `app/fastapi_routes.py` — FastAPI app factory and routes.
- `app/rabbit_interface.py` — `RabbitInterface`: connect/consume, message dispatch for the
  `transcribe` and `load_model` commands, response publishing.
- `app/config.py` / `app/config.yaml` — worker name resolution (`WORKER_NAME` env overrides yaml).
- `app/templates/`, `app/static/` — minimal web UI served at `/`.
- `installer/` — NSIS Windows installer (embeddable Python + ffmpeg); see `readme.md`.

### Things that are easy to get wrong

- **Flat module imports, no package.** Code uses `from core import ...`, `from server import ...`,
  etc. The Dockerfile copies each `app/*.py` flat into `/app`, so the working directory must be
  `app/` (or on `sys.path`). The supported run command is `python main.py` from inside `app/` —
  not `python -m app.main`, and `main.py` does **not** expose a module-level `app` object for
  `uvicorn app.main:app` despite what `installer/scripts/start.bat` implies.
- **GPU-only.** `core.py` hardcodes `device="cuda"` and `compute_type="int8"`. An NVIDIA GPU
  (+ CUDA libs, set via `LD_LIBRARY_PATH` in `docker-compose.yml`) is required. The README's
  "CPU mode" wording and `main.py`'s `--use-cuda` flag are stale and unused.
- **Language is hardcoded to `ru`** in `transcribe_audio_core`.
- **Models must be loaded before transcribing.** Transcribe paths raise
  `"Модель ... не загружена"` if the model isn't already in the in-memory `models` dict. Load it
  first via `POST /api/load_model` (web) or a `load_model` message (worker). Available sizes come
  from the `ModelSize` enum (`small`, `medium`, `large`, plus a Russian-finetuned `LARGE_RUS`).
- **Model cache** lives in `app/models/` (HuggingFace layout) and is mounted as a host volume so
  models survive container restarts.

## Running

```bash
# Docker Compose (production path; requires NVIDIA GPU runtime)
docker-compose up -d
docker-compose logs -f

# Local web server (run from inside app/)
cd app && python main.py            # FastAPI on :8000

# Local RabbitMQ worker
cd app && python main.py --rabbit-worker

# Install deps
pip install -r requirements.txt
```

The image runs as a worker by default (`ENV RABBIT_WORKER=1` in the Dockerfile); set
`RABBIT_WORKER=0` to run the web server instead.

There is no test suite, linter, or build step beyond `docker build`.

## Web API (port 8000)

- `GET /` — web UI; `GET /docs` — Swagger.
- `POST /api/transcribe` — UI endpoint (file upload or `?url=`), returns JSON with logs.
- `POST /transcribe`, `POST /transcribe-url` — return raw SRT/NDJSON.
- `POST /api/load_model?model_size=...` — load a model into memory.
- `GET /health`, `GET /models`, `GET /` (info).

Supported audio extensions: mp3, wav, m4a, flac, ogg, opus, webm.

## RabbitMQ protocol

Configured via env (`RABBIT_HOST/PORT/USER/PASSWORD`, `QUEUE_IN`, `QUEUE_OUT`, `WORKER_NAME`).
Consumes from `QUEUE_IN`, publishes results to `QUEUE_OUT`, `prefetch_count=1` (one job at a time;
transcription runs on a single-worker thread pool). Messages are JSON with a `command` field:

- `{"command": "load_model", "model_size": "small", "task_id": "...", "correlation_id": "..."}`
- `{"command": "transcribe", "file_url": "...", "model_size": "small", "format": "srt|json",
   "task_id": "...", "correlation_id": "..."}`

Responses echo `task_id`/`correlation_id` and carry `status` (`success`/`error`), `worker_id`,
and either `result` or `error`. See `readme.md` for full examples.
