/** Bounded text files for local fixtures; generated code is never executed here. */
import { lstat, mkdir, readFile, writeFile, rename, unlink } from 'node:fs/promises'
import { join } from 'node:path'
import { randomUUID } from 'node:crypto'
import { z } from 'zod'

export const FixturePath = z.string().max(120).refine(path => path.split('/').every(part => /^[A-Za-z0-9_][A-Za-z0-9_.-]*$/.test(part)), 'Use a relative path with safe filename segments')
const MAX_BYTES = 64000

async function target(directory: string, path: string): Promise<string> {
  FixturePath.parse(path)
  const parts = path.split('/')
  let current = directory
  for (let index = 0; index < parts.length; index++) {
    current = join(current, parts[index]!)
    const parent = index < parts.length - 1
    try {
      const stat = await lstat(current)
      if (stat.isSymbolicLink() || (parent ? !stat.isDirectory() : !stat.isFile())) throw new Error('Fixture paths must be ordinary files and directories')
    } catch (e) {
      if ((e as NodeJS.ErrnoException).code !== 'ENOENT') throw e
      if (parent) {
        await mkdir(current, { recursive: true })
        const stat = await lstat(current)
        if (stat.isSymbolicLink() || !stat.isDirectory()) throw new Error('Invalid fixture parent directory')
      }
    }
  }
  return current
}

export async function readFixtureFiles(directory: string, allowed: string[]): Promise<Record<string, string>> {
  const files: Record<string, string> = {}
  for (const path of allowed) {
    const file = await target(directory, path)
    try {
      if ((await lstat(file)).size > MAX_BYTES) throw new Error('Fixture file exceeds 64000 bytes')
      files[path] = await readFile(file, 'utf8')
    } catch (e) { if ((e as NodeJS.ErrnoException).code !== 'ENOENT') throw e }
  }
  return files
}

export async function writeFixtureFile(directory: string, allowed: string[], path: string, content: string): Promise<number> {
  if (!allowed.includes(path)) throw new Error(`Writable files: ${allowed.join(', ')}`)
  const bytes = Buffer.byteLength(content)
  if (bytes > MAX_BYTES) throw new Error('Fixture file exceeds 64000 bytes')
  const file = await target(directory, path), temporary = file + '.' + randomUUID() + '.tmp'
  try { await writeFile(temporary, content, { mode: 0o600, flag: 'wx' }); await rename(temporary, file) }
  finally { await unlink(temporary).catch(() => {}) }
  return bytes
}

export async function seedFixtureFiles(directory: string, allowed: string[], starters: Record<string, string>): Promise<void> {
  for (const path of allowed) {
    const content = starters[path]
    if (content === undefined) continue
    if (Buffer.byteLength(content) > MAX_BYTES) throw new Error('Starter exceeds 64000 bytes')
    const file = await target(directory, path)
    try { await writeFile(file, content, { mode: 0o600, flag: 'wx' }) }
    catch (e) { if ((e as NodeJS.ErrnoException).code !== 'EEXIST') throw e }
  }
}
