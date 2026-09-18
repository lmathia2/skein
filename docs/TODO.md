# Skein implementation TODO

## Minimal Harbor/Pier harness

- [x] Preserve the pre-simplification repository on a backup branch.
- [x] Remove the TUI, WebSocket app, interactive CLI, and launch surface.
- [x] Retain and verify four-tool and Skein notebook PTC eval profiles.
- [x] Document developer setup, eval commands, modes, and visual architecture.
- [ ] Run the selected live Harbor campaigns; deterministic local checks do not
  replace provider-backed benchmark evidence.
- [x] Correct empty-note compaction, phase tracking, recoverable oversized PTC
  reads, and incomplete-provider usage accounting from the six-task diagnostics.
- [x] Run the corrected-memory canary, frozen paired twenty-task panel, and
  three-model forced-compaction follow-up in
  `docs/experiments/memory-fixed-20-20260911.md`.
- [x] Reject coding completion when verification sees no changed repository paths.

## Context programs and long-running recovery

- [x] Add strict, disabled-by-default context-program, reconstruction, recovery, and
  owned-continuity configuration with dependency and budget validation.
- [x] Integrate bounded history/aggregate programs, receipts, notes, and safe artifact reads.
- [x] Publish replayable context epochs and bounded advisory/PTC handoffs.
- [x] Carry a bounded deterministic index of prior read ranges and validation
  receipts in compaction handoffs.
- [x] Bind operation identities and checkpoint integrity to safe ADK resume.
- [x] Add explicitly owned prior-run recall and conversation notebook continuity.
- [x] Prepare executable context experiment profiles, schedules, and independent graders.
- [ ] Run the ADR's live paired experiments and decide promotion independently per feature.

See [the context and memory ADR](adr/context-and-memory.md). Implementation
does not satisfy the live empirical gates; default behavior remains unchanged.

### Evidence-backed PTC and memory continuity

Implementation ownership, dependencies, and acceptance gates are in the
[PTC and memory continuity plan](design/ptc-memory-continuity-plan.md).

- [x] S0 — Freeze reproducible reread/exposure measurements and continuity contracts.
  - [x] Add a bounded canonical-ledger read-coverage analyzer with overlap/version
    regression tests. Versioned exact-line exposure now distinguishes selected PTC,
    shell/artifact output, and decoded public provider input; unmappable content stays
    unknown. Old canaries still lack complete exposure capture.
- [x] S1 — Complete authorized evidence addressing and bounded direct recovery.
- [x] S2 — Add provenance-aware PTC descriptions and safe binding lifecycle tracking.
- [x] S3 — Preserve evidence-linked findings and build a task-relevant working set.
  - [x] Add typed advisory findings, versioned corrections, authorized citations,
    and bounded focus-ranked selection over the existing note ledger.
- [x] S4 — Integrate bounded state updates and cache-stable continuation messages.
  Whole-entry handoffs retain findings and recovery handles, identify actual worker
  availability, collapse compatible contained reads, and freeze provider prefixes.
- [x] S5 — Verify freshness, interruption, worker-loss, and prior-run lifecycles.
  Observed changes invalidate dependencies; historical recovery never bypasses
  current-version guards. Unknown effects still fail closed. Live quality is S6.
