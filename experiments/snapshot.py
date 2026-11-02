"""Copy installed runtime, built plugin, dependencies, Node and experiment inputs once per batch."""
import copy,hashlib,json,os,shutil,subprocess
from pathlib import Path

GRADER_FILES=('check.py','checks_v2.py','app_checks.py','app_checks_v2.py','app_reference.py','component_checks.py')

def tree_hashes(root,boundary=None):
    boundary=(boundary or root).resolve()
    result={}
    for p in sorted(root.rglob('*')):
        if p.is_symlink():
            if not p.resolve().is_relative_to(boundary):raise ValueError('Snapshot has an external symlink: '+str(p.relative_to(root)))
            result[str(p.relative_to(root))]='link:'+os.readlink(p)
        elif p.is_file():
            h=hashlib.sha256()
            with p.open('rb') as f:
                for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
            result[str(p.relative_to(root))]=h.hexdigest()
    return result

def snapshot_inputs(config):return Path(config.get('experiment_root',Path(__file__).resolve().parent))

def source_fingerprint(repository):
    files={}
    for name in ['src','prompts','skills','locale','experiments']:
        if (repository/name).exists():
            files.update({name+'/'+k:v for k,v in tree_hashes(repository/name,repository).items() if '__pycache__/' not in k and not k.endswith('.pyc')})
    for name in ['package.json','cordis.patch.yml','pnpm-lock.yaml','tsdown.config.ts']:
        if (repository/name).exists():files[name]=hashlib.sha256((repository/name).read_bytes()).hexdigest()
    return hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest()

def verify_snapshot(config):
    root=Path(config['runtime_snapshot']);receipt=json.loads((root/'receipt.json').read_text())
    actual={}
    for name in ['plugin','runtime','home','bin','experiments']:
        actual.update({name+'/'+k:v for k,v in tree_hashes(root/name,root).items()})
    if actual!=receipt['files']:raise RuntimeError('Frozen runtime/input snapshot changed; no run started')
    digest=hashlib.sha256(json.dumps(actual,sort_keys=True).encode()).hexdigest()
    if digest!=config['runtime_snapshot_sha256']:raise RuntimeError('Snapshot receipt changed')
    return digest

def freeze_runtime(config,directory,*,build=True,repository=None):
    if config.get('runtime_snapshot'):
        verify_snapshot(config);return copy.deepcopy(config)
    directory=directory.resolve();directory.mkdir(parents=True,exist_ok=False)
    repository=(repository or Path(__file__).resolve().parent.parent).resolve()
    source_before=source_fingerprint(repository)
    if build:subprocess.run(['pnpm','build'],cwd=repository,check=True,stdout=subprocess.DEVNULL)
    if not (repository/'lib/index.js').is_file():raise ValueError('Build the plugin before freezing it')
    profile=config.get('profile','arena');source_profile=Path(config['dsh_home'])/'profiles'/profile
    runtime=Path(config['dsh_bin']).absolute().parent.parent
    if runtime.name!='node_modules' or not Path(config['dsh_bin']).is_file():raise ValueError('dsh_bin must be inside installed node_modules/.bin')
    plugin=directory/'plugin';plugin.mkdir()
    for name in ['lib','node_modules','prompts','skills','locale']:
        if (repository/name).exists():shutil.copytree(repository/name,plugin/name,symlinks=True)
    for name in ['package.json','cordis.patch.yml','README.md','pnpm-lock.yaml']:
        shutil.copy2(repository/name,plugin/name)
    (directory/'runtime').mkdir();shutil.copytree(runtime,directory/'runtime/node_modules',symlinks=True)
    shutil.copytree(repository/'experiments',directory/'experiments',symlinks=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    target=directory/'home/profiles'/profile;target.mkdir(parents=True)
    for name in ['package.json','cordis.patch.yml']:
        if (source_profile/name).exists():shutil.copy2(source_profile/name,target/name)
    shutil.copytree(source_profile/'node_modules',target/'node_modules',symlinks=True)
    # Rewrite copied links using longest-prefix matching, including the profile's development link.
    mapping=[(repository.resolve(),plugin),(runtime.resolve(),directory/'runtime/node_modules'),(source_profile.resolve(),target)]
    mapping.sort(key=lambda pair:len(str(pair[0])),reverse=True)
    for p in directory.rglob('*'):
        if not p.is_symlink():continue
        source_relative=p.relative_to(directory)
        if source_relative.parts[0]=='plugin':source=repository/Path(*source_relative.parts[1:])
        elif source_relative.parts[:2]==('runtime','node_modules'):source=runtime/Path(*source_relative.parts[2:])
        elif source_relative.parts[:3]==('home','profiles',profile):source=source_profile/Path(*source_relative.parts[3:])
        else:raise ValueError('Unexpected symlink in snapshot inputs')
        original=source.resolve(strict=True)
        for old,new in mapping:
            if original.is_relative_to(old):
                dest=new/original.relative_to(old);p.unlink();p.symlink_to(os.path.relpath(dest,p.parent));break
        else:raise ValueError('Installed dependency links outside snapshot roots: '+str(source))
    package=json.loads((target/'package.json').read_text());package['dependencies']['dsh-superagent']='link:'+str(plugin);(target/'package.json').write_text(json.dumps(package,indent=2)+'\n')
    binary=directory/'bin';binary.mkdir();node=shutil.which('node')
    if not node:raise ValueError('Node is unavailable')
    shutil.copy2(Path(node).resolve(),binary/'node')
    if source_fingerprint(repository)!=source_before:raise RuntimeError('Live source changed during build/snapshot; no run started')
    result=copy.deepcopy(config);result.update(dsh_bin=str(directory/'runtime/node_modules/.bin/dsh'),dsh_home=str(directory/'home'),experiment_root=str(directory/'experiments'),runtime_snapshot=str(directory),node_bin_dir=str(binary),port_lock_home=str(Path(config['dsh_home']).resolve()))
    files={}
    for name in ['plugin','runtime','home','bin','experiments']:files.update({name+'/'+k:v for k,v in tree_hashes(directory/name,directory).items()})
    digest=hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest()
    revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repository,text=True).strip()
    dirty=bool(subprocess.check_output(['git','status','--porcelain'],cwd=repository,text=True).strip())
    receipt={'schema':1,'files':files,'sha256':digest,'source_revision':revision,'source_dirty':dirty,'source_inputs_sha256':source_before,'node_version':subprocess.check_output([str(binary/'node'),'--version'],text=True).strip()}
    (directory/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n');result['runtime_snapshot_sha256']=digest
    # A hash check before every arm detects edits; read-only files also discourage accidental changes.
    for p in directory.rglob('*'):
        if p.is_file() and not p.is_symlink():p.chmod(p.stat().st_mode & ~0o222)
    return result
