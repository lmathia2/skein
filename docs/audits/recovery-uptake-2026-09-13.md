# Why delivered recovery did not reduce rereads

Scope: the closed two-trial Luna/max ordinary-recovery diagnostic on `08df057`,
not a claim about all 41 recorded Luna experiment batches. Sources are under
`.artifacts/ordinary-read-recovery-live-v1/`; inspect only public instructions,
submitted cells, receipts and results, not opaque provider reasoning.

## What happened

Every first-post-cut request contained the shared decoder and exact source load
expressions; all six handoffs had zero omitted entries. The findings arm also
received three source-linked notes. Delivery is established; utilization is not.

- Control cells 78 and 261 each loaded the three source artifacts successfully.
  They printed `model_text` and `data.text`, the latter containing a serialized
  original result envelope. Neither executed the supplied guarded JSON decoder.
  Cell 78 retained only the last loop value; cell 261 retained loader envelopes
  in `loaded`, not source-text bindings. These six loads establish access, not
  productive recovery. The following cells fetched the source files again.
- Control cell 283 captured all three current files into `current`, checked their
  hashes and completeness, then cell 305 fetched the same three files into
  `by_path` before solving. Both ran in the same kernel epoch.
- Findings cell 87 captured and printed all 57 source lines into `reads_current`.
  Cell 110 reacquired the same files into `source_by_path` to compute the first
  answer. The epoch did not change, and no intervening source mutation explains
  that duplication. Findings made no artifact-load calls in the trial.
- Both final reviews read sources and answers again. In findings wire `011.json`,
  the newest review navigation explicitly reports `revalidation_required` and
  `usable_as_current_fact: false` for all three findings. A preceding successful
  shell operation (canonical 282/285) carried `workspace_may_have_changed: true`.
  Such reads must not all be labeled needless: freshness and independent verification
  remain required. This does not explain the earlier same-epoch duplicates.

## Mechanisms supported by the evidence

1. **The notes preserved topics more than learned content.** The configuration
   finding says only, "routing.json was read as the active rule configuration."
   The router finding says where ordered evaluation/disabled/terminal behavior is
   implemented, rather than preserving an exact reusable algorithm or rule data.
   The contract finding describes dimensions to preserve rather than all their
   actual semantics. These notes are useful addresses but are insufficient alone
   to answer delayed routing questions. Source recovery remains necessary.
2. **Recovery has an extra representation step.** A current read gives the familiar
   result with usable `data.text`. Artifact loading gives a byte-page envelope whose
   `data.text` is JSON for the old result. The model must check paging/status,
   decode, check historical identity and preserve distinct source values. The
   submitted code stopped at displaying the wrapper. The hypothesis is that the
   simpler familiar read path wins over this more demanding path; traces establish
   the skipped steps but cannot establish the model's internal reason.
3. **Live-value navigation was incomplete and ambiguous.** The successful cell-87
   full manifest retained three sources, but its 2 KB notice exposed only `r` and
   `reads_batch[0]`, omitting router.py. A generic dict type did not say whether
   the binding was a result envelope or data mapping. The new patch addresses this
   specific gap, not weak note content or all artifact-recovery friction.
4. **Recovery, verification and new work were not composed.** The public code
   repeatedly separates acquisition/printing, hash checks, computation and review.
   Later cells rebuild source mappings even after earlier cells produced usable
   values. This is a work-planning/reuse behavior as well as a storage problem.
5. **Safety signals can favor reacquisition.** Historical evidence correctly does
   not guarantee freshness, and some review instructions explicitly require
   revalidation. However, the delayed-question instructions also explicitly permit
   applicable completed-evidence reuse and require acquisition only for missing or
   changed evidence. No blanket reread requirement explains the whole trace.

Adding another reminder is therefore not an established fix. The experiment already
delivered explicit recovery code and reuse instructions. Nor does this small pair
establish that Luna lacks memory capability or that more capable models would solve it.

## Outcome and stop point

Both independent verifications passed first time; all six answer submissions had
completed required source evidence. Control used 15 calls/$0.03265830 and findings
14/$0.02833852, but tracked source reread lines were 284 and 285 respectively.
All 21 same-epoch outgoing prefix transitions passed. Artifact/shell exposure remains
partially unmapped, and correct source-supported answers do not prove semantic use
of notes. The joint memory-efficiency gate remains failed.

The current bounded binding patch has 216 focused passes and one skip, including
actual PTC use of the delivered content expressions without more reads. No live
benefit is claimed. Further implementation and paid experiments stop here at the
user's request. If resumed, assess content-sufficient notes, direct decoded recovery,
and freshness-vs-content acquisition separately; do not expand the benchmark or
weaken verification to manufacture a reread win.
