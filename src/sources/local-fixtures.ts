/** Opt-in local experiment intake. It never polls or sends to an external service. */
import type { Context } from '@deepseek-ai/cordis'
import { appendFile, readFile, mkdir, writeFile, lstat, realpath } from 'node:fs/promises'
import { join, resolve, relative, isAbsolute } from 'node:path'
import { spawn } from 'node:child_process'
import { setTimeout as delay } from 'node:timers/promises'
import { z } from 'zod'
import { key, type Trigger } from '../core/index.ts'
import { sourceContext } from './host.ts'
import type { SourceKind } from './kind.ts'

export const Fixture = z.object({ id: z.string().regex(/^[a-z0-9-]{1,80}$/),
  prompt: z.string().min(1).max(24000), files: z.record(z.string(), z.string()).default({}) }).strict()
export const FixtureList = z.array(Fixture).max(64).refine(items => new Set(items.map(x => x.id)).size === items.length, 'Duplicate fixture id')
export type FixtureTask = z.infer<typeof Fixture>
export function fixtureTrigger(fixture: FixtureTask): Trigger {
  return { id: key('fixtures', fixture.id), source: 'fixtures', threads: [{ source: 'fixtures', id: fixture.id }],
    actor: { source: 'fixtures', id: 'experiment' }, human: true, addressed: 'direct', at: '2026-01-01T00:00:00.000Z',
    text: fixture.prompt, meta: { fixture: fixture.id },
    channel: { source: 'fixtures', thread: `fixtures:${fixture.id}`, address: { fixture: fixture.id } } }
}
export const fixtureKind: SourceKind = {
  kind: 'fixtures', title: { en: 'Local experiment fixtures', zh: '本地实验' },
  description: { en: 'Explicit local benchmark tasks; replies stay in the experiment state directory.', zh: '仅本地任务和回复。' },
  schema: z.object({}).strict(), fields: [], health: ['fixtures'],
  scope: ({ meta }) => typeof meta.fixture === 'string' ? { listened: true, observed: false } : { listened: false, reason: 'not_a_fixture' },
  channel: {
    // Channel persistence is installed per runtime below, never a global directory.
    send: async () => { throw new Error('Fixture channel is not bound to an experiment runtime') },
    link: () => ({ url: '', label: { en: 'Local fixture', zh: '本地实验' } }),
  },
}
export async function fixtures(root: string): Promise<FixtureTask[]> {
  try { return FixtureList.parse(JSON.parse(await readFile(join(root, 'fixtures.json'), 'utf8'))) }
  catch (error) { if ((error as NodeJS.ErrnoException).code === 'ENOENT') return []; throw error }
}

