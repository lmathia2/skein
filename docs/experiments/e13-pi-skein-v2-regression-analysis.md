# E13 Pi + Skein PTC v2: corrected regression analysis

## Scope and evidence limits

Eight tasks, three fresh trials per arm; Muse Spark 1.3 Contributor, OpenRouter,
xhigh, output limit 32768. Results are under `.artifacts/e13-pi-skein-v2` and
`.artifacts/e13-ptc-isolation-pi-code`. This revision replaces earlier claims that
PTC contract defects were ruled out, that persistent variable reuse was established
by name references, and that Scriggo concealed its known gaps.

The user supplied the shell projection counts and cross-arm compaction audit below.
The timeout discard path, two Ink state losses, actual adapter contracts, and full
Scriggo final response were independently checked against local code and traces.
The exact shell-count classification has not been independently reproduced here.

## Result: the binary gap overstates the quality gap

| Metric | Pi + Code Tool | Pi + Skein PTC v2 | v2 minus Code Tool |
|---|---:|---:|---:|
| Exact pass | 21/24 (87.5%) | 18/24 (75.0%) | -12.5 pp |
| Mean partial | 0.997619 | 0.998549 | +0.000931 |
| Mean feature-to-pass (F2P) | 0.955000 | 0.975235 | +0.020235 |
| Mean pass-to-pass (P2P) | 0.999921 | 1.000000 | +0.000079 |
| Median agent time | 17.75 min | 14.96 min | -15.7% |
| Repriced cost | $4.726 | $4.439 | -6.1% |
| Repriced cost per exact pass | $0.225 | $0.247 | +9.6% |

V2 produces slightly better average partial, F2P, and P2P scores while producing
three fewer exact passes. Its six failures preserve every pre-existing test. Five
of the six are within one to five feature tests of passing; one incomplete Scriggo
implementation misses 15 feature subtests. The remaining regression is therefore
reliability at the last semantic edge, not general patch quality or backward
compatibility.

The observed exact-pass difference is also uncertain at this sample size. A task
cluster bootstrap gives a v2-minus-Code-Tool difference of -12.5 pp with a 95%
interval of approximately -33.3 to +8.3 pp. The task-level evidence is more useful
than treating 18 versus 21 as a stable population estimate.

## Where the three-pass gap comes from

| Task | Code Tool | Skein v2 | Difference |
|---|---:|---:|---:|
| Ink | 2/3 | 3/3 | +1 |
| Koota | 3/3 | 2/3 | -1 |
| Obsidian | 2/3 | 3/3 | +1 |
| Query | 3/3 | 3/3 | 0 |
| Scriggo | 2/3 | 0/3 | -2 |
| Tengo | 3/3 | 2/3 | -1 |
| Testem | 3/3 | 2/3 | -1 |
| Textual | 3/3 | 3/3 | 0 |

Scriggo explains two of the net three missing passes. The remaining net pass comes
from three isolated edge misses, partly offset by v2 doing better on Ink and
Obsidian. There is no uniform per-task degradation.

## Contract defects with concrete mechanisms

### Shell diagnostics disappear when the model projects stdout

User's v2 audit: 1,696 bash cells; 1,102 print stdout only; 798 of those contain no
`2>&1`; only eight cells check `exit_code`. The two Scriggo near-miss trials have
207 and 180 bash cells without stderr or redirection. These are exposure risks,
not counts of actual hidden errors: the current observations do not establish
whether those omitted streams were nonempty.

The tested Code Tool remote adapter returns a JSON string containing exit_code,
stdout, and stderr. Printing that string exposes all three together. It is not a
chronologically interleaved stdout/stderr stream and can still be selectively
parsed. Skein returns a dictionary; its prompt example explicitly selects stdout
and separately selects exit_code, omitting stderr. The model frequently keeps only
the stdout projection. The broker has the missing evidence, but the parent does not.

