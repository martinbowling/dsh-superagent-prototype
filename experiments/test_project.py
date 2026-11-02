import json
from pathlib import Path
import unittest
from app_checks import program,FILES
from app_preview import safe_html,local_path
from matrix import schedule
from app_checks_v2 import program as v2_program
from component_checks import program as component_program
from app_reference import REFERENCE
import ast

class ProjectBoundaryTests(unittest.TestCase):
    def test_component_contracts_cannot_substitute_unowned_model_files(self):
        files={**REFERENCE,'app.py':'unowned app','store.py':'owned database'}
        def embedded(source):return ast.literal_eval(ast.parse(source).body[0].value)
        database=embedded(component_program(files,'database'))
        self.assertEqual(database['store.py'],'owned database');self.assertEqual(database['app.py'],REFERENCE['app.py'])
        self.assertEqual(embedded(component_program(files,'integration'))['app.py'],'unowned app')
        self.assertNotIn('UI open filter excludes',program(REFERENCE));self.assertIn('UI open filter excludes',v2_program(REFERENCE))
        with self.assertRaises(ValueError):component_program(files,'unknown')
    def test_project_requires_exact_bounded_files(self):
        files={x:'text' for x in FILES}
        self.assertIn('FILES=',program(files))
        with self.assertRaises(ValueError):program({**files,'../secret':'x'})
        with self.assertRaises(ValueError):program({**files,'app.py':'界'*22000})
    def test_preview_removes_active_content_and_external_targets(self):
        source='<html><head><meta http-equiv="refresh" content="0;url=https://evil"><script>bad()</script></head><body><main><a href="https://evil">external</a><a href="/?status=open">Open</a><form action="//evil" method="post"><input type="password" onclick="bad()" name="title"></form><iframe src="https://evil"></iframe></main></body></html>'
        result=safe_html(source)
        for bad in ['https://evil','//evil','onclick','bad()','<script','<iframe','http-equiv','type="password"']:self.assertNotIn(bad,result)
        self.assertIn('href="/?status=open"',result);self.assertIn('name="title"',result)
    def test_preview_routes_stay_in_the_app(self):
        for value in ['http://evil/','//evil/','/static/../../secret','/%2e%2e/secret','/api/tasks/0','/__preview_stop']:
            self.assertFalse(local_path(value),value)
        for value in ['/','/?status=done','/api/tasks/12','/tasks/12/toggle','/static/style.css']:self.assertTrue(local_path(value),value)
    def test_three_repeats_counterbalance_worker_order(self):
        rows=schedule([1,2,4],3,17)
        self.assertEqual(len(rows),9)
        for repeat in [1,2,3]:self.assertEqual({w for r,w in rows if r==repeat},{1,2,4})
        for position in range(3):self.assertEqual({rows[r*3+position][1] for r in range(3)},{1,2,4})

if __name__=='__main__':unittest.main()
