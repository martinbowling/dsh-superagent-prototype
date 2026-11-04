/** Shared synthetic project state. Only the trusted fixture tools touch this directory. */
import { mkdir, lstat, writeFile, rename } from 'node:fs/promises'
import { join } from 'node:path'
import { createHash, randomUUID } from 'node:crypto'
import { readFixtureFiles, writeFixtureFile, seedFixtureFiles } from './fixture-files.ts'

export const PROJECT_FILES = ['app.py', 'store.py', 'templates/index.html', 'static/style.css']
export const ROLE_FILES = { database: ['store.py'], api: ['app.py'], ui: ['templates/index.html', 'static/style.css'], integration: PROJECT_FILES }
export type ProjectRole = keyof typeof ROLE_FILES
type Publication = { sha256: string; at: string; files: string[]; passedCases: number; totalCases: number }
type TestResult = { passed?: boolean; passed_cases?: number; total_cases?: number }
const digest = (files: Record<string, string>, allowed: string[]) => createHash('sha256').update(JSON.stringify(allowed.map(path => [path, files[path] ?? null]))).digest('hex')

/** Owns component revisions, serialized writes, test receipts and publication locks for one shared fixture app. */
export class FixtureProject {
  private chain: Promise<unknown> = Promise.resolve()
  private publications: Partial<Record<ProjectRole, Publication>> = {}
  private tests: Partial<Record<ProjectRole, { sha256: string; result: TestResult }>> = {}
  private constructor(readonly directory: string) {}
  static async create(root: string, id: string, starters: Record<string, string>): Promise<FixtureProject> {
    if (!/^[a-z0-9-]{1,80}$/.test(id)) throw new Error('Invalid shared project id')
    for (const directory of [join(root, 'projects'), join(root, 'projects', id)]) {
      await mkdir(directory, { recursive: true })
      const stat = await lstat(directory)
      if (stat.isSymbolicLink() || !stat.isDirectory()) throw new Error('Invalid project directory')
    }
    const project = new FixtureProject(join(root, 'projects', id))
    try { await lstat(join(project.directory, 'publications.json')); throw new Error('Published project cannot be resumed by a fresh runtime; use a fresh run') }
    catch (e) { if ((e as NodeJS.ErrnoException).code !== 'ENOENT') throw e }
    await seedFixtureFiles(project.directory, PROJECT_FILES, starters)
    return project
  }
  private serial<T>(fn: () => Promise<T>): Promise<T> {
    const result = this.chain.then(fn)
    this.chain = result.catch(() => {})
    return result
  }
  private ready(role: ProjectRole): void {
    if (role === 'integration' && !(['database', 'api', 'ui'] as const).every(x => this.publications[x]))
      throw new Error('Integration waits for database, API and UI publications')
  }
  snapshot(role: ProjectRole) {
    return this.serial(async () => {
      const files = await readFixtureFiles(this.directory, PROJECT_FILES)
      return { files, sha256: digest(files, ROLE_FILES[role]), publications: structuredClone(this.publications) }
    })
  }
  write(role: ProjectRole, path: string, content: string) {
    return this.serial(async () => {
      this.ready(role)
      if (this.publications[role]) throw new Error('Published component is locked; integration owns later repairs')
      return writeFixtureFile(this.directory, ROLE_FILES[role], path, content)
    })
  }
  recordTest(role: ProjectRole, sha256: string, result: TestResult) {
    return this.serial(async () => {
      this.ready(role)
      const current = digest(await readFixtureFiles(this.directory, PROJECT_FILES), ROLE_FILES[role])
      if (current !== sha256) throw new Error('Files changed during the test; test the current revision again')
      this.tests[role] = { sha256, result }
    })
  }
  publish(role: ProjectRole) {
    return this.serial(async () => {
      this.ready(role)
      if (this.publications[role]) return this.publications[role]!
      const files = await readFixtureFiles(this.directory, PROJECT_FILES), sha256 = digest(files, ROLE_FILES[role])
      const test = this.tests[role]
      if (!test || test.sha256 !== sha256 || !test.result.passed || !test.result.total_cases || test.result.passed_cases !== test.result.total_cases)
        throw new Error('Publish requires passing tests of the current owned files')
      const publication: Publication = { sha256, files: ROLE_FILES[role], at: new Date().toISOString(), passedCases: test.result.passed_cases, totalCases: test.result.total_cases }
      this.publications[role] = publication
      const target = join(this.directory, 'publications.json'), temporary = target + '.' + randomUUID()
      try { await writeFile(temporary, JSON.stringify(this.publications, null, 2), { mode: 0o600, flag: 'wx' }); await rename(temporary, target) }
      catch (e) { delete this.publications[role]; throw e }
      return publication
    })
  }
}
