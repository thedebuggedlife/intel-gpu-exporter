# Test fixtures

| File | Source |
| --- | --- |
| `physical.json` | Captured from a host with an Intel iGPU using `intel_gpu_top` 2.3 with `-p`, on an idle GPU. Four samples. |
| `class.json` | The same capture setup without `-p`, so engine keys have no instance suffix. Four samples. |
| `loaded.json` | Captured from a host with an Intel iGPU using `intel_gpu_top` 2.3 with `-J -p`, during a 20-second synthetic encode at a 2 s period, and stopped with `SIGTERM`. Fifteen samples. The client's PID is a placeholder. |
| `handwritten-truncated.json` | Written by hand: the first sample of `physical.json`, then the second cut off partway through. |
| `handwritten-malformed.json` | Written by hand: the first three samples of `physical.json`, with the second broken by a missing colon. |
| `handwritten-multi-gt.json` | Written by hand from the `intel_gpu_top` 2.3 source: two samples from a two-GT device with `-p`. The values are invented. |

`physical.json` and `class.json` have an empty `clients` section. None of the 2.3 captures has `imc-bandwidth`: `../data.json` is an older capture and the only one that does.

`loaded.json` is byte-for-byte what the tool wrote, so don't reformat it. Its first ten samples have one client, and it is the only fixture with commas between samples.

Any fixture written by hand is named `handwritten-*.json` and is replaced with a real capture during hardware validation, unless it shows output a working tool can't produce, as `handwritten-malformed.json` does.
