# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately, through GitHub's [Report a vulnerability](https://github.com/thedebuggedlife/intel-gpu-exporter/security/advisories/new) form. Don't open a public issue.

Include the image tag or digest, what you did and what happened. This is a volunteer project, so there's no guaranteed response time. A confirmed problem is fixed in a new release, and the advisory is published once that release is out.

## Supported versions

| Version | Supported |
| --- | --- |
| The newest release | Yes |
| Any older release | No. Update to the newest release |
| Upstream's `rolling` image | No. It no longer receives fixes |

Fixes go into a new release, not into older ones, and a published `X.Y.Z` tag never changes. `latest` moves to the new release, and `X.Y` moves if the release is in that series, as the [update policy](README.md#tags) describes.

## Scope

In scope are the exporter, the image and the release automation in this repository.

These aren't vulnerabilities in this project:

- **The listening port has no authentication.** That's by design, and it must stay on a trusted network.
- **A setup that grants more than the exporter needs.** [Permissions](README.md#permissions) lists what each grant allows.
- **A flaw in `intel_gpu_top` itself.** Please report those to the [igt-gpu-tools](https://gitlab.freedesktop.org/drm/igt-gpu-tools) project as well, because that's where a fix has to happen. Scanners can't see this binary, so tell us too if it affects this image.