Fix the presentation boundary: preserve structured data for computation while
ensuring nonempty stderr and nonzero exit status reach the parent even when the
cell prints stdout only. A combined shell receipt with an `[exit N]` marker is
another option. Do not infer success from a successful Python cell. Also preserve
pipeline semantics: `test | tail` can report tail's zero exit status; receipt
formatting alone cannot recover the test process's lost status.

Instrument each broker call with stream byte counts, exit status, timeout state,
call ID, and which fields reach the parent. Distinguish nonempty stderr from true
failure. Keep full logs retrievable; bounded summaries must not silently drop the
failure reason.

### Equal shell and cell deadlines destroy persistent state

`_PierPtcBroker.bash` defaults to 120 seconds, and the adapter calls
`worker.execute(..., 120)`. The cell deadline starts before shell dispatch. While
awaiting a broker reply, `replies.get` uses the remaining cell budget; expiration
calls `_discard()`. The shell has no time to report its own timeout before the
worker deadline expires.

Verified Ink traces: `sj2MSGx` line 1225 discards a worker whose preceding state
count is 3; `f2XJrKV` line 1547 discards one whose preceding count is 20. Both
trials eventually pass, so this is demonstrated recovery/efficiency harm, not an
explanation of a failed reward. The user's Code Tool comparison reports that the
same slow command raises an error while keeping session state.

Give broker operations shorter deadlines than the remaining cell deadline, with
room to deliver and process their timeout result. Return a structured shell timeout
inside the cell. A larger fixed cell timeout alone is insufficient for a batch of
multiple shell calls: budget each call against the remaining cell time. Preserve
hard deadlines for stuck Python. A timed-out shell may already have changed files;
keeping Python state must not falsely imply that side effects were rolled back or
that the command is still safe to repeat. Verify termination/cancellation behavior.

### Paged reads can become destructive whole-file writes

Scriggo `suAGQGQ` cell 190 reads the first 700 lines of a 1,592-line file into
`txt`, drops completeness metadata, and attempts to replace text at line 1123.
It prints `False` for membership, then writes `txt.replace(...)` anyway. The file
shrinks to 700 lines. Cells 191–195 diagnose and restore it; cell 200 uses shell
Python `Path.read_text()` and an assertion to apply the edit.

Code Tool's tested `read(path)` acquires the whole file; Skein defaults to 400
lines. Bounding acquisition and bounding what the model sees are different choices.
The prompt example discards pagination metadata immediately. This is an observed
contract-related disruption, but it was repaired before evaluation; attribution of
the final Scriggo semantic failures to it remains unproved.

### Retained results and output limits need an accurate contract

Skein caps observations at 16,000 bytes, versus Code Tool's reported 50 KB cap.
The user found no failed trial with a truncated test run. Matching caps is a useful
controlled comparison, not an established accuracy fix. Zero of 3,464 v2 calls
request `result_id`, verified directly from tool arguments.

Oversized final-expression `value_repr` is truncated inside the worker before the
retained record is constructed. Unlike printed stdout, it has no separate full
representation. Retrieval therefore cannot always recover omitted text. Records
also have a 256,000-byte cap and a 32-record eviction limit. Expose those limits and
make missing details genuinely retrievable before promising full-result access.

### State feedback and scratch paths add unnecessary friction

The earlier 29–37% reuse rates count loads of previously assigned names. They do
not measure consumption of a value from an earlier cell: `b = bash(...); print(b)`
can be counted even though the old b was overwritten. Withdraw those rates as
evidence of useful reuse. The Tengo failing trial has 117 cells and zero persistent
state in the user's audit. Measure reads before reassignment, ideally runtime
provenance, rather than name overlap.

Show newly introduced names by default instead of repeated `[state: b]` updates;
show deletions, resets, or recovery separately, and leave the complete namespace
available on demand. The user's audit also finds eight scratch writes rejected at
`/tmp`. State the workspace-only helper rule explicitly and offer a documented
workspace scratch location; do not broaden filesystem permissions incidentally.

## Generated-patch failures: exact diagnosis and attribution

