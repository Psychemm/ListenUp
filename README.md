# ListenUp

Read any web page, or just the text you highlighted, aloud with a **local** voice.
A Brave/Chrome extension sends the text to a small server on your PC, a lightweight
LLM rewrites it so it reads well (numbers, units, symbols, citations, URLs), and
[Chatterbox](https://github.com/resemble-ai/chatterbox) turns it into speech. Nothing
leaves your machine.

```
Brave extension ──text──▶ server.py ──batches──▶ Ollama (qwen2.5:3b) ──clean text──▶ Chatterbox Turbo ──WAV chunks──▶ extension plays them
```

Audio starts after the first short sentence is ready and streams sentence by sentence
while the rest generates in the background.

## Requirements

- Windows/Linux/macOS, Python 3.10–3.12, an NVIDIA GPU with 4+ GB free VRAM
  (CPU works but is slow).
- [Ollama](https://ollama.com) running locally with a small non-reasoning model
  (default `qwen2.5:3b`). Optional: without it the server falls back to regex cleanup.
- Brave, Chrome, Edge or any Chromium browser (Manifest V3, offscreen documents).

## Server setup

```bash
python -m venv venv
venv/Scripts/activate            # or: source venv/bin/activate
# torch for YOUR CUDA. RTX 50-series (Blackwell) needs cu128 or newer:
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu130
# chatterbox pins torch==2.6.0 (no Blackwell kernels), so skip its deps:
pip install chatterbox-tts --no-deps
pip install -r server/requirements.txt
pip install hf_transfer          # optional, much faster first download (~3 GB)
ollama pull qwen2.5:3b
```

Start it (Windows helper sets cache dirs and env vars):

```powershell
.\start-server.ps1                       # Chatterbox Turbo, qwen2.5:3b, port 8765
.\start-server.ps1 -Engine standard      # original Chatterbox (exaggeration/CFG knobs)
.\start-server.ps1 -Llm gemma3:4b        # another Ollama model for cleanup
```

Or directly: `LISTENUP_ENGINE=turbo LISTENUP_LLM=qwen2.5:3b python server/server.py`.
First launch downloads the model from Hugging Face into `HF_HOME`.

### Voice cloning

Drop a 5–15 s clean `.wav` of a voice into `server/voices/` and pick it in the
extension's Settings. `default` is Chatterbox's built-in voice.

## Extension setup

1. Open `brave://extensions` (or `chrome://extensions`).
2. Turn on **Developer mode** (top right).
3. **Load unpacked** → choose the `extension/` folder.
4. Pin ListenUp to the toolbar.

Use it via the toolbar popup, the right-click menu ("ListenUp: read selection" /
"read this page"), or the shortcuts **Alt+Shift+L** (read selection, or the page if
nothing is selected) and **Alt+Shift+P** (pause/resume).

## How the text pipeline works

1. `content.js` grabs the selection, or finds the main article container (article/main,
   else the densest cluster of paragraphs), skipping nav, footers, sidebars, comments,
   code blocks and hidden elements.
2. The server batches paragraphs (a small first batch for fast start), sends each batch
   to Ollama with a "prepare for narration" system prompt, sanity-checks the output
   length and falls back to regex normalisation if the LLM is off or misbehaves.
3. `textprep.regex_clean` always runs afterwards (URLs, `[12]` citations, `$`, `%`,
   `&`, units like GB/GHz, smart quotes, dangling "See." fragments).
4. Text is split into sentences and packed into ≤260-character segments that never
   cross a paragraph boundary; each segment becomes one WAV chunk.
5. The extension's offscreen document long-polls `/jobs/{id}/chunks/{n}` and keeps one
   chunk prefetched so playback has no gaps.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | engine/LLM/GPU status |
| GET | `/voices` | reference voices in `server/voices` |
| POST | `/jobs` | `{text, title, options{use_llm, llm_model, voice, temperature, exaggeration, cfg_weight, max_chars}}` → `{id}` |
| GET | `/jobs/{id}` | status, segment texts, chunks ready |
| GET | `/jobs/{id}/chunks/{n}` | `audio/wav`; 202 = still generating (retry), 204 = end |
| DELETE | `/jobs/{id}` | cancel |

Starting a new job cancels the previous one (one GPU, one reader).

## Notes

- Qwen3-family models keep "thinking" even with `think: false` on Ollama, which
  turns a 1-second cleanup into a minute. Use a non-reasoning model (qwen2.5, gemma3,
  llama3.2) for the cleanup step.
- Chatterbox Turbo ignores `exaggeration`/`cfg_weight`; switch to `-Engine standard`
  if you want those controls.
- Measured on an RTX 5070 Ti: Turbo generates ~3× faster than real time.
