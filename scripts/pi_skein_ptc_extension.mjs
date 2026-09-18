const schema = {
  type: 'object',
  properties: { code: { type: 'string', description: 'Python source for one persistent cell' } },
  required: ['code'],
  additionalProperties: false,
}

export default function (pi) {
  pi.registerTool({
    name: 'code',
    label: 'Skein PTC',
    description: [
      'Run one cell in Skein\'s persistent CPython worker. Variables and functions persist across calls.',
      'The prebound agent exposes agent.fs.read/write/edit, agent.shell.run, agent.parallel,',
      'and agent.state.list/describe/reuse. Process results in Python and print only useful observations.',
    ].join(' '),
    promptSnippet: 'code: run persistent CPython with Skein workspace capabilities; state persists',
    promptGuidelines: [
      'Use code for repository work. Call workspace capabilities through the prebound agent object, retain intermediate values across cells, and print only evidence needed for the next decision.',
    ],
    parameters: schema,
    async execute(_toolCallId, params) {
      const response = await fetch(process.env.PI_HARBOR_BRIDGE_URL, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ name: 'execute_code', input: { code: params.code } }),
      })
      const result = await response.json()
      if (!response.ok) throw new Error(result.error ?? 'Skein PTC execution failed')
      return { content: [{ type: 'text', text: result.value }], details: {} }
    },
  })
}