| Task/trial | Verified failure | What this establishes |
|---|---|---|
| Scriggo `2uYeauK` | 32/48 F2P; zero/empty method results and interface outputs | Broadly incomplete compiler/runtime semantics |
| Scriggo `Fhrskru` | 44/48 F2P; interface calls lose expected string results | Runtime dispatch/result propagation gap |
| Scriggo `suAGQGQ` | 42/48 F2P; method-expression values panic | Stops with explicitly acknowledged requirements unfinished |
| Koota `tsiBUAG` | 37/38 F2P; pair/trait AND coexistence | Composition defect despite related custom tests |
| Tengo `6di8JZm` | 90/91 F2P; generic error obscures required rest diagnostic | Parser recovery/diagnostic precedence defect |
| Testem `UD2UzWb` | 64/65 F2P; duplicate XML roots | Idempotence guard scoped to template mode only |

Scriggo's full final response says: **Known gaps: `f:=T.M` as value and `*T`
interface dispatch currently panic with zero Value.** The earlier false-completion
characterization was incorrect. It declared partial completion and deleted failing
temporary probes before committing. The corrective guideline should be: do not
finalize while an explicit requirement remains a known gap; retain failing probes
until resolved or explicitly justified. The concise Pi prompt is context, not
proof of why the model chose to stop.

Koota cell 188 writes a mixed `Added(Foo, Likes(t1))` test. Cells 189 and 210
show failures; later edits attempt fixes. The exact hidden query-consumption
sequence needs comparison before calling the behavior untested.

Testem's `close()` calls `finish()`. The failed patch places the `_finished`
guard inside `if (useLauncherTemplate)`, so explicit finish followed by close
writes two documents in legacy mode. Both successful Skein trials put the guard
before that branch. The failed trial tests double-finish in template/TAP mode,
missing the legacy/XML combination. This is a concrete within-arm counterexample
to explaining every semantic miss as an executor limitation.

## Compaction and aggregate efficiency

All three Code Tool Scriggo trials compacted too, one twice, and two passed
(user's cross-arm audit). Thus compaction occurrence does not distinguish the
successful Code Tool runs from failed Skein runs. The four compacted v2 trials
being failures cannot support a causal compaction claim on its own. Investigate
lost evidence or state across individual boundaries, not simply whether a cut ran.

Failed-versus-passing cell and output averages are confounded by task difficulty.
They describe expensive failures but cannot explain them. High partial scores are
also weighted by many existing tests; inspect F2P and exact task completion
separately. The result tables do not establish that the executor is intrinsically
better or worse.

## Revised priorities and experiment

1. Preserve shell failure evidence at the parent boundary and add telemetry of
   omitted diagnostics. This addresses a pervasive observability risk.
2. Separate shell and cell deadlines and preserve state on handled shell timeouts.
   Prove it with a short synthetic timeout, a retained sentinel variable, and a
   follow-up cell; also verify that the shell process has stopped.
3. Prevent partial-source overwrite workflows and repair oversized-result recovery.
   Check these offline before paying for another model run.
4. Reduce state/mutation noise, document scratch paths, and match observation caps
   as a separate treatment so its effect remains attributable.
5. Add completion guidance for declared gaps and retained failing probes as a
   separate prompt ablation.

Use Ink, Scriggo, Tengo, and Testem for the diagnostic panel, three fresh trials
per treatment. Keep Code Tool and current v2 as controls where budgets allow; do
not combine all fixes and call the resulting change causal evidence. Add Koota
when testing composition/completion guidance. Include held-out tasks before a
broad quality claim, since these task failures have now informed the treatment.

Log quality (exact pass, F2P, P2P), tokens by cache status, actual versus repriced
cost, model and tool latency, resets, lost bindings, useful cross-cell value reads,
result retrieval, truncation source, shell stderr/exit exposure, and known gaps at
termination. Count timeouts and omitted failure evidence directly rather than
using pass rate as their proxy. Estimate cost before launching; this revision
launches no new runs and changes no runtime behavior.