- [ ] S6 — Run approved controlled continuations and diverse paired DeepSWE trials;
  decide promotion separately per feature.
  - [x] Complete the 48-case Luna/OpenRouter continuity cohort and analyze paired
    correctness, fetched versus emitted reads, cost, and provider-wire stability.
  - DeepSWE expansion held: findings failed missing-range correctness and the
    no-cost-increase gate. See the [continuity audit](audits/ptc-memory-continuity-2026-09-12.md).
  - [x] Separate recovery-page completion from source coverage and run the eight-case
    live diagnostic. Six passed; findings still failed missing-range evidence use.
  - [x] Harden completion evidence: require observed zero exits, reject incomplete
    baseline comparisons, and preserve original criterion-validation indices.
  - [x] Exercise real PTC/workflow rejection and bounded recovery: a wrong answer
    with a passing self-check cannot finish; a sufficient one-line source read plus
    passing independent verification can finish without a whole-file read.
  - [x] Request one fresh working-note checkpoint before each advancing soft cut;
    retain exact evidence during that opportunity and mark bounded/hard-limit
    fallback stale. A nonempty old note no longer implies a fresh checkpoint.
  - [x] Clarify PTC result contracts for managed CLI view bodies versus process
    stdout/stderr, and direct state descriptors versus status/data envelopes.
  - [x] Expose rejected-cell non-execution separately from preserved heap state;
    reproduce late source-validation rejection and require a corrected call before
    a same-name binding can supply new evidence. Keep successful result egress bounded.
  - [x] Route controlled continuations through real workflow verification/re-entry,
    preserve first-verification and terminal accounting, and add six diverse
    development fixtures with task-specific, integrity-checked external oracles.
  - [x] Replace the exposed-file diagnostic oracle after contamination was observed;
    keep expected answers in the host-owned checker and isolate live commands in
    Docker through the existing runtime factory. Pass the real-workflow isolation
    preflight and declare exact fixture output keys before further paid trials.
  - [x] Audit decisive path/version/range availability before each managed answer
    write, preserving first versus repaired answers and unknown retrieval routes.
    Prove separately that a correct guess can pass the artifact oracle without
    demonstrating source availability; neither verdict establishes semantic use.
  - [x] Require source availability in the development oracle as well as correct
    answer bytes; bind the verdict to the last managed answer hash. Prove rejected
    guesses, bounded missing-range recovery, and reuse without forced rereads.
  - [x] Remove duplicate workflow/plugin handoff delivery, using one factory-derived
    owner while preserving off/shadow behavior, conservative budget reservation,
    and post-reconstruction provider-request measurement.
  - [x] Add charged model-written checkpoint development cases through the existing
    runner, steering queue, and context plugin, with delayed questions and unavailable-
    evidence abstention. Test source/checkpoint timing and recovery without seeded notes.
  - [x] Repair quoted multiline note parsing, report note-budget requirements, and
    preserve known pre-commit rejection effects in PTC without weakening unknown
    append/publication failures. Verify through the model-written live screen.
  - [x] Add an explicit note/recall-off control and unseeded worker-loss qualification
    protocol, sharing PTC artifacts, safe restoration, context-cut policy, and
    independent completed-source verification with treatment.
  - [x] Preserve exact artifact byte pages across UTF-8/binary boundaries with
    pre-encoding redaction checks; document envelope recovery and advertise only
    available memory/PTC recovery routes. Keep any repeat a routing diagnostic.
  - [x] Expose the complete validated note-input contract on demand, avoiding
    repeated trial-and-error over hidden ID/kind/size constraints. The routing
    diagnostic passed 4/4 but findings cost 35.3% more with equal total calls.
  - [x] Add unseeded two-cut source-revision cases with normal brokered changes,
    post-change captures, final-source guards, interval measurements, and negative
    completion/ordering tests. Freeze a four-trial live lifecycle screen separately.
  - [x] Repair staged acknowledgement identity/owned-message consumption and
    virtual-command versus process result confusion found in the first staged live
    screen. All four revisions succeeded but zero reached the second cut; retain
    that first cohort as an unexercised quality gate. The repaired diagnostic reached
    both cuts and first verification in 4/4 trials; findings still cost 37.6% more.
  - [x] Reduce repeated model-facing provenance metadata without losing recoverable
    evidence identities, versions or uncertainty; distinguish historical advisory
    next-actions from current steering. Exact-reconstruction and budget tests cover
    the prompt-only projection; full canonical records remain unchanged. Saved
    advisory blocks shrink about 1–17%, including the added action-authority notice.
  - [x] Test the compact projection's live usability, full prompt/cost impact and
    unchanged-source rereads. All four pass first verification; findings reread
    zero versus 28 source lines but cost 41.2% more and explicitly requery memory.
  - [x] Separate note-write commit acknowledgement from full note retrieval after
    tracing publication/CAS/idempotency callers. Verify bounded receipt bodies, full
    publication and paged historical recovery, legacy retries and real PTC continuity.
  - [x] Measure live receipt usability/preparation overhead. Both canary trials pass
    first verification; findings costs 9.9% less but uses two more calls. Both have
    zero source rereads; the findings final continuation needs no recovery query.
  - [x] Implement multi-answer temporal evidence gates: per-artifact values, completed
    sources before each write, assigned cut windows, receipt/current-byte identity,
    checkpoint/acknowledgement rechecks and final verification of every artifact.
    Keep unsupported earlier submissions visible even after a valid correction.
  - [ ] Freeze fresh diverse repeated-use continuations with one-shot and actual
    missing/failed/pending tool controls. Account for checkpoint/protocol overhead
    and retain capable artifacts. The gate is tested; this fresh cohort is not run.
    - [x] Add repository-call-path, configuration-precedence/revision and signed
      reconciliation families with matched one/three-use cases and individually
      verified outputs. Reuse historical checkpoints in unchanged phases without
      claiming freshness; retain normal new-version capture after source changes.
    - [x] Add actual completed/failed/missing validation-operation controls and a
      pending-result-publication race; reject guessed and temporally premature answers.
    - [x] Freeze and run the 18-trial v10 diagnostic screen. Preserve all results;
      output-contract ambiguities prevent clean qualification. Explicit JSON object
      keys and workspace-relative paths are now published by the v11 fixtures.
    - [x] Fix invalidated finding projection using the actual revised-config trace:
      stale values remained salient despite factored freshness labels. Verify the
      scoped recovery handle; retain historical notes and withhold unsafe default text.
    - [x] Live-test recomputation after invalidation and corrected output schemas:
      all six first verifications pass; revised-config findings encountered invalidation
      and recovered without a source reread. This is one targeted regression, not promotion.
    - [ ] Reduce measured preparation/checkpoint overhead without weakening provenance:
      findings cost 48.3% more on the changed-source canary, including a bounded
      note-budget rejection/retry. Inspect accounting, update guidance and exposure.
      - [x] Replay the rejected merge and correct unnecessary note-read/plan-write
        guidance. Reusing two existing finding IDs fits 7,649 bytes with full evidence,
        versus the recorded 10,088-byte rejection under the unchanged 8,000-byte cap.
      - [x] Measure guidance in the frozen six-trial diagnostic; all findings pass
        without plan-only notes or note-budget retries, but two controls fail to reach
        the same stopping point, preventing clean paired efficiency conclusions.
      - [x] Add bounded missing-marker feedback while retaining actual execution
        requirements; give both repeated-use arms 24-call headroom under unchanged
        input/wall budgets. Retain the old unexercised and call-limit outcomes.
      - [x] Freeze and run the v12 comparison after checks: 6/6 first verifications,
        completed evidence and all cuts exercised. Findings cost 12.1% less overall
        but 18.2% more on short validation. Artifact recovery fell 23 to zero;
        both arms had zero source rereads. No held-out qualification claim.
      - [x] Run the frozen full-family v12 stability repeat with actual validation
        negatives. Fourteen positive answer checks passed and four negatives withheld,
        but one accepted control retained an unknown shell effect: safety gate failed.
        Preserve raw outcomes and all family costs; this is not qualification.
      - [x] Close completion's unresolved-capability/cell gap, share admission with
        resume and handoff, and independently flag unsafe acceptance in evaluation.
        Actual failed-shell, pending/corrupt/no-effect boundaries and a deliberate
        missing-fence detection test pass; 878 full-suite checks plus final targeted
        and Docker checks pass. No new paid cohort has used the correction yet.
  - [x] Run the frozen v13 completion-fence live regression: all four positive
    first verifications pass with no unresolved execution; both failed-check
    trials withhold and block. Preserve that voluntary blocking does not itself
    exercise a forced completion claim. Move to new held-out fixtures next.
  - [x] Isolate same-worker retained-value reuse from compaction and learned memory.
    Across three four-family paired cohorts, the on-demand/no-notice path kept one
    epoch in 12/12 trials and reused completed values for 30/30 delayed questions
    with zero avoidable source rereads. Eager binding notices had no reread benefit,
    lost the epoch in 2/12 trials, and cost 16.2% more; keep them opt-in.
  - [ ] Demonstrate evidence-use and actual reread improvements on new held-out
    continuations; distinguish first proposals, verifier rejection/recovery, and
    independently accepted outcomes. Earlier fixture loops bypassed outer verification.
    - [x] Implement fresh breadth and partial-capture variants through the existing
      real workflow. Keep control artifacts, verify each answer, reject correct
      unsupported guesses, and measure premature/opaque acquisition separately.
      Twelve local and Docker checks pass; no new fixture has been dispatched live.
    - [x] Add two changed-source and two conflicting-finding variants with guarded
      revisions, deferred authoritative evidence and real worker-loss checkpoints.
      Sixteen local/Docker checks cover conflict/supersession, stale-answer rejection
      and repair, premature guesses and early-acquisition ineligibility. Not yet live.
    - [x] Retract only a successful unchanged validation's own provisional source
      invalidation; retain older/intervening uncertainty and execution fencing.
      Eleven fault-boundary tests plus actual PTC validation coverage pass.
    - [x] Add fresh actual-validation success/reuse and failure cases. Exercise
      skipped/wrong-command checks, late answers, forced completion after failure,
      and a successful check on the wrong source version. Bind validation to source
      versions at dispatch and independently flag accepted-but-unsupported results.
    - [x] Run the frozen v16 live regression: all four positive first verifications
      pass and both failed checks withhold. Preserve the short-validation cost
      regression and lack of a post-check live cut; no held-out qualification claim.
    - [ ] Complete the owned-prior family and its independent source applicability
      audit; freeze repetitions, full hashes and eligibility gates before the panel.
      Five implemented families do not close S6.
      - [x] Preserve unrelated finding freshness after successful single-file no-op
        writes/edits with an observed hash; retain earlier or unbounded uncertainty.
        Prior-retrieval regression exposed the bug; eight reducer boundary tests cover it.
      - [x] Add an optional scope-aware prior evidence auditor to every answer gate.
        Match completed managed retrievals, canonical source manifests and findings,
        current version observations and answer dispatch boundaries. Keep metadata-only
        lookup and opaque/partial routes unmapped; never inherit validation authority.
        Twenty-three real-PTC admission/integrity checks pass; producer terminal state in
        these unit fixtures is host-supplied, not fresh model qualification.
      - [ ] Run distinct model-written producer/consumer episodes through the existing
        verified workflow, freeze owned bindings and producer snapshots, and include
        all producer calls/costs in the paired budget before paid qualification.
        - [x] Implement two disjoint catalog/shipment pairs through the real root
          workflow, with unchanged and changed-policy variants, actual producer
          verification, owned snapshots and all preparation costs. Ten scripted
          workflow checks pass; three repeat/admission/accounting-stop checks pass.
        - [x] Dispatch and analyze the clean frozen six-family, three-repetition
          panel: 40/72 trials ran before its stop gate; 32 remain unstarted. This
          failed qualification, not completion of all planned model episodes.
        - [x] Classify wrapped task-input budget exits separately in runtime/evals
          and measure zero-cut trials without losing diagnostic evidence.
        - [x] Correct artifact admission effect semantics; preserve integrity and
          unknown-effect fences through actual recovery/completion and fault tests.
        - [ ] Improve review/prior evidence reuse and qualify on fresh cases without
          expanding defaults or paid benchmarks prematurely. Keep consumer version
          observations separate from producer provenance and advisory finding truth.
          - [x] Add separately watermarked consumer-version observations to prior
            working sets and handoffs; preserve source selection, bounds and advisory
            scope. No new completion authority or implicit foreign-note citations.
          - [x] Measure the representation in a bounded live diagnostic: all eight
            episodes verified, but reread/cost gates failed; no promotion.
          - [x] Preserve complete review/task control fields in provider packets;
            reserve required sections before optional context and report distinct
            required-context overflow instead of head/tail-spliced instructions.
          - [ ] Address review-stage evidence navigation, post-observation prior
            applicability exposure and remaining scoped-citation usability.
            - [x] Expose attested reads inside retained plain containers through
              exact, bounded state selectors and existing completed-cell messages.
            - [x] Refresh evidence navigation in host-appended work-batch packets;
              preserve immutable snapshots, replay, bounds and worker/effect fences.
            - [x] Expose bounded identity-only prior applicability after completed
              PTC reads, with source clocks, replay and unchanged completion gates.
            - [x] Give prior findings exact source-note recovery and a reuse-in-place
              contract; retain current-note scope and no-effect foreign-citation rejection.
            - [x] Analyze the prior-navigation diagnostic: three of six executed
              episodes verified, two consumers unstarted, no valid paired efficiency
              conclusion. Confirm retained-binding reuse and missing review steering.
            - [x] Preserve delivered steering across host review/work boundaries;
              validate complete packet/replay/budget and negative completion contracts.
              Full regression: 1037 passed/two skipped; 50 cached-Docker checks and
              15 final serialized-provider checks pass. Live effects remain unproven.
            - [x] Run the unchanged two-case steering diagnostic: 8/8 first-verified,
              unchanged prior-source refetch 18 to three identity lines, but findings
              costs 58.1% more. Preserve the failed cost gate and reused-case scope.
            - [ ] Reduce measured consumer note/prompt overhead without dropping
              completed evidence, freshness checks or required control; check finding
              dependency granularity and actual provider-cache behavior separately.
              - [x] Anchor inner-loop steering at immutable delivery positions;
                verify replay, publication, protected cuts and provider input prefixes.
                Full suite: 1045 passed/two skipped; 50 Docker and 84 final focused
                checks pass. Actual cache/cost remains the separately frozen live gate.
              - [x] Close the anchored-steering diagnostic: all eight first-verified,
                73 append-only within-epoch transitions; findings still costs 33.3%
                more and has no identity-adjusted prior-source refetch reduction.
              - [x] Clarify independent finding scope in PTC and note-schema guidance;
                preserve genuine joint dependencies and reuse prior findings in place.
                Test selective observation, changed siblings, bounded batched notes and
                unchanged historical claims. Live authoring/efficiency is not qualified.
              - [x] Measure finding-scope guidance: both producers author separate
                source-scoped facts; both consumers reuse prior learning before answer.
                Seven of eight episodes verify; blocked control prevents one clean pair,
                and the other retains equal adjusted rereads with 62.7% higher cost.
              - [x] Report proven pre-execution unavailable managed search as no-effect
                and offer an available route, without weakening failed-shell uncertainty.
              - [x] Disambiguate note-schema identity from current-note CAS version;
                preserve rejected writes and replay semantics.
              - [ ] Improve completed-artifact recovery after actual exception-induced
                worker loss and distinguish it from unnecessary review revalidation.
                - [x] Retain bounded, replayable historical read recovery handles in
                  binding-loss messages. Test real exceptions, saved-result reuse,
                  partial/changed/corrupt evidence, preserved heaps and unknown effects;
                  verify the actual root prompt-to-artifact path without a recovery reread.
                - [x] Expose completed reads from the failed cell itself, which have
                  canonical artifacts but no earlier completed binding manifest. Derive
                  from completed capability receipts, never from dirty heap state.
                  Group retained invalidations so recent read handles can fit without
                  raising entry/byte budgets; verify the real root completion path.
                - [x] Measure live recovery-handle use after a recorded actual exception;
                  retain unexercised failures and separate review-stage rereads/cost.
                  Four seeded trials first-verify with all interventions exercised;
                  direct handles/coverage avoid lookups. Messages cost 8.39% less,
                  but both arms have zero avoidable source rereads; not qualification.
                - [ ] Remove the observed saved-result-envelope decoding detour using
                  existing recovery/JSON facilities; preserve complete paging and
                  historical-versus-current source semantics.
                  - [x] Add a shared guarded recipe to bounded recovery notices and
                    existing artifact help; test actual prompt-to-recovery execution,
                    incomplete/binary pages, partial source coverage and replay hashes.
                  - [ ] Measure whether live models decode and compute in the same
                    recovery cell; deterministic recipe execution is not model uptake.
            - [ ] Qualify model reuse rather than equating a retained worker or a
              scripted recovery check with reliable live memory.
              - [x] Implement and preflight the new ordered-routing, SQL-eligibility
                and revised-build-graph panel; charge acquisition and three delayed uses.
              - [x] Close its frozen twelve-trial paired live screen and report every
                source/answer window, repetition, terminal, exposure route and cost.
                Ten verified, two call-limited; both arms 5/6 on different failed pairs.
                Findings use 8% fewer calls but 4.56% more source reread lines and
                3.51% more cost. Gate failed; cases consumed, no default promotion.
              - [ ] Address observed PTC workspace-import/exec detours and review
                reacquisition using existing execution and evidence-navigation paths;
                measure on diagnostic cases before freezing another held-out panel.
                - [x] Share the computation/workspace execution boundary between PTC
                  instructions and kernel help; retain separate source/answer/check
                  bindings in the shipped example and test its actual execution.
                - [ ] Measure live uptake without treating consumed cases as held out.
                  Six labeled diagnostic trials closed: SQL/findings verifies with
                  zero source rereads, but findings overall verify only 1/3 versus
                  control 3/3 and reread 293 versus 204 source lines. Qualification fails.
                - [x] Correct data-relative exception positions being reported as
                  notebook source lines; retain parser/source-validation locations.
                  Also distinguish earlier-cell function frames from current source;
                  diagnostics change without altering effects or recovery authority.
                - [ ] Preserve useful blocked-approach evidence across cuts from the
                  existing approval/receipt history, respecting later authorization
                  and distinguishing no-effect rejections from unknown execution.
                  First remove the reproduced policy false positives: all five
                  routing Python attempts were split inside quoted program bodies.
                - [x] Keep quoted/escaped shell separators inside arguments in the
                  shared command classifier; retain real trailing-command risk checks
                  and fail closed on unsupported syntax. Live benefit remains open.
                  The bounded routing diagnostic exercises successful quoted project
                  execution, but findings still fails the memory quality/cost gate.
                - [x] Bound malformed provider tool-call arguments in the public
                  history while retaining exact raw evidence and explicit non-execution;
                  do not synthesize code or treat ADK argument rejection as success.
                  Redaction takes precedence over exact retained bytes, with original
                  hash/size and an explicit redacted flag. 73 focused checks pass,
                  including real ADK/PTC streaming, parallel valid-call continuation,
                  preserved heap, artifact recovery, usage and rejection metrics.
                  Full regression: 1,212 passed/three skipped. The common-fix live
                  pair verifies 2/2 but has equal rereads and 147.2% higher findings
                  cost. Malformed recovery was not naturally exercised; no promotion.
                - [x] Preserve a note-checkpoint reminder at its original request
                  position across calls/restart, with bounded replay and prefix tests.
                  The latest diagnostic loses one same-epoch prefix after its reminder.
                  Implemented with the existing addressed-exposure pattern; 80 focused
                  context/steering checks pass. Post-fix live qualification remains open.
                - [x] Keep incidental Python bytecode out of the Docker workspace,
                  matching local execution hygiene without relaxing scope verification
                  or hiding deliberately requested build artifacts.
                  Cached-Docker import/explicit-compilation checks pass; live benefit
                  is exercised by first-verification success without bytecode cleanup
                  in the common-fix pair. This does not qualify memory efficiency.
                - [x] Reuse the complete-read artifact recovery recipe in ordinary
                  cut handoffs, with exact binding/load guidance and unchanged
                  freshness/range/epoch constraints. Both common-fix arms reread
                  all 57 source lines after every cut despite available evidence.
                  New deterministic PTC recovery passes all 119 focused checks and
                  14 cached-Docker lifecycle cases without overlapping source reads;
                  live recipe exposure is confirmed in all six diagnostic handoffs,
                  but productive reuse and the reread-efficiency gate still fail.
                - [ ] Reproduce and improve productive decoded artifact recovery and
                  warm-worker value reuse from the ordinary-recovery live pair.
                  Control loads six artifacts then rereads; findings ignores the
                  delivered recipe and reacquires 57 lines twice within one epoch.
                  Preserve historical scope, explicit freshness checks and verification.
                  - [x] Reproduce the three-source warm-worker notice and expose
                    attested source-content expressions through one shared bounded
                    projection. Real PTC uses all three expressions without a fresh
                    read; mutation/worker-loss/replay checks remain intact. Focused
                    suite: 216 passed, one skipped. Live benefit remains untested.
                - [x] Give malformed/missing current-task note citations accurate
                  recovery guidance; prefer existing read-reference values over
                  retyping hashes. Never guess-repair or relax citation authorization.
                  All 36 focused note tests and 12 cached-Docker continuation checks
                  pass; live recovery efficiency remains unqualified.
                - [x] Trace completed-phase instructions versus current obligations
                  after compaction/final review. The saved outgoing final review
                  includes current steering and the explicit no-repeat instruction;
                  a missing-delivery hypothesis is contradicted. Preserve the fixture
                  and required verification in the next recovery diagnostic.
                - [ ] Demonstrate avoidance of repeated finished preparation without
                  dropping independently required checks. Repetition despite delivered
                  guidance remains model behavior, not an established phase-state defect.
                - [x] Reduce redundant navigation metadata without removing evidence,
                  version/range identity, uncertainty or independently required checks.
                  Prompt-only live-binding projection retains canonical descriptors;
                  all twelve offline review snapshots retain prior selected evidence.
                  Live reuse/cost qualification remains open.
              - [x] Preserve known no-effect file precondition rejections through direct
                tools, receipts and PTC; check guards before creating parent directories
                and keep post-mutation failures unknown. Discovered by the new panel's
                stale-write-guard negative, before any provider dispatch.
              - [x] Distinguish missing files from existing empty files in both file
                adapters, preserving hash guards and truthful mutation receipts.

