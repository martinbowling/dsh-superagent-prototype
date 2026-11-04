/** Opt-in local experiment intake. It never polls or sends to an external service. */
import type { Context } from '@deepseek-ai/cordis'
import { appendFile, readFile, lstat, realpath } from 'node:fs/promises'
import { join, resolve, relative, isAbsolute } from 'node:path'
import { spawn } from 'node:child_process'
import { setTimeout as delay } from 'node:timers/promises'
import { z } from 'zod'
import { key, type Trigger } from '../core/index.ts'
import { sourceContext } from './host.ts'
import type { SourceKind } from './kind.ts'
import { FixturePath, readFixtureFiles, seedFixtureFiles, writeFixtureFile } from './fixture-files.ts'
import { FixtureProject, PROJECT_FILES, ROLE_FILES, type ProjectRole } from './fixture-project.ts'

export const Fixture = z.object({ id: z.string().regex(/^[a-z0-9-]{1,80}$/),
  prompt: z.string().min(1).max(24000), files: z.record(z.string(), z.string()).default({}),
  writableFiles: z.array(FixturePath).min(1).max(4).refine(paths => new Set(paths).size === paths.length, 'Duplicate output path').optional(),
  sharedProject: z.string().regex(/^[a-z0-9-]{1,80}$/).optional(),
  projectRole: z.enum(['database', 'api', 'ui', 'integration']).optional(),
}).strict().superRefine((task, ctx) => {
  if (Boolean(task.sharedProject) !== Boolean(task.projectRole)) ctx.addIssue({ code: 'custom', message: 'Shared project and role must be paired' })
  if (task.projectRole && JSON.stringify([...(task.writableFiles ?? [])].sort()) !== JSON.stringify([...ROLE_FILES[task.projectRole]].sort()))
    ctx.addIssue({ code: 'custom', message: 'Shared project role must use its exact owned files' })
})
export const FixtureList = z.array(Fixture).max(64).refine(items => new Set(items.map(x => x.id)).size === items.length, 'Duplicate fixture id')
  .refine(items => { const owners = items.filter(x => x.sharedProject).map(x => `${x.sharedProject}:${x.projectRole}`); return new Set(owners).size === owners.length }, 'Duplicate shared component owner')
