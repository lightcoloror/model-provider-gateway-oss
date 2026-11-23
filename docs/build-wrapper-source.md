# Gateway Build Wrapper Source Guide

## Scope & Operational Preconditions
`scripts/build-package.ps1` (frozen at 552 bytes, AGPL-3.0-only) provides a first-party build wrapper for `model-provider-gateway`. Publication distributes the source statically without requiring historical Windows runtime reconstruction. An earlier PowerShell runtime authorization refusal remains recorded privately. This batch did not retry it, change security settings or use a different host to execute it; no claim is made that the historical process was fully reviewed here. The script is not certified runnable, build success is not asserted, and Windows portability remains unverified.

## Execution Constraints & Environment Sensitivity
- **Working Directory & Paths**: `OutputDir` defaults to `dist`. The script never changes directory to `RepoRoot`, making execution sensitive to the caller's working directory. `RepoRoot` derived from `PSScriptRoot` is used exclusively for `.local\build-tmp`.
- **Temp Handling**: `.local\build-tmp` is reused across runs without isolation, concurrency protection, or sandboxing.
- **Environment Restoration**: The source saves TEMP/TMP values and attempts to assign those saved values in finally. Restoration success, assignment failures and empty/missing-state behavior were not tested. The wrapper does not select or change its own working directory.
- **Python & Dependencies**: Invokes PATH-resolved `python` with `-m build --no-isolation`, requiring pre-installed backend dependencies (`python>=3.11`, `setuptools>=78`, `wheel`, `build>=1.2,<2`). Flags do not guarantee offline builds or backend trustworthiness. A nonzero Python result causes a throw with that value in its message; the exact outer PowerShell process exit code was not verified.

## Verification Boundaries
Owner verified exact frozen-source bytes and reused the complete prior source review. The source LICENSE bytes equal the existing public AGPL-3.0-only LICENSE and the public pyproject declares that license; this is not blanket third-party distribution clearance. No PowerShell parsing, package build, installation, build/runtime network access or acceptance tests were executed in this batch. Final frozen-delta hashes, references, privacy checks and default-rule secret scanning are separate from runtime acceptance.

No current synthetic build stub, service, provider or Gateway execution test was run. Earlier Python-core/lifecycle results belong to a different increment and do not validate this wrapper. Actual builds can create outputs and run a backend; source publication does not grant permission to execute them. No new dependencies, credentials, account configuration, state, media or private history are distributed.
