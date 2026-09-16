# Starts the ListenUp TTS server. Keeps caches on A: because C: is nearly full.
param(
    [ValidateSet("turbo", "standard")] [string] $Engine = "turbo",
    [string] $Llm = "qwen2.5:3b",
    [int] $Port = 8765
)
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$env:HF_HOME = "A:\LLM SHIT\hf-cache"
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = "1"
$env:TMP = "$root\tmp"; $env:TEMP = "$root\tmp"
$env:LISTENUP_ENGINE = $Engine
$env:LISTENUP_LLM = $Llm
$env:LISTENUP_PORT = $Port
$env:TQDM_DISABLE = "1"
$env:HF_HUB_ENABLE_HF_TRANSFER = "1"
New-Item -ItemType Directory -Force "$root\tmp" | Out-Null
& "$root\venv\Scripts\python.exe" "$root\server\server.py"
