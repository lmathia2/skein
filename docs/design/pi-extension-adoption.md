# Pi Extension Adoption Decisions

This note records which Pi and adjacent extension patterns belong in this harness,
which remain optional, and the evidence required before expanding the model-facing
interface.

## Decision summary

| Capability | Decision | Harness shape |
|---|---|---|
| Structured compaction | Adopt | Deterministic ledger snapshot, chained prior summary, recent raw tail, cumulative file state, and recoverable artifact identifiers |
| Generic token compaction | Retain as backstop | ADK event compaction is opt-in for interval/overlap and otherwise protects only the context ceiling |
| Structural code indexing | Adopt as the local default | Immutable, bounded repository snapshots with syntax-aware Python parsing and explicitly labeled fallbacks |
| FFF lexical/path index | Adopt behind `bash` | Locked native Python binding, lazy workspace index, strict virtual grammar, harness-owned grouped pagination and cursors |
| LSP or Moderne/LST enrichment | Support as an optional provider | Disabled-by-default, fingerprint-scoped, read-only operator CLI contract; never a new model-visible tool |
| Programmatic tool calling | Adopt routing guidance first | Progressive `programmatic-tool-routing` skill composes deterministic high-fanout work through `bash` |
| Sandboxed `code_execution` meta-tool | Defer behind an ablation | Add only if paired evaluation improves system-level outcomes without weakening safety or traceability |
| Arbitrary Pi extension loading | Do not adopt | Translate trusted capabilities into skills, approved CLIs, or deterministic workflow nodes |

## Compaction

Pi's useful contract is continuity, not merely a shorter transcript: preserve a recent
raw tail, chain the previous summary, track files cumulatively, and never make the
summary the only durable copy of important evidence. The harness implements that
contract in `harness/core/context/compaction.py`. Full outputs remain in the artifact
store, while a bounded set of content-addressed identifiers survives repeated
compactions. ADK compaction remains a ceiling backstop rather than the primary policy.

The harness does not import Pi's extension hooks or session format. Its own append-only
control stream, checkpoints, receipts, and deterministic prefix hashes remain the
source of truth.

## Repository intelligence

FFF is the repository discovery path: grouped lexical grep and fuzzy filename search
without injecting a repository-wide map. The compact manifest retains only build and
verification metadata.

FFF provides that discovery with fast lexical grep and fuzzy filename search.
The pinned Python binding is installed by the project lockfile, so cloning developers
do not install a Pi extension, npm package, or separate search binary. A reserved
`search grep|find|health` grammar is routed inside the managed `bash` adapter. It is
lazy, workspace-rooted, symlink-disabled, post-confined, redacted, and unavailable for
remote workspaces that are not authoritative on the host.

FFF's native grep cursor advances by file and its page limit may return a whole file
group beyond the requested count. The harness therefore drains a bounded snapshot and
owns exact model-facing pages. Per-page scheduling admits only a bounded number of
matches from each file before considering the next, preventing a common token flood
from burying relevant paths. Content-addressed cursors bind the workspace, operation,
query hash, options, and matched-file hashes; changed files make a cursor stale.

Language servers and Moderne Lossless Semantic Trees can materially improve
cross-language definitions, references, implementations, and multi-repository
architecture discovery. They also introduce installation, build, licensing,
freshness, and sometimes network or mutation concerns. The provider-neutral contract
in `harness/execution/repo/intelligence.py` therefore requires:

- an operator-supplied absolute executable and fixed argv;
- explicit enablement and independent command authorization;
- canonical query JSON on stdin, with no shell interpolation;
- repository-fingerprint readiness and stale-result rejection;
- bounded, source-linked evidence with completeness and provenance;
- no inherited credentials, network permission, or mutation command in the plan.

Moderne Prethink generation and `mod git apply` are outside the automatic agent path
because they can write generated context into a repository. An operator can run and
review that workflow separately, then the normal project-instruction and repository
index paths can consume the committed result.

## Programmatic tool calling

Programmatic tool calling is most attractive for high-fanout mechanical work where
many intermediate results would otherwise consume model turns and context. The first
increment uses the existing, policy-controlled `bash` tool with a progressively loaded
skill. It favors FFF for bounded discovery and `rg --json`, `jq`, or short Python
standard-library programs for complete mechanical transforms, requires
deterministic bounded summaries, and keeps mutations behind `edit` and `write`.

The committed paired ablation changes only disclosure of that skill. Both variants
retain exactly `read`, `bash`, `edit`, and `write`, and must report pass rate, cost per
passed task, uncached input, cache-read ratio, prefix versions, tool calls, and wall
time.

A future sandboxed `code_execution` tool must pass the same paired gate and additionally
prove that nested calls are policy-checked, individually traced, replay-safe, output
bounded, unable to inherit secrets or network access, and unable to mutate outside the
existing atomic tool adapters. Until then, a fifth model-facing tool is not justified.

## Rollout order

1. Use FFF lexical discovery, the compact manifest, and structured compaction on every task.
2. Run the routing-skill ablation on the fixed real-repository suite when model
   credentials are available.
3. Enable a semantic provider only for repositories whose scale or language tooling
   justifies its operational cost.
4. Compare the semantic provider against the FFF baseline using the same
   outcome, context, cost, and latency metrics.
5. Consider a sandboxed programmatic meta-tool only after the routing-skill results
   show a remaining, measurable gap.
