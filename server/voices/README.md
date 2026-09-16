# Reference voices

Drop any clean 5–15 s mono recording here (`.wav`, `.mp3`, `.flac`) and it appears in the
extension's Voice list. Chatterbox clones the timbre and pacing zero-shot. Names ending in
`_m` / `_f` are the speaker's apparent pitch range (male / female), not a verified label.

Bundled samples were converted to 24 kHz mono, silence-trimmed and peak-normalised.

| File | Source | Licence |
|---|---|---|
| `cori_samuel_f.wav`, `phil_benson_m.wav`, `john_van_stan_m.wav` | [Hi-Fi TTS](https://huggingface.co/datasets/MikhailT/hifi-tts) (LibriVox readers 92, 6097, 9017) | CC BY 4.0 |
| `libritts_*_{m,f}.wav` | [LibriTTS-R](https://huggingface.co/datasets/mythicinfinity/libritts_r), `dev.clean` speakers by ID | CC BY 4.0 |
| `mina.wav`, `shadow_female.wav` | user-supplied | – |

The underlying audiobook recordings are LibriVox public-domain readings; the dataset
packaging is CC BY 4.0 (attribution above).