## Project identity

- [x] Rename the distribution, CLI, launchers, runtime/config identity, environment
  prefix, documentation, terminal labels, and repository to Skein; document the name.

## Optimization-facing behavior configuration

- [x] Externalize model generation, stagnation routing, and stable project-context
  budgets; export the safe prompt/model/context/tool/cache surface with behavior-hash,
  outcome-metric, and redacted-trace contracts for external optimization loops; ship
  fully annotated four-tool, PTC+JSONL, and PTC+DuckDB standard configurations.
- [x] Enforce byte-stable provider request prefixes across worker calls and derive
  Codex/OpenResponses cache routing keys from the complete stable request shape.

## Trace-native notebook PTC

- [x] Add a disabled-by-default, local-only notebook PTC configuration gate.
- [x] Add deterministic append-only notebook reduction and nbformat materialization.
- [x] Add a persistent CPython worker with bounded output, timeout termination, and safe-cell restoration.
- [x] Route nested file and shell capabilities through existing policy, approval, receipt, and redaction paths.
- [x] Record submitted/completed/failed/timed-out cells and requested/completed/failed/blocked capabilities.
- [x] Externalize oversized rich MIME outputs as content-addressed artifacts.
- [x] Add the canonical DuckDB event schema, idempotent writer, temporal reads, deterministic importers, and shadow capture for task events, receipts, checkpoints, and traces.
- [x] Shadow-capture approvals, steering, metrics, public/run events, and redacted ADK session lifecycle alongside task events, receipts, checkpoints, and traces.
- [x] Add idempotent historical backfill for recognized JSONL, SQLite, and ADK session stores with deterministic hashes and source-count equality auditing.
- [x] Prove byte-level semantic equality for the live task-event reader, then serve task state, recent context, and compaction from canonical ledger events with idempotent compatibility-store read repair.
- [x] Implement deterministic history, progress, open-execution, time, task-memory, and dream-mode views plus receipt-bearing P0-P3 prompt manifests.
- [x] Add an optional immutable LanceDB hybrid-search projection with canonical event provenance; keep DuckDB as the sole ledger authority.
- [x] Preserve the main four-tool path when canonical memory is disabled and add a canonical JSONL fallback with byte-equal deterministic views.
- [x] Make DuckDB and LanceDB optional installation extras selected through validated YAML.
- [ ] Wire an explicit embedding provider before allowing live `retrieval: lance` prompt use.
- [ ] Cut ledger views into live prompt/compaction readers after byte, cache, and correctness ablations pass.
- [x] Add candidate, shadow, active, retired lifecycle enforcement for restricted relational memory programs.
- [x] Add atomic deterministic Parquet sealing with hot-versus-sealed watermark equality; defer DuckLake until scale measurements justify it.
- [x] Add explicit physical task erasure covering ledger rows, recognized operational SQLite rows, JSONL, notebooks, uniquely referenced artifacts, and manifested sealed segments.
- [x] Run the four-tool versus notebook-PTC quality, token, latency, and cache-hit
  ablation before changing the default; the six-task gate failed, so four tools
  remain the default.
