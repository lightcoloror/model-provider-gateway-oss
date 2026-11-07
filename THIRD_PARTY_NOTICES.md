# Third-Party Notices

## Runtime / integrated

### LiteLLM

- Project: <https://github.com/BerriAI/litellm>
- Reviewed installed version: `1.81.7`
- License: MIT outside LiteLLM's enterprise directory.
- Use: protocol and Provider adaptation base. No LiteLLM source is vendored here.

### jsonschema and keyring

- Installed as normal Python dependencies under their upstream licenses.
- Use: JSON Schema validation and OS credential-store abstraction.

## Extracted authority

### Video Knowledge Pipeline (VKP)

- Project: <https://github.com/lightcoloror/video-knowledge-pipeline>
- License: AGPL-3.0.
- Use: this project extracts/adapts Catalog, key-only onboarding, route revision, consent and secretless-config concepts and therefore uses `AGPL-3.0-only`.
- No VKP media, model, runtime profile, credential, Bundle or private fixture is included.

## Design references only; no source copied

### Cline

- Commit: `5594512eb0112155f9d6d74c1330c71b81847dee`
- License: Apache-2.0.
- Reviewed: provider catalog separation and secret-safe deterministic config fingerprint.

### Open WebUI

- Commit: `01f4282f1ffe0d6212f58d3afbeae21fffd0c4be`
- License: custom Open WebUI license with branding restrictions at the reviewed commit.
- Reviewed: OpenAI-compatible connection normalization and normal/advanced UI split.
- Decision: design reference only because its license is not treated as a permissive source for copied code.

### Cherry Studio

- Commit: `de7da76426bd2a1322410d8c4051387a989fd3a6`
- Root license: AGPL-3.0; `packages/provider-registry` declares MIT.
- Reviewed: generated catalog architecture and endpoint capability matrix.
- Decision: design reference only; no source copied.

### CC Switch

- Project: <https://github.com/farion1231/cc-switch>
- Reviewed release: `v3.16.3` (`21e695f` release reference).
- Official source archive SHA-256: `7402059b908657974a452d2e063a9a6b4a25909e045855c2d9fb70922ca40ccc`.
- License: MIT.
- Reviewed: provider preset selector, provider form, advanced-field disclosure and preset tests.
- Verification: 40 focused upstream tests passed from the fixed release source.
- Decision: interaction and architecture reference only. No Tauri, React component or provider preset source is copied into this package.
