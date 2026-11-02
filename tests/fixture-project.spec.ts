import { mkdtemp, rm, readFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { beforeEach, afterEach, describe, expect, it } from 'vitest'
import { FixtureProject, PROJECT_FILES, ROLE_FILES, type ProjectRole } from '../src/sources/fixture-project.ts'
import { Fixture, FixtureList } from '../src/sources/local-fixtures.ts'
let root: string
beforeEach(async () => { root = await mkdtemp(join(tmpdir(), 'fixture-project-')) })
afterEach(async () => { await rm(root, { recursive: true, force: true }) })
const passed = { passed: true, passed_cases: 60, total_cases: 60 }
const starters = Object.fromEntries(PROJECT_FILES.map(p => [p, 'starter ' + p]))
async function publish(project: FixtureProject, role: ProjectRole) {
  const snapshot = await project.snapshot(role)
  await project.recordTest(role, snapshot.sha256, passed)
  return project.publish(role)
}
describe('shared fixture project', () => {
  it('pairs roles with exact file ownership and allows shared reads', async () => {
    expect(() => Fixture.parse({ id: 'a', prompt: 'x', sharedProject: 'p' })).toThrow('paired')
    expect(() => Fixture.parse({ id: 'a', prompt: 'x', sharedProject: 'p', projectRole: 'ui', writableFiles: ['app.py'] })).toThrow('owned')
    expect(() => FixtureList.parse(['a','b'].map(id => ({ id, prompt: 'x', sharedProject: 'p', projectRole: 'database', writableFiles: ['store.py'] })))).toThrow('owner')
    const project = await FixtureProject.create(root, 'taskboard', starters)
    await project.write('database', 'store.py', 'database implementation')
    expect((await project.snapshot('api')).files['store.py']).toBe('database implementation')
    await expect(project.write('ui', 'app.py', 'overwrite')).rejects.toThrow('Writable files')
    await expect(project.write('integration', 'app.py', 'early')).rejects.toThrow('waits')
  })
  it('requires current passing tests and locks a published component', async () => {
    const project = await FixtureProject.create(root, 'taskboard', starters)
    await expect(project.publish('database')).rejects.toThrow('passing tests')
    const before = await project.snapshot('database')
    await project.write('database', 'store.py', 'edited')
    await expect(project.recordTest('database', before.sha256, passed)).rejects.toThrow('changed')
    const after = await project.snapshot('database')
    await project.recordTest('database', after.sha256, { passed: false, passed_cases: 59, total_cases: 60 })
    await expect(project.publish('database')).rejects.toThrow('passing tests')
    await publish(project, 'database')
    await expect(project.write('database', 'store.py', 'late edit')).rejects.toThrow('locked')
    await expect(FixtureProject.create(root, 'taskboard', starters)).rejects.toThrow('fresh run')
  })
  it('preserves concurrent publications and lets integration repair the combined revision', async () => {
    const project = await FixtureProject.create(root, 'taskboard', starters)
    await Promise.all((['database', 'api', 'ui'] as const).map(role => publish(project, role)))
    const before = await project.snapshot('integration')
    expect(Object.keys(before.publications).sort()).toEqual(['api', 'database', 'ui'])
    await project.write('integration', 'app.py', 'integrated')
    await expect(project.recordTest('integration', before.sha256, passed)).rejects.toThrow('changed')
    const receipt = await publish(project, 'integration')
    expect(receipt.sha256).toBe((await project.snapshot('integration')).sha256)
    const saved = JSON.parse(await readFile(join(project.directory, 'publications.json'), 'utf8'))
    expect(Object.keys(saved)).toHaveLength(4)
    expect(saved.integration.files).toEqual(ROLE_FILES.integration)
    await expect(project.write('integration', 'app.py', 'after final')).rejects.toThrow('locked')
    expect(await project.publish('api')).toEqual(before.publications.api)
  })
})