- [x] Define notebook PTC's supported execution boundary as trusted local workspaces; production/adversarial isolation is an optional future deployment profile, not an activation gate.
- [x] Add standalone executable notebooks for PTC state, cache-aware compaction, and versioned trace-memory programs.
- [x] Project task, public message, steering, and compaction events into timestamped notebook Markdown cells.
- [x] Add task-scoped notebook rematerialization and compact required `nb-cli`
  inspection without a notebook-JSON parsing fallback.
- [x] Snapshot the complete logical notebook as one immutable content-addressed
  artifact when its PTC worker closes; keep the ledger authoritative and the live
  Python heap ephemeral.
- [x] Classify only non-externalizing local `nb-cli` reads as automatic; keep notebook execution and mutation approval-gated.
- [x] Make failed PTC cells transactional by discarding the dirty kernel epoch;
  restore only previously committed cells before accepting more work.
- [x] Restrict automatic cell replay to self-contained data construction; classify
  calls, imports, definitions, attribute/subscript access, and loaded-name dependencies
  as requiring reconciliation.
- [x] Add bounded `agent.state.list()` and `agent.state.describe(name)` metadata views,
  compact state deltas, and a regression proving bulk nested capability results remain
  in Python until explicitly selected.
- [x] Key notebook/REPL recovery by stable owned conversation identity rather than a
  server run ID, with process/server restart and concurrent-run rejection tests.
