// Provider serialization and the v4.1 tool contract are shared, never reimplemented.
// In RPC mode this process makes single model calls; ADK owns continuation.
import { appendFileSync, writeFileSync, readFileSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { createInterface } from 'node:readline'
import { pathToFileURL } from 'node:url'
import { join } from 'node:path'
import extension, { needsEvidenceReview, advanceEvidence, evidenceReviewPrompt } from './pi_skein_ptc_extension.mjs'

const root = process.env.SKEIN_PI_ROOT
const load = path => import(pathToFileURL(join(root, path)).href)
const { ModelRuntime } = await load('packages/coding-agent/dist/core/model-runtime.js')
const { buildSystemPrompt } = await load('packages/coding-agent/dist/core/system-prompt.js')
const { validateToolArguments, createAssistantMessageEventStream } = await load('packages/ai/dist/index.js')
const { Agent } = await load('packages/agent/dist/agent.js')
const replay = process.env.SKEIN_PARITY_REPLAY
  ? JSON.parse(readFileSync(process.env.SKEIN_PARITY_REPLAY, 'utf8')) : null
const runtime = replay ? null : await ModelRuntime.create({
  authPath: join(process.env.PI_CODING_AGENT_DIR, 'auth.json'),
  modelsPath: join(process.env.PI_CODING_AGENT_DIR, 'models.json'),
})
const resolvedModel = replay?.model ?? runtime.getModel(process.env.SKEIN_PARITY_PROVIDER, process.env.SKEIN_PARITY_MODEL)
if (!resolvedModel) throw new Error('Pinned parity model is unavailable')
const model = resolvedModel.provider === 'openrouter'
  ? { ...resolvedModel, api: 'openai-completions' } : resolvedModel
let definition
await extension({ registerTool: tool => { definition = tool }, on: () => {} })
const tools = [{ name: definition.name, description: definition.description, parameters: definition.parameters }]
const systemPrompt = buildSystemPrompt({
  selectedTools: ['code'], toolSnippets: { code: definition.promptSnippet },
  cwd: process.env.SKEIN_PARITY_WORKSPACE, contextFiles: [], skills: [],
})
const reviewPrompt = evidenceReviewPrompt
const logs = process.env.SKEIN_PARITY_LOGS
const contract = { systemPrompt, tools, model: model.id, provider: model.provider,
  api: model.api, reasoning: process.env.SKEIN_PARITY_REASONING,
  maxTokens: Number(process.env.SKEIN_PARITY_MAX_TOKENS), compaction: false,
  reviewPrompt, task: process.env.SKEIN_PARITY_TASK, replay: Boolean(replay), modelDefinition: model,
  runtimeHashes: Object.fromEntries([
    'packages/agent/dist/agent-loop.js', 'packages/coding-agent/dist/core/system-prompt.js',
    'packages/coding-agent/dist/core/model-runtime.js', 'packages/ai/dist/compat.js',
    'packages/ai/dist/api/openai-completions.js', 'packages/ai/dist/api/transform-messages.js',
  ].map(path => [path, createHash('sha256').update(readFileSync(join(root, path))).digest('hex')])) }
const digest = createHash('sha256').update(JSON.stringify(contract)).digest('hex')
writeFileSync(join(logs, 'parity-contract.json'), JSON.stringify({ ...contract, sha256: digest }, null, 2))
let mutation = 0, verified = 0, reviewed = false
const append = (name, data) => appendFileSync(join(logs, name), JSON.stringify(data) + '\n')
const options = {
  reasoning: contract.reasoning, maxTokens: contract.maxTokens,
  maxRetries: 0,
  onPayload: payload => {
    if (model.provider === 'openrouter' && !Array.isArray(payload.messages)) {
      throw new Error('Strict parity requires Chat Completions messages; Responses is not permitted')
    }
    append('provider-requests.jsonl', payload)
    return payload
  },
}
let replayIndex = 0
const stream = (selectedModel, context) => {
  context = { ...context, tools }
  append('model-contexts.jsonl', context)
  if (!replay) return runtime.streamSimple(selectedModel, context, options)
  const message = replay.messages[replayIndex++]
  if (!message) throw new Error('Offline parity replay exhausted')
  const events = createAssistantMessageEventStream()
  events.push({ type: 'start', partial: { ...message, content: [] } })
  events.push({ type: 'done', reason: message.stopReason, message })
  events.end()
  return events
}
const tool = { ...definition, async execute(id, args) {
  const result = await definition.execute(id, args)
  ;[mutation, verified] = advanceEvidence(result.details?.broker_outcomes ?? [], mutation, verified)
  append('ptc-details.jsonl', { toolCallId: id, ...result })
  return result
} }
function review(text) {
  if (reviewed || !needsEvidenceReview(text, mutation, verified)) return null
  reviewed = true
  return reviewPrompt
}
if (process.argv.includes('--pi-loop')) {
  const agent = new Agent({ initialState: {
    systemPrompt, model, thinkingLevel: contract.reasoning, tools: [tool],
  }, streamFn: stream, toolExecution: 'sequential' })
  agent.subscribe(event => {
    process.stdout.write(JSON.stringify(event) + '\n')
  })
  await agent.prompt(process.env.SKEIN_PARITY_TASK)
  if (agent.state.errorMessage) throw new Error(agent.state.errorMessage)
  const text = agent.state.messages.filter(x => x.role === 'assistant')
    .flatMap(x => x.content ?? []).filter(x => x.type === 'text').map(x => x.text).join('\n')
  const reminder = review(text)
  if (reminder) await agent.prompt(reminder)
  if (agent.state.errorMessage) throw new Error(agent.state.errorMessage)
} else {
  // Strictly serial RPC: one provider request or one tool operation at a time.
  for await (const line of createInterface({ input: process.stdin })) {
    try {
      const request = JSON.parse(line)
      let value
      if (request.method === 'contract') value = contract
      else if (request.method === 'complete') {
        value = await stream(model, { systemPrompt, tools, messages: request.messages }).result()
      } else if (request.method === 'tool') {
        try {
          if (request.call.name !== tool.name) throw new Error(`Tool ${request.call.name} not found`)
          const args = validateToolArguments(tool, request.call)
          value = { ...(await tool.execute(request.call.id, args)), isError: false }
        } catch (error) {
          value = { content: [{ type: 'text', text: error.message }], details: {}, isError: true }
        }
      } else if (request.method === 'review') value = review(request.text)
      else throw new Error('Unknown parity RPC operation')
      process.stdout.write(JSON.stringify({ value }) + '\n')
    } catch (error) {
      process.stdout.write(JSON.stringify({ error: error.message }) + '\n')
    }
  }
}
