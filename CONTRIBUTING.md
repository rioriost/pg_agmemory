# Contributing

[日本語](CONTRIBUTING-jp.md) | [README](README.md) | [Current contract](docs/STATUS.md)

Contributions to `pgag_memory` are made under the project's [MIT license](LICENSE).
The Python package/service is `pg_agmemory`. Git is already initialized; work
on a branch rather than reinitializing the repository. This is an initial M1
slice, not a completed MVP. Read [ADR 0001](docs/adr/0001-initial-slice.md) before
changing authorization, evidence, transactions, or deletion.

## Container-first validation

Local validation uses **Apple Container**, not Docker Desktop or a silent host
fallback. Install/start Apple Container and ensure `jq` is available, then run
from the repository root:

```bash
container system start
./scripts/test-containers.sh
```

The script builds the test image, runs Ruff, mypy, unit tests and PostgreSQL
integration tests, then builds and starts the non-root runtime image and checks
HTTP liveness. It uses separate disposable PostgreSQL containers for tests and
the startup smoke check and cleans up its own resources. Do not aim tests,
schema resets, purge drills, or restore experiments at a persistent/shared DB.
`PGAG_TEST_DATABASE_URL` is for disposable test data only; prefer letting the
script create it rather than supplying a personal database.

GitHub Actions runs the same script with **Docker on native `linux/amd64` and
`linux/arm64` runners**, not build-only or emulation-only checks:

```bash
./scripts/test-containers.sh docker
```

That command is the CI path, not the local default. A single local run does not
prove both CI architectures passed. Report the actual engine, architecture,
command, and result; identify unavailable checks rather than claiming success.
No model API key is required. Initial runs need access to pinned images and
Python packages. Change dependency manifests and lock data together when needed;
do not loosen pins or add unrelated tools to bypass a failing check.

## Change scope and documentation

- Keep patches focused and add regression tests for changed behavior. Preserve
  tenant/scope isolation, current authorization on replay, literal same-scope
  episode evidence, commit-before-send, and the deletion drain protocol.
- Do not advertise workers, job enqueue, vector/graph search, checkpoints,
  corrections, SDKs, or other roadmap features before implementation and
  validation. Do not replace null confidence or byte-budget caveats with
  unsupported truth/token guarantees.
- Update English and Japanese documentation **in the same change**, including
  matching examples, limits, status, and operational caveats. Maintain reciprocal
  links and use the existing `-jp.md` names. Distinguish design targets from
  implemented behavior and measured results.
- OpenAPI is exposed at runtime through `/openapi.json`; no generated
  documentation files are required. Update source models and explain contract
  changes in the paired documentation.
- Schema changes belong in the packaged PostgreSQL storage resources with an
  explicit migration approach. Do not silently edit an already-applied schema
  version and assume rerunning migration will upgrade an existing DB.

## Safe submissions

Use synthetic fixtures only. Do not commit tokens, passwords, signing keys,
private connection URLs, personal conversations, customer data, or copied
production backups. Do not send private data to third-party services. Sanitize
logs and screenshots before including them in issues or pull requests.

In a pull request, summarize the change and limitations, list actual validation
results, and link related issues or ADRs. Record remaining checks honestly.
Claims of full erasure, DR, production readiness, performance, or memory quality
require separate evidence; a passing health probe or container build is not
that evidence. Keep dependency/dataset licensing separate from the project's
MIT license and do not add unlicensed or sensitive fixtures.