- [x] Add the metadata-only REPL state catalog and last committed kernel epoch to the
  compaction handoff without moving them into the cache-stable prefix.
- [x] Capture actual serialized provider requests and prove long-session context grows
  with selected egress rather than nested result bytes or the live heap.

See `docs/design/trace-native-repl-agent.md` for tenets, contracts, phased gates,
and the implementation/evaluation rubric.

## PTC execution refinement

The current quality-first implementation sequence and promotion gates are in
[PTC improvements](ptc_improvements.md).

- [x] Normalize nested results, classify compact failures, and bound capability calls.
- [x] Expose deterministic capability, kernel, CLI, and search manifests on demand.
- [x] Bound help catalogs with deterministic full-contract, compact-signature, and
  targeted-query fallback tiers without mutating the cache-stable prompt.
- [x] Apply the configured secret redactor before registered capability results,
  printed overflow, or explicitly published values reach model output or durable
  artifacts. Keep reversible PII tokenization out until a cross-MCP data-flow policy
  and protected token vault are explicitly required.
- [x] Bind first-review criterion decomposition, completion claims, transition probes,
  and validation evidence to stable criterion IDs.
- [x] Emit deterministic phase guidance in the dynamic work packet.
- [x] Add opt-in bounded snapshot rollback without replaying brokered effects.
- [x] Run the `24/48` versus `12/36` E3 live comparison on Koota with three
  no-retry attempts per arm. Keep `24/48`: it scored 3/3 while `12/36` scored
  2/3, so the tighter candidate failed the no-quality-regression gate.
