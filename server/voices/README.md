# Reference voices

Drop any clean 5–15 s mono recording here (`.wav`, `.mp3`, `.flac`) and it appears in the
extension's Voice list. Chatterbox clones the timbre and pacing zero-shot. Names ending in
`_m` / `_f` are the speaker's apparent pitch range (male / female), not a verified label.

Bundled samples were converted to 24 kHz mono, silence-trimmed and peak-normalised.

| File | Source | Licence |
|---|---|---|
| `cori_samuel_f.wav`, `phil_benson_m.wav`, `john_van_stan_m.wav` | [Hi-Fi TTS](https://huggingface.co/datasets/MikhailT/hifi-tts) (LibriVox readers 92, 6097, 9017) | CC BY 4.0 |
| `ava_f` (1673), `emma_f` (1919), `olivia_f` (2035), `liam_m` (2803), `noah_m` (2902), `james_m` (3752), `sophia_f` (5895), `mia_f` (7850), `charlotte_f` (7976), `isabella_f` (84) | [LibriTTS-R](https://huggingface.co/datasets/mythicinfinity/libritts_r) `dev.clean`, LibriTTS speaker ID in parentheses; the first names are placeholders, not the readers' names | CC BY 4.0 |
| `mina.wav`, `shadow_female.wav` | user-supplied | – |

The underlying audiobook recordings are LibriVox public-domain readings; the dataset
packaging is CC BY 4.0 (attribution above).
