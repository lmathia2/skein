import { pathToFileURL } from 'node:url'

const { createPythonExtension } = await import(
  pathToFileURL(`${process.env.PI_CODE_TOOL_PACKAGE}/dist/pi/extension.js`).href
)

function remote(name, params, returns = 'str') {
  return {
    name,
    description: `Run ${name} in the Harbor task workspace.`,
    params,
    returns,
    async execute(args, kwargs) {
      const input = Object.fromEntries(params.map((param, index) => [
        param.name, kwargs[param.name] ?? args[index],
      ]))
      const response = await fetch(process.env.PI_HARBOR_BRIDGE_URL, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ name, input }),
      })
      const result = await response.json()
      if (!response.ok) throw new Error(result.error ?? `Harbor ${name} failed`)
      return result.value
    },
  }
}

const str = (name) => ({ name, type: 'str' })

export default createPythonExtension({
  bridgePiTools: false,
  noBuiltins: true,
  mountWorkspace: false,
  toolStore: false,
  autoApprove: true,
  typeCheck: true,
  limits: { maxDurationSecs: 120, maxMemory: 128 * 1024 * 1024 },
  tools: [
    remote('read', [str('path')]),
    remote('bash', [str('command')]),
    remote('edit', [str('path'), str('old_text'), str('new_text')]),
    remote('write', [str('path'), str('content')]),
  ],
})