- [x] Preserve the implementation phase after a changed `max_cells` yield; only
  no-change yields enter criterion review. Add repository-neutral read-once and
  bounded-search guidance without changing the `24/48` limits or 16 kB output cap.
- [x] Persist each complete nested capability result as an immutable,
  content-addressed artifact before bounding model-visible output. Record only its
  URI, media type, byte size, operation identity, status, and result hash in the
  ledger; keep the original result available to the live Python worker.
- [x] Preserve complete stdout/stderr when cell output exceeds the configured cap.
  Return a bounded head-and-tail preview plus artifact URI and omitted-byte count
  instead of silently discarding the overflow.
- [x] Expose confined `agent.artifacts.load(uri, offset=0, limit=...)` and
  `agent.artifacts.list()` operations so persisted results and overflow remain
  reloadable after a worker restart. Keep reads bounded, task-scoped, redacted, and
  receipt-bearing.
- [x] Add an explicit `agent.artifacts.publish(value, name, description=None)` for
  intentional host-facing deliverables. Normalize the name, store immutable bytes,
  mark the artifact as published in metadata, and let the host decide whether to
  display or forward it; never treat automatic internal artifacts as user-facing.
- [x] Prove the artifact contract deterministically: small results remain unchanged
  in Python, large results do not enter the prompt unless selected, truncated output
  is byte-equal when reloaded, names cannot escape their namespace, and repeated
  persistence deduplicates by content hash. Re-run Koota only after these checks;
  retain 16 kB unless live evidence justifies a separate 32 kB cap arm.

