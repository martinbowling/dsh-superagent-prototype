import { mkdtemp, mkdir, readFile, rm, symlink, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { Fixture } from '../src/sources/local-fixtures.ts'
import { FixturePath, readFixtureFiles, seedFixtureFiles, writeFixtureFile } from '../src/sources/fixture-files.ts'
let directory: string
beforeEach(async () => { directory = await mkdtemp(join(tmpdir(), 'fixture-files-')) })
afterEach(async () => { await rm(directory, { recursive: true, force: true }) })
describe('bounded fixture projects', () => {
  it('refuses traversal, absolute paths, hidden directories and unsafe segments', () => {
    for (const path of ['../secret', '/tmp/x', 'a/../x', 'a//x', 'a\\x', 'a/.git/config', '.', 'a/']) expect(() => FixturePath.parse(path)).toThrow()
    expect(FixturePath.parse('templates/index.html')).toBe('templates/index.html')
    expect(() => Fixture.parse({ id: 'app', prompt: 'x', writableFiles: ['x','x'] })).toThrow()
  })
  it('seeds only allowed paths and preserves later edits on repeated reads', async () => {
    const allowed = ['app.py', 'static/style.css']
    await seedFixtureFiles(directory, allowed, { 'app.py': 'starter', 'static/style.css': 'body {}', 'other.py': 'not output' })
    await writeFixtureFile(directory, allowed, 'app.py', 'edited')
    await seedFixtureFiles(directory, allowed, { 'app.py': 'starter' })
    expect(await readFixtureFiles(directory, allowed)).toEqual({ 'app.py': 'edited', 'static/style.css': 'body {}' })
    await expect(readFile(join(directory, 'other.py'))).rejects.toMatchObject({ code: 'ENOENT' })
  })
  it('rejects unlisted files and counts UTF-8 bytes, preserving the old file on failure', async () => {
    await writeFixtureFile(directory, ['app.py'], 'app.py', 'old')
    await expect(writeFixtureFile(directory, ['app.py'], 'secret.py', 'x')).rejects.toThrow('Writable files')
    await expect(writeFixtureFile(directory, ['app.py'], 'app.py', '界'.repeat(22000))).rejects.toThrow('64000 bytes')
    expect(await readFile(join(directory, 'app.py'), 'utf8')).toBe('old')
  })
  it('refuses file and parent-directory symlinks', async () => {
    await mkdir(join(directory, 'actual'))
    await writeFile(join(directory, 'actual/x'), 'keep')
    await symlink(join(directory, 'actual'), join(directory, 'alias'))
    await symlink(join(directory, 'actual/x'), join(directory, 'app.py'))
    await expect(writeFixtureFile(directory, ['alias/x'], 'alias/x', 'replace')).rejects.toThrow('ordinary')
    await expect(readFixtureFiles(directory, ['app.py'])).rejects.toThrow('ordinary')
    expect(await readFile(join(directory, 'actual/x'), 'utf8')).toBe('keep')
  })
})
