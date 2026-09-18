const schema = {
  type: 'object',
  properties: {
    code: { type: 'string', description: 'One persistent Python cell.' },
    result_id: { type: 'string', description: 'Page retained evidence from an earlier cell.' },
    more: { type: 'string', description: 'Short alias for result_id.' },
    offset: { type: 'integer', minimum: 0 },
    limit: { type: 'integer', minimum: 1, maximum: 51200 },
  },
  oneOf: [{ required: ['code'] }, { required: ['result_id'] }, { required: ['more'] }],
  additionalProperties: false,
}

// Independent, stable components. No session state enters the instructions.
export const promptComponents = {
  runtime: 'Run persistent CPython with workspace helpers available as plain functions. Variables/functions persist while the worker lives; committed plain-data variables recover after a restart. json, math, and re are preloaded.',
  workflow: 'Prefer one code call for multi-step work: call helpers, loop/filter/compute, reuse useful variables, and print only what the parent needs.',
  contract: 'Call helpers without await. stdout is returned; the final expression is shown as =>. Contract errors are Python exceptions; bash returns its exit status and verify raises on failure. Imports that access the host are restricted; use the helpers. Paths stay in the workspace; scratch files go in .ptc-scratch/.',
  verification: 'Use bash(...) for exploration and intermediate checks. After changes settle, call verify(...) once for the final required check; call it again only if the workspace revision changes. Custom probes must assert or exit nonzero on mismatch.',
  example: "source = read('src/app.py')\nprint([line for line in source.splitlines() if 'timeout' in line][:20])\nprint(verify('pytest -q', timeout_seconds=120))",
}

export function assemblePrompt(signatures) {
  return [promptComponents.runtime, promptComponents.workflow, signatures,
    promptComponents.contract, promptComponents.verification, promptComponents.example].join('\n\n')
}

export function needsEvidenceReview(finalText, workspaceRevision, verifiedRevision) {
  const unresolved = finalText.replace(/\bno known gaps?\b/gi, '')
  return workspaceRevision !== verifiedRevision || /known gaps?|remaining (?:gap|issue)|unimplemented|failing probe/i.test(unresolved)
}

export function advanceEvidence(outcomes, workspaceRevision, verifiedRevision, nextRevision) {
  const lastVerify = outcomes.findLastIndex(outcome => outcome.operation === 'verify' && outcome.status === 'ok')
  const effectAfterVerify = lastVerify >= 0 && outcomes.slice(lastVerify + 1)
    .some(outcome => ['write', 'edit', 'bash'].includes(outcome.operation))
  if (lastVerify >= 0 && !effectAfterVerify) verifiedRevision = nextRevision
  return [nextRevision ?? workspaceRevision, verifiedRevision]
}

async function bridge(name, input) {
  const response = await fetch(process.env.PI_HARBOR_BRIDGE_URL, {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ name, input }),
  })
  const result = await response.json()
  if (!response.ok) throw new Error(result.error ?? 'Skein PTC request failed')
  return result.value
}

export default async function (pi) {
  const signatures = await bridge('describe_contract', {})
  let workspaceRevision
  let verifiedRevision
  let verificationCalls = 0
  let invalidatedVerificationCalls = 0
  let verifiedCallsAtRevision = 0
  let reviewQueued = false
  pi.registerTool({
    name: 'code', label: 'Skein PTC v4.2',
    description: assemblePrompt(signatures),
    promptSnippet: 'code: persistent Python with workspace helpers; batch and reuse state',
    parameters: schema,
    async execute(_toolCallId, params) {
      const resultId = params.more ?? params.result_id
      const value = await bridge(resultId !== undefined ? 'read_result' : 'execute_code',
        resultId !== undefined
          ? { result_id: resultId, offset: params.offset ?? 0, limit: params.limit ?? 51200 }
          : { code: params.code })
      const outcomes = value.details?.broker_outcomes ?? []
      const nextRevision = value.details?.workspace_revision
      if (workspaceRevision !== undefined && nextRevision !== undefined && workspaceRevision !== nextRevision) {
        invalidatedVerificationCalls += verifiedCallsAtRevision
        verifiedCallsAtRevision = 0
      }
      const successfulVerifies = outcomes.filter(outcome => outcome.operation === 'verify' && outcome.status === 'ok').length
      verificationCalls += outcomes.filter(outcome => outcome.operation === 'verify').length
      ;[workspaceRevision, verifiedRevision] = advanceEvidence(
        outcomes, workspaceRevision, verifiedRevision, nextRevision)
      if (successfulVerifies && verifiedRevision === workspaceRevision) verifiedCallsAtRevision += successfulVerifies
      value.details = { ...(value.details ?? {}), evidence: { workspaceRevision, verifiedRevision,
        verificationCalls, invalidatedVerificationCalls } }
      return { content: [{ type: 'text', text: value.text }], details: value.details ?? {} }
    },
  })
  if (process.env.PTC_EVIDENCE_REVIEW === '1') {
    pi.on('agent_end', async (event) => {
      if (reviewQueued) return
      const finalText = event.messages.filter(message => message.role === 'assistant')
        .flatMap(message => message.content ?? []).filter(part => part.type === 'text')
        .map(part => part.text).join('\n')
      if (!needsEvidenceReview(finalText, workspaceRevision, verifiedRevision)) return
      reviewQueued = true
      pi.sendMessage({ customType: 'ptc-evidence-review', display: true,
        content: 'Before finalizing, compare every requirement with concrete evidence. Run the required final check with verify(...). Do not finish while required behavior remains a known gap.' },
        { deliverAs: 'followUp', triggerTurn: true })
    })
  }
}