### Modular architecture execution checklist

- [x] U1: Prebuild the pinned ADK sandbox image and verify its package contents.
- [x] U2: Reuse one trusted-eval container per sequential worker with a fresh
  interpreter, workspace view, environment and tool package per example; prove
  descendant cleanup, no cross-example state, replacement on reset failure, and
  container/start/reset metrics.
- [x] U3: Normalize PTC model-visible results to a compact stable envelope and make
  top-level plus nested tool usage queryable through versioned trace-memory views.
- [x] U4: Replace scattered PTC assembly branches with one closed dispatch point;
  document and exhaustively test behavior with PTC/memory/context independently off.
- [x] U5: Consolidate memory programs into one code-owned `(name, version)` registry
  used by configuration and execution; remove unused competing prompt/program code
  only after proving it has no production callers.
- [x] U6: Extract context selection as a pure policy computation while retaining one
  renderer, stable-prefix bytes, complete call/result pairs and bounded evidence.
- [x] U7: Re-evaluate the resulting module combinations from first principles,
  update the canonical support matrix, and run unit/integration/type/lint plus the
  controlled live comparison before changing defaults.
  - [x] Audit and document the actually supported and rejected combinations.
  - [x] Pass the full unit suite, runnable integrations, full lint, and changed-code typing.
  - [x] Clear the production `app`/`harness` Pyright baseline; executable tests remain
    covered by pytest rather than static analysis of their deliberately dynamic fakes.
  - [x] Keep four tools as the default after the matched provider comparisons; notebook
    PTC did not clear the quality gate despite lower median token use and active time.
  - [x] Remove ADK Code Mode, its container pool/image, and vendored Docker SDK; keep a
    helpful configuration migration error.
  - [x] Add bounded concurrent Harbor task scheduling while preserving one isolated
    Pier trial per worker.
  - [x] Make matched DeepSWE runs benchmark-selectable and single-attempt by default;
    preserve official reward, latency, token usage, and separate Skein reliability
    status in each append-only run record.
  - [x] Rerun four tools and Skein notebook PTC on the historical reference tasks at
    one clean revision; notebook reduced raw latency, tokens, and cost but did not
    clear the quality gate.

- [ ] Extract the shared `PtcRuntime` and `PtcSession` contracts behind
  `execute_code` without changing either current implementation's tool declaration.
  - [x] Extract native lifecycle adapters behind a shared `PtcSession`.
    Keep runtime-specific protocols separate until cross-pairing requires more.
- [ ] Separate PTC serialization (`notebook` or ledger-only `jsonl`) from runtime-state
  recovery (`none`, `replay_safe`, or bounded `snapshot`) and reject invalid combinations.
- [x] Replace bundled memory implementation selection with exact code-owned
  `(program name, version)` configuration and receipt identity.
- [x] Run the deterministic composition matrix across PTC, memory-program, context,
  serialization, state, and continuity selections, including explicit rejection of
  unsupported combinations.
- [x] Run the matched live comparison before changing the four-tool default; the
  six-task result did not clear the quality gate, so the default remains unchanged.

- [x] Make Skein notebook PTC selectable behind one ADK tool, with optional
  trace-native or Pi-derived memory for matched ablations.
- [x] Add executable notebooks demonstrating PTC and all optional
  memory strategies without requiring credentials or a running sandbox.

- [x] Reconcile the PTC proposal with the Anthropic sources, reference runtimes,
  and current Skein execution path; publish an actionable implementation plan.
- [x] Add machine-readable capability results and actual provider-payload checks.
- [x] Add executable phase-aware programming examples using the existing policy surface.
- [x] Add bounded brokered parallel reads and deadline/reconciliation checks.
- [x] Reduce redundant verification/snapshot work without weakening evidence.
- [x] Freeze bounded context handoffs at explicit cache-stable epochs.
- [x] Validate the bundle on the same six DeepSWE tasks with Muse Spark 1.3 Contributor.