export const name = 'dsh-superagent-fixtures'
export const inject = ['orchestrator', 'tools']
export function apply(ctx: Context): void {
  const host = sourceContext(ctx.orchestrator, fixtureKind)
  let root = ''
  let logging = Promise.resolve()
  let active = 0, serial = 0
  const callLog = (row: Record<string, unknown>) => {
    if (root) logging = logging.then(() => appendFile(join(root, 'arena-calls.jsonl'), JSON.stringify(row) + '\n', { mode: 0o600 }))
      .catch(error => { ctx.logger.warn('arena call telemetry: %s', String(error)) })
  }
  ctx.on('llm/stream', async function* (options, next) {
    const id = ++serial
    callLog({ id, phase: 'start', at: Date.now(), active: ++active, provider: options.provider, model: options.model, maxTokens: options.maxTokens })
    let first = true, completed = false
    try {
      for await (const chunk of next()) {
        if (first && chunk.type.endsWith('-delta')) { first = false; callLog({ id, phase: 'first_delta', at: Date.now() }) }
        yield chunk
      }
      completed = true
    } finally { callLog({ id, phase: 'end', at: Date.now(), active: --active, completed }) }
  })
  ctx.on('session/event', (session, event) => {
    if (root) logging = logging.then(() => appendFile(join(root, 'arena-events.jsonl'),
      JSON.stringify({ sessionId: session.id, event }) + '\n', { mode: 0o600 })).catch(error => { ctx.logger.warn('arena telemetry: %s', String(error)) })
  })
  ctx.effect(() => async () => { await logging })
  const kind: SourceKind = { ...fixtureKind, channel: { ...fixtureKind.channel!, send: async (_host, message) => {
    const path = join(root, 'fixture-replies.jsonl')
    // Outbox retries retain their id. A local replay must not duplicate a reply.
    let prior = ''
    try { prior = await readFile(path, 'utf8') } catch (e) { if ((e as NodeJS.ErrnoException).code !== 'ENOENT') throw e }
    if (!prior.split('\n').filter(Boolean).some(line => JSON.parse(line).id === message.id))
      await appendFile(path, JSON.stringify(message) + '\n', { mode: 0o600 })
    return 'sent'
  } } }
  ctx.effect(() => ctx.orchestrator.sources.register(kind), 'fixtures: source kind')
  const controller = new AbortController()
  const running = (async () => {
    await host.ready
    if (!host.configured() || !host.deployment.localFixtures) throw new Error('Local fixture source requires localFixtures=true')
    root = resolve(host.deployment.stateDir)
    host.report('fixtures', { state: 'ready', detail: 'Local fixtures only' })
    while (!controller.signal.aborted) {
      if (host.config('fixtures')) for (const task of await fixtures(root)) await host.receive(fixtureTrigger(task))
      await delay(1000, undefined, { signal: controller.signal }).catch(() => {})
    }
  })().catch(error => { host.report('fixtures', { state: 'error', detail: String(error) }) })
  ctx.effect(() => async () => { controller.abort(); await running })

  const locate = async (agentId: string | undefined) => {
    if (!root || !agentId?.startsWith('worker-')) throw new Error('Only a fixture worker can use arena tools')
    const ticket = host.tickets.get(agentId.slice(7))
    const trigger = ticket && host.triggers.get(ticket.origin.triggerId)
    const task = (await fixtures(root)).find(task => task.id === trigger?.meta.fixture)
    if (!ticket?.execution || !task) throw new Error('No fixture is assigned to this worker')
    const directory = await realpath(ticket.execution.directory), base = await realpath(join(root, 'work')), rel = relative(base, directory)
    if (!rel || rel.startsWith('..') || isAbsolute(rel) || (await lstat(directory)).isSymbolicLink()) throw new Error('Invalid fixture workspace')
    return { task, directory }
  }
  const output = { schema: { type: 'object' as const, additionalProperties: true },
    render: (_args: unknown, value: unknown) => [{ type: 'text' as const, text: JSON.stringify(value) }] }
  const parameters = (properties: Record<string, unknown>, required: string[] = []) => ({ type: 'object', properties, required, additionalProperties: false })
  ctx.tools.register({ name: 'arena_read', description: 'Read the assigned fixture requirements, input files, and current solution.',
    parameters: parameters({}), output, execute: async (_args, exec) => {
      const { task, directory } = await locate(exec.agent?.id)
      let solution: string | null = null
      try { solution = await readFile(join(directory, 'solution.py'), 'utf8') } catch { /* Not written yet. */ }
      return { fixture: task.id, prompt: task.prompt, files: task.files, solution }
    } })
  ctx.tools.register({ name: 'arena_write', description: 'Write the complete Python implementation to solution.py in this fixture workspace.',
    parameters: parameters({ content: { type: 'string', maxLength: 64000 } }, ['content']), output,
    execute: async (value, exec) => {
      const { content } = z.object({ content: z.string().max(64000) }).strict().parse(value)
      const { directory } = await locate(exec.agent?.id)
      await mkdir(directory, { recursive: true })
      const target = join(directory, 'solution.py')
      try { if ((await lstat(target)).isSymbolicLink()) throw new Error('Symlink solutions are refused') } catch (e) { if ((e as NodeJS.ErrnoException).code !== 'ENOENT') throw e }
      await writeFile(target, content, { mode: 0o600 })
      return { path: 'solution.py', bytes: Buffer.byteLength(content) }
    } })
  ctx.tools.register({ name: 'arena_test', description: 'Run the assigned fixture tests in a bounded disposable container and return the result.',
    parameters: parameters({}), output, execute: async (_value, exec) => {
      const { task, directory } = await locate(exec.agent?.id)
      const driver = process.env.DSH_ARENA_TEST_DRIVER
      if (!driver) throw new Error('DSH_ARENA_TEST_DRIVER is not configured')
      const source = await readFile(join(directory, 'solution.py'), 'utf8')
      return await new Promise<Record<string, unknown>>((resolve, reject) => {
        const child = spawn('python3', [driver, '--task', task.id.split('--')[0]!], {
          stdio: ['pipe', 'pipe', 'pipe'], signal: AbortSignal.any([exec.signal, AbortSignal.timeout(25000)]) })
        let stdout = '', stderr = ''
        child.stdout.on('data', b => { stdout = (stdout + b).slice(-64000) })
        child.stderr.on('data', b => { stderr = (stderr + b).slice(-2000) })
        child.on('error', reject)
        child.on('close', code => { try { resolve(JSON.parse(stdout)) } catch { reject(new Error(`Test driver ${code}: ${stderr}`)) } })
        child.stdin.end(JSON.stringify({ source }))
      })
    } })
}
