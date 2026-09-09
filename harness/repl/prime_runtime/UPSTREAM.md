# Prime REPL runtime

Copied from https://github.com/PrimeIntellect-ai/prime-agent at
bf8894afa55832f7cfa2094c8a0d041bc680a691 (MIT; see LICENSE).

The Python REPL, shell helper, Windows process helper, and protocol specification
are copied without behavioral changes. The package root is changed to
harness.repl.prime_runtime. Skein supplies the parent protocol client and bootstrap;
Prime's TypeScript manager, agents, providers, and session tree are not included.
