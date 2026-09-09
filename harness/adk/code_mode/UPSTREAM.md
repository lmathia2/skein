# Vendored ADK Code Mode

Vendored from `a2anet/adk-code-mode` 1.6.0 at commit
`e24b92fc3b38cd98b5f36ea0f3deaeddf32d2b39` under Apache-2.0.

Skein changes the import root to `harness.adk.code_mode` and runs it on the
repository's ADK 2.7.x pin. The upstream suite passed 198 of 199 tests on ADK
2.8; the sole failure asserted ADK 1's deprecated schema field while ADK 2
correctly populated `parameters_json_schema`. Skein's integration test covers
the ADK 2 tool declaration and worker assembly used here.

The Docker backend now imports the SDK from `harness._vendor.docker`, removing its
PTC-specific installation requirement without changing container lifecycle logic.
