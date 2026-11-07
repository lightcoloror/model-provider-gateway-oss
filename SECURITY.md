# Security Policy

## Reporting

Please use GitHub private vulnerability reporting. Do not open a public issue containing a credential, private artifact, customer data, or exploit details.

## Secret handling

- Use `env:<VARIABLE>` or `keyring:<name>` in profiles.
- Never commit `.env`, provider keys, DPAPI ciphertext, cookies, authorization headers, media, transcripts, or consent files created from private data.
- DPAPI support is an optional owner-application adapter. The shared schema never stores encrypted blobs because ciphertext is still sensitive configuration.
- Secrets are resolved only at the final execution boundary and are excluded from profiles, requests, results, ledgers, logs, fixtures, and exceptions.

## Execution boundary

- Canonical remote execution supports only reviewed text and vision profiles through LiteLLM. ASR, OCR, TTS, and embedding use metadata-only owner plan/receipt contracts; their remote execution remains blocked.
- Execution requires an exact provider profile, route plan, consent_manifest.v2, execution request, atomic ledger reservation, timeout, and positive cost ceilings.
- Files are revalidated against exact SHA-256, bytes, MIME, data type, and purpose before transport. Paths, input content, base64 data URLs, authorization material, and raw provider errors are not persisted in result receipts.
- LiteLLM is configured with zero automatic retries. Multi-member routes never trigger automatic fallback.
- Applications embedding the gateway must still enforce their own business authorization and network policy. VKP additionally requires its Broker reservation receipt; the shared gateway does not grant or replace that gate.
- `execution run` can contact a real Provider. Use it only after the owner application has obtained exact user authorization for destination, artifacts, purpose, model, limits, and validity period.

## Supported security reports

Reports about consent replay, route drift, artifact drift, destination expansion, secret disclosure, ledger races, silent fallback, or result receipt tampering are in scope.
