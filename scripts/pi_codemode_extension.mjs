import { pathToFileURL } from 'node:url'

const { default: codemode } = await import(
  pathToFileURL(`${process.env.PI_CODEMODE_PACKAGE}/dist/index.js`).href
)

export default function harborCodemode(pi) {
  codemode(pi)
  pi.on('session_start', () => pi.setActiveTools(['codemode']))
  pi.on('before_agent_start', (event) => ({
    systemPrompt: event.systemPrompt + '\n\nHarbor task files and commands are available only through mcp.harbor.read, mcp.harbor.bash, mcp.harbor.edit, and mcp.harbor.write inside codemode. Use those tools; project-local read and patch tools are unavailable in this run.',
  }))
}
