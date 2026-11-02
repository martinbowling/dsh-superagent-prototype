import json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from snapshot import freeze_runtime,verify_snapshot

class SnapshotTests(unittest.TestCase):
    def tree(self,root):
        repo=root/'repo';repo.mkdir()
        for name in ['lib','node_modules/.pnpm/dep']:(repo/name).mkdir(parents=True,exist_ok=True)
        (repo/'lib/index.js').write_text('built before edit');(repo/'node_modules/.pnpm/dep/index.js').write_text('dependency')
        (repo/'node_modules/dep').symlink_to('.pnpm/dep')
        for name in ['package.json','cordis.patch.yml','README.md','pnpm-lock.yaml']:(repo/name).write_text('{}')
        (repo/'experiments').mkdir();(repo/'experiments/check.py').write_text('# frozen grader')
        modules=root/'runtime/node_modules';(modules/'.bin').mkdir(parents=True);(modules/'.bin/dsh').write_text('# installed cli')
        profile=root/'home/profiles/arena';(profile/'node_modules').mkdir(parents=True);(profile/'node_modules/dsh-superagent').symlink_to(repo)
        (profile/'package.json').write_text(json.dumps({'dependencies':{'dsh-superagent':'link:'+str(repo)}}))
        node=root/'node';node.write_text('#!/bin/sh\necho v24\n');node.chmod(0o755)
        return repo,node,{'dsh_bin':str(modules/'.bin/dsh'),'dsh_home':str(root/'home'),'profile':'arena'}
    def test_snapshot_survives_live_rebuild_and_detects_tampering(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);repo,node,config=self.tree(root)
            with patch('snapshot.shutil.which',return_value=str(node)),patch('snapshot.subprocess.check_output',return_value='source-revision\n'):
                frozen=freeze_runtime(config,root/'snapshot',build=False,repository=repo)
            verify_snapshot(frozen);(repo/'lib/index.js').write_text('live edit after snapshot')
            self.assertEqual(Path(frozen['dsh_bin']).parent.parent.name,'node_modules')
            self.assertEqual((root/'snapshot/plugin/lib/index.js').read_text(),'built before edit')
            self.assertEqual((root/'snapshot/home/profiles/arena/node_modules/dsh-superagent').resolve(),(root/'snapshot/plugin').resolve())
            verify_snapshot(frozen)
            target=root/'snapshot/plugin/lib/index.js';target.chmod(0o644);target.write_text('tampered')
            with self.assertRaisesRegex(RuntimeError,'changed'):verify_snapshot(frozen)
    def test_external_dependency_links_are_refused(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);repo,node,config=self.tree(root);outside=root/'outside';outside.write_text('external')
            (repo/'node_modules/outside').symlink_to(outside)
            with patch('snapshot.shutil.which',return_value=str(node)),patch('snapshot.subprocess.check_output',return_value='revision\n'):
                with self.assertRaisesRegex(ValueError,'outside snapshot'):freeze_runtime(config,root/'snapshot',build=False,repository=repo)

if __name__=='__main__':unittest.main()
