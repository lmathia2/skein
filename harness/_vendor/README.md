# PTC dependencies

Vendored from installed upstream release distributions:

| Package | Release | Source | License |
| --- | --- | --- | --- |
| dill | 0.4.0 | https://github.com/uqfoundation/dill | BSD-3-Clause, `dill/LICENSE` |
| Docker SDK for Python | 7.2.0 | https://github.com/docker/docker-py | Apache-2.0, `docker/LICENSE` |

`SOURCES.json` records SHA-256 hashes of the upstream Python and license files.
Tests, bytecode and distribution metadata are not shipped. Upgrade deliberately:
replace the release sources and hashes together, then run the PTC dependency tests.

Docker's absolute `from docker` imports are relocated to `from harness._vendor.docker`;
all other source bytes are unchanged. The provenance test reverses that exact
relocation before checking upstream hashes. Docker is imported through this private namespace. Its
HTTP dependencies (`requests`, `urllib3`) are already supplied by the base ADK
installation. Docker Engine and an explicit sandbox image remain external runtime
requirements; optional SSH and Windows named-pipe transports are not bundled.

Dill retains its original import name **only in the Prime child process** by putting
this directory first on that child's PYTHONPATH. This preserves existing snapshot
module identities without installing or shadowing `dill` in the parent process.
It remains an unsafe serializer for trusted runtime state, not a sandbox.
