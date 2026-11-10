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

## Artifact lifecycle (optional public core)

Gateway can orchestrate the separately distributed, first-party Apache-2.0
[Artifact Lifecycle Core](https://github.com/lightcoloror/artifact-lifecycle-core-oss)
at commit `fab239085724458c2a6b9452d0a1ba4f4012593e`. The Core is not vendored,
and `LICENSE`, `pyproject.toml`, package version and third-party notices are
unchanged.

From a source checkout, select the Core explicitly and run the synthetic
inspect/plan loop against a new workspace:

```sh
ARTIFACT_LIFECYCLE_CORE_ROOT=/path/to/artifact-lifecycle-core-oss \
python examples/artifact_lifecycle_demo.py \
  --workspace "/path/to/a brand-new synthetic workspace" \
  --core-root /path/to/artifact-lifecycle-core-oss \
  --run-id "synthetic closure 01"
```

The demo refuses an existing workspace, creates only clearly marked synthetic
fixtures, keeps its reports, SQLite checkpoint and an empty quarantine directory
inside that new workspace, and stops after read-only plan verification. The plan
stays `plan_only` with `authorization_state: not_authorized`; owner, process and
dispatch gates remain `unknown`. See
[docs/artifact-lifecycle.md](docs/artifact-lifecycle.md) for the separate source
CLI commands and evidence boundaries.

Run the stdlib CLI checks with the same explicit Core selection:

```sh
ARTIFACT_LIFECYCLE_CORE_ROOT=/path/to/artifact-lifecycle-core-oss \
python tests/test_artifact_lifecycle_cli.py
```

The existing `python -m pytest -q` source-checkout check remains optional: it
needs a compatible `.[dev]` environment and the five baseline pytest cases are
not rerun or claimed by this integration. Source-checkout support only: no
installed Gateway console entry is added for the lifecycle adapter.


## Existing PowerShell build wrapper source

See [the unchanged build wrapper source and operator prerequisites](docs/build-wrapper-source.md). It depends on the caller working directory and current Python environment; the earlier Windows runtime refusal was not retried and no successful wrapper build is claimed.
