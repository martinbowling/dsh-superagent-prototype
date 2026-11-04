"""Component contracts use reference peers; integration uses only model-authored files."""
from app_reference import REFERENCE
from app_checks_v2 import program as app_program
ROLE_FILES={'database':['store.py'],'api':['app.py'],'ui':['templates/index.html','static/style.css'],'integration':list(REFERENCE)}

def program(files,role):
    if role not in ROLE_FILES:raise ValueError('Unknown project role')
    if role=='integration':return app_program(files)
    peers=dict(REFERENCE)
    for path in ROLE_FILES[role]:
        if path not in files:raise ValueError('Missing component file: '+path)
        peers[path]=files[path]
    return app_program(peers)
