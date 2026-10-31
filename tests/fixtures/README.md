# Test fixtures

| File | Source |
| --- | --- |
| `physical.json` | Captured from a host with an Intel iGPU using `intel_gpu_top` 2.3 with `-p`, on an idle GPU. Four samples. |
| `class.json` | The same capture setup without `-p`, so engine keys have no instance suffix. Four samples. |

Both have an empty `clients` section and no `imc-bandwidth`. `../data.json` is an older capture and the only one with `imc-bandwidth`.

Any fixture written by hand is named `handwritten-*.json` and is replaced with a real capture during hardware validation.
