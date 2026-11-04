import { describe, it, expect } from 'vitest'
import { Context } from '@deepseek-ai/cordis'
import { fixtureTrigger, FixtureList, fixtureKind, fixtureReadResult, fixtureReadPage } from '../src/sources/local-fixtures.ts'
import { Setup } from '../src/app/setup.ts'
import { SettingsSchema } from '../src/policy.ts'
import { testMachine } from './fixtures/deployment.ts'

describe('opt-in local fixture experiments', () => {
  it('pages large labeled inputs completely without transport spills or exposing other files', () => {
    const labels = '1234567890123,1\n'.repeat(5062)
    const [task] = FixtureList.parse([{ id: 'isbn13', prompt: 'Revise.', files: { 'labeled.csv': labels } }])
    const result = fixtureReadResult(task!, {}, ['weights.json'])
    expect(result).toMatchObject({ paged: true, files: {} })
    let text = '', offset: number | null = 0
    while (offset !== null) {
      const page = fixtureReadPage(task!, {}, 'labeled.csv', offset, 4000)
      expect(Buffer.byteLength(JSON.stringify(page))).toBeLessThan(32000)
      text += page.content; offset = page.nextOffset
    }
    expect(text).toBe(labels)
    expect(() => fixtureReadPage(task!, {}, 'other.csv', 0, 4000)).toThrow('No such')
  })
  it('returns lossless JSON for standalone fixture reads and retains shared metadata when present', () => {
    const [task] = FixtureList.parse([{ id: 'isbn13', prompt: 'Write weights.', files: { 'weights.json': '' }, writableFiles: ['weights.json'] }])
    const result = fixtureReadResult(task!, { 'weights.json': '' }, ['weights.json'])
    expect(result).toStrictEqual(JSON.parse(JSON.stringify(result)))
    expect(result).not.toHaveProperty('sharedProject')
    expect(result).not.toHaveProperty('publications')
    expect(result.solution).toBeNull()
    const [shared] = FixtureList.parse([{ id: 'taskboard', prompt: 'Build store.', sharedProject: 'taskboard', projectRole: 'database', writableFiles: ['store.py'] }])
    const value = fixtureReadResult(shared!, { 'store.py': 'source' }, ['store.py'])
    expect(value).toStrictEqual(JSON.parse(JSON.stringify(value)))
    expect(value).toMatchObject({ sharedProject: 'taskboard', projectRole: 'database', publications: {} })
  })
  it('creates stable addressed local inputs and never a service URL', () => {
    const [task] = FixtureList.parse([{ id: 'ledger', prompt: 'Fix the ledger.' }])
    const a = fixtureTrigger(task!), b = fixtureTrigger(task!)
    expect(a).toEqual(b)
    expect(a).toMatchObject({ source: 'fixtures', human: true, addressed: 'direct', actor: { id: 'experiment' } })
    expect(a.url).toBeUndefined()
    expect(fixtureKind.scope!({ meta: {} }, {})).toMatchObject({ listened: false })
  })
  it('rejects path-like ids and repeated fixture ids before intake', () => {
    expect(() => FixtureList.parse([{ id: '../escape', prompt: 'x' }])).toThrow()
    expect(() => FixtureList.parse([{ id: 'x', prompt: 'a' }, { id: 'x', prompt: 'b' }])).toThrow()
  })
  it('resolves local setup without accessing credentials or subprocess services', async () => {
    const ctx = new Context()
    const setup = new Setup({ context: ctx, machine: testMachine({ localFixtures: true }),
      lifetime: new AbortController().signal, restart() {} })
    expect(await setup.run(SettingsSchema.parse({ sources: { github: { enabled: false } } }))).toBe(true)
    expect(setup.deployment.github.cli).toBe('/usr/bin/false')
    expect(setup.deployment.repository).toBe('local/fixtures')
    setup.restore()
    await ctx.fiber.dispose()
  })
  it('refuses an enabled external source in fixture mode', async () => {
    const ctx = new Context()
    const setup = new Setup({ context: ctx, machine: testMachine({ localFixtures: true }),
      lifetime: new AbortController().signal, restart() {} })
    await expect(setup.run(SettingsSchema.parse({ sources: { github: { enabled: true } } }))).rejects.toThrow('external Source')
    await ctx.fiber.dispose()
  })
})