export type FixtureTask = z.infer<typeof Fixture>
/** Build a lossless JSON tool result; optional shared metadata must be omitted, never undefined. */
export function fixtureReadResult(task: FixtureTask, workspace: Record<string, string>, allowed: string[], publications: Record<string, unknown> = {}) {
  const result = { fixture: task.id, prompt: task.prompt, files: task.files, writableFiles: allowed, workspace,
    ...(task.sharedProject ? { sharedProject: task.sharedProject, projectRole: task.projectRole!, publications } : {}),
    solution: workspace['solution.py'] ?? null }
  if (Buffer.byteLength(JSON.stringify(result)) <= 32000) return result
  const catalog = (files: Record<string, string>) => Object.fromEntries(Object.entries(files).map(([path, content]) => [path, { characters: content.length, bytes: Buffer.byteLength(content) }]))
  return { ...result, files: {}, workspace: {}, solution: null, paged: true,
    inputFiles: catalog(task.files), workspaceFiles: catalog(workspace),
    instructions: 'Large files require paged reads. Call arena_read({path, offset:0, limit:4000}), then follow nextOffset until null. Offsets count characters. No input files are omitted from the catalogs.' }
}
/** Bound a file response below the transport spill threshold, including escaped JSON. */
export function fixtureReadPage(task: FixtureTask, workspace: Record<string, string>, path: string, offset: number, limit: number) {
  const content = workspace[path] ?? task.files[path]
  if (content === undefined) throw new Error('No such fixture input or allowed workspace file')
  const end = Math.min(offset + limit, content.length)
  return { fixture: task.id, path, offset, content: content.slice(offset, end), totalCharacters: content.length,
    nextOffset: end < content.length ? end : null }
}
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
  const projects = new Map<string, Promise<FixtureProject>>()
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
    const allowed = task.writableFiles ?? ['solution.py']
    if (task.sharedProject) {
      let project = projects.get(task.sharedProject)
      if (!project) { project = FixtureProject.create(root, task.sharedProject, task.files); projects.set(task.sharedProject, project) }
      return { task, directory, allowed, project: await project }
    }
    if (task.writableFiles) await seedFixtureFiles(directory, allowed, task.files)
    return { task, directory, allowed, project: undefined }
  }
  const output = { schema: { type: 'object' as const, additionalProperties: true },
    render: (_args: unknown, value: unknown) => [{ type: 'text' as const, text: JSON.stringify(value) }] }
  const parameters = (properties: Record<string, unknown>, required: string[] = []) => ({ type: 'object', properties, required, additionalProperties: false })
  ctx.tools.register({ name: 'arena_read', description: 'Read fixture requirements and current files. Large files return catalogs; read a named path in pages and follow nextOffset until null.',
    parameters: parameters({ path: { type: 'string' }, offset: { type: 'integer', minimum: 0 }, limit: { type: 'integer', minimum: 1, maximum: 4000 } }), output, execute: async (args, exec) => {
      const page = z.object({ path: FixturePath.optional(), offset: z.number().int().min(0).default(0), limit: z.number().int().min(1).max(4000).default(4000) }).strict().parse(args)
      const { task, directory, allowed, project } = await locate(exec.agent?.id)
      const snapshot = project && await project.snapshot(task.projectRole!)
      const workspace = snapshot?.files ?? await readFixtureFiles(directory, allowed)
      if (page.path) return fixtureReadPage(task, workspace, page.path, page.offset, page.limit)
      return fixtureReadResult(task, workspace, allowed, snapshot?.publications)
    } })
  ctx.tools.register({ name: 'arena_write', description: 'Write a complete allowed output file. path defaults to solution.py; app fixtures list their writableFiles in arena_read.',
    parameters: parameters({ path: { type: 'string' }, content: { type: 'string', maxLength: 64000 } }, ['content']), output,
    execute: async (value, exec) => {
      const { content, path } = z.object({ content: z.string().max(64000), path: FixturePath.default('solution.py') }).strict().parse(value)
      const { directory, allowed, task, project } = await locate(exec.agent?.id)
      return { path, bytes: project ? await project.write(task.projectRole!, path, content) : await writeFixtureFile(directory, allowed, path, content) }
    } })
  ctx.tools.register({ name: 'arena_test', description: 'Run the assigned fixture tests in a bounded disposable container and return the result.',
    parameters: parameters({}), output, execute: async (_value, exec) => {
      const { task, directory, allowed, project } = await locate(exec.agent?.id)
      const driver = process.env.DSH_ARENA_TEST_DRIVER
      if (!driver) throw new Error('DSH_ARENA_TEST_DRIVER is not configured')
      const snapshot = project && await project.snapshot(task.projectRole!)
      const files = snapshot?.files ?? await readFixtureFiles(directory, allowed)
      const args = [driver, '--task', task.sharedProject ? 'taskboard' : task.id.split('--')[0]!]
      if (task.projectRole) args.push('--role', task.projectRole)
      const result = await new Promise<Record<string, unknown>>((resolve, reject) => {
        const child = spawn('python3', args, {
          stdio: ['pipe', 'pipe', 'pipe'], signal: AbortSignal.any([exec.signal, AbortSignal.timeout(task.writableFiles ? 45000 : 25000)]) })
        let stdout = '', stderr = ''
        child.stdout.on('data', b => { stdout = (stdout + b).slice(-64000) })
        child.stderr.on('data', b => { stderr = (stderr + b).slice(-2000) })
        child.on('error', reject)
        child.on('close', code => { try { resolve(JSON.parse(stdout)) } catch { reject(new Error(`Test driver ${code}: ${stderr}`)) } })
        child.stdin.end(JSON.stringify(task.writableFiles ? { files } : { source: files['solution.py'] }))
      })
      if (project) await project.recordTest(task.projectRole!, snapshot!.sha256, result)
      return result
    } })
  ctx.tools.register({ name: 'arena_publish', description: 'Lock a shared component after passing tests of its current revision, then report ready. Integration waits for database/API/UI publications.',
    parameters: parameters({}), output, execute: async (_value, exec) => {
      const { task, project } = await locate(exec.agent?.id)
      if (!project) throw new Error('Publish is only available to shared-project fixtures')
      return { role: task.projectRole, publication: await project.publish(task.projectRole as ProjectRole), readableFiles: PROJECT_FILES }
    } })
}
