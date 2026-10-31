# Test fixtures

| File | Source |
| --- | --- |
| `physical.json` | Captured from a host with an Intel iGPU using `intel_gpu_top` 2.3 with `-p`, on an idle GPU. Four samples. |
| `class.json` | The same capture setup without `-p`, so engine keys have no instance suffix. Four samples. |
| `handwritten-truncated.json` | Written by hand: the first sample of `physical.json`, then the second cut off partway through. |
| `handwritten-malformed.json` | Written by hand: the first three samples of `physical.json`, with the second broken by a missing colon. |

Both have an empty `clients` section and no `imc-bandwidth`. `../data.json` is an older capture and the only one with `imc-bandwidth`.

Any fixture written by hand is named `handwritten-*.json` and is replaced with a real capture during hardware validation, unless it shows output a working tool can't produce, as `handwritten-malformed.json` does.
