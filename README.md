# Model Provider Gateway

Local-first provider catalog, key references, route revisions, consent and
execution contracts. An execution plan binds the selected capability,
provider, limits and input evidence. Adapters for local owner runtimes and
optional external providers are configured separately.

## Install and inspect

```sh
python -m pip install .
model-provider-gateway --help
model-provider-gateway catalog list
```

The catalog is local metadata. Remote execution requires an explicitly
configured provider, credentials and a matching consent record. Use
`python -m pip install ".[execution]"` for the optional LiteLLM adapter,
or `".[ui]"` for the optional local console dependencies.

From the source checkout, install `".[dev,ui]"` and run
`python -m pytest -q` for offline checks. The optional LiteLLM test needs
the execution extra. Fixtures use synthetic inputs and mock transports.
Keep credential material, provider profiles and execution records outside
version control.

This source edition adapts documentation and replaces deployment-specific
evidence paths with paths relative to the corresponding consumer.
[License](LICENSE): AGPL-3.0-only. See [third-party notices](THIRD_PARTY_NOTICES.md).
