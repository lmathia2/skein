# PTC dependencies

Vendored from installed upstream release distributions:

| Package | Release | Source | License |
| --- | --- | --- | --- |
| dill | 0.4.0 | https://github.com/uqfoundation/dill | BSD-3-Clause, `dill/LICENSE` |

`SOURCES.json` records SHA-256 hashes of the upstream Python and license files.
Tests, bytecode and distribution metadata are not shipped. Upgrade deliberately:
replace the release sources and hashes together, then run the PTC dependency tests.

Dill retains its original import name **only in the Prime child process** by putting
this directory first on that child's PYTHONPATH. This preserves existing snapshot
module identities without installing or shadowing `dill` in the parent process.
It remains an unsafe serializer for trusted runtime state, not a sandbox.
