import { describe, it, expect } from 'vitest'
import { Context } from '@deepseek-ai/cordis'
import { fixtureTrigger, FixtureList, fixtureKind } from '../src/sources/local-fixtures.ts'
import { Setup } from '../src/app/setup.ts'
import { SettingsSchema } from '../src/policy.ts'
import { testMachine } from './fixtures/deployment.ts'

describe('opt-in local fixture experiments', () => {
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