See [PTC execution refinement](design/ptc-execution-refinement.md) for scope,
implementation seams, deterministic checks, and the single bundled live screen.
This replaces per-feature paid ablations for this proposal, not the safety or
independent-completion contracts. Existing runtime defaults remain unchanged.

## Pi terminal experience

- [x] Prove standalone Pi toolkit reuse with deterministic rendering fixtures.
- [x] Separate public replies from workflow control and support non-coding turns.
- [x] Preserve conversations and wire queues, model/auth controls and resources.
  - [x] Add cancellable server-owned provider authentication controls.
  - [x] Connect Pi-style login/status/logout dialogs to the authenticated server.
  - [x] Wire searchable model selection and saved defaults to actual next-turn ADK configuration.
  - [x] Expose bounded read-only transcript pages from durable public events.
  - [x] Connect `/resume` and historical transcript navigation to those pages.
  - [x] Expose trusted resource metadata and actual skill selection through the server.
- [x] Complete Pi-style UI and migrate installation/launching.
  - [x] Prevent stored approvals from leaking across tasks or surviving expiration in a shared adapter.
  - [x] Wire command approval decisions to waiting worker/verification execution and terminal controls.
  - [x] Stream eligible public replies with immutable control headers, verification gates and reconnect tests.
  - [x] Finish quiet activity presentation and live visual/latency comparisons.
  - [x] Migrate installation/launching after the new client passes the delivery gates.
- [x] Verify conversational/coding examples, replay and harness swap; remove Go TUI.

See `docs/design/pi-terminal-migration.md` for delivery gates.

## Minimal-harness simplification

- [x] Reorganize `harness/` by authority boundary, move Harbor campaign support to
  `evals/`, and delete unreachable legacy eval, tuning, backfill, and summary modules.

- [x] Remove Magnitude, LiteLLM integration, and installer/launcher branches; retain Codex and native ADK provider seams.
- [x] Replace shadowed legacy tools with the tested atomic file primitives and remove unwired adapters.
- [x] Reduce fixed-graph configuration and optional orchestration layers without weakening verification or approvals.
- [x] Remove ADK's model-authored compactor; derive bounded memory views from the append-only log.
- [x] Give the ADK worker sole ownership of the coding loop and fail closed on non-terminal results.
- [x] Add one durable counterexample pass before coding-task verification without
  expanding the worker or tool topology.
- [x] Verify retained paths and report source-line and McCabe-complexity changes.

Each checked item is committed independently.

## Fixed-intelligence benchmark evaluation

- [x] Add a Harbor 0.22 host-side external agent that maps Skein's execution
  boundaries into the task environment without copying provider credentials.
- [x] Freeze deterministic 6/18/42/105-task DeepSWE 1.1, Terminal-Bench 2.1,
  and SWE-Atlas-QnA manifests with immutable task hashes and equal-weight scoring.
- [x] Add fail-closed experiment matrices, sequential task selection, official
  Harbor reward import, idempotent result ledgers, and paired task-level analysis.
- [ ] Install Docker or select another supported Harbor environment, then run all
  selected official oracles; SWE-Atlas also requires its approved judge key.
- [ ] Freeze the approved subscription account/workspace, Luna/max snapshot,
  client version, and harness revisions after the authorization gate passes.
- [ ] Run the six-task live adapter smoke, 18-task Skein ablation, 42-task
  multi-harness pilot, and 105-task/two-attempt finalist confirmation in order.

## Historical delivery record

The entries below describe earlier deliveries, not the supported feature inventory.
See `IMPLEMENTATION_STATUS.md` and `simplification.md` for current capabilities and
intentional removals.

- [x] Commit the Pi-inspired ADK coding-harness design brief.
- [x] Add an Agents CLI-compatible prototype scaffold and pin upstream Google skills.
- [x] Implement typed task, ledger, tool, context, checkpoint, and verification models.
- [x] Implement deterministic context compilation, prefix hashing, and coding-aware compaction.
- [x] Implement the environment abstraction, command policy, bounded output, and four coding tools.
- [x] Implement repository discovery, lexical/structural indexing, and compact repository maps.
- [x] Pin native FFF search and expose bounded, cursor-paginated discovery through the existing `bash` tool.
- [x] Implement event reduction, SQLite persistence, tool receipts, steering, and checkpoints.
- [x] Implement deterministic verification and acceptance-criterion evidence.
- [x] Wire the ADK 2.x coding agent, deterministic verification workflow, caching, and resumability.
- [x] Add unit, integration, resume, security, and Agents CLI evaluation fixtures.
- [x] Add CI and operational documentation after the MVP contracts are stable.

## Declarative runtime and interactive client

- [x] Define strict YAML composition, volatile runtime bindings, ADK model/App assembly seams, and versioned AG-UI/control protocol contracts.
- [x] Replace singleton application wiring with a closed registry of harness factories that assemble and reuse ADK primitives.
- [x] Adapt the current coding harness to the common runtime and prove a second registered test harness can be selected without server or client changes.
- [x] Make safe Pi prompt/model choices executable configuration and reject unsupported topology or agent changes during parsing.
- [x] Implement the durable run registry and bidirectional WebSocket/AG-UI server with replay and backpressure.
- [x] Implement a Bubble Tea TUI that depends only on the public protocol.
- [x] Add deterministic harness-swap, reconnect, replay, steering, cancellation, and client compatibility tests.
