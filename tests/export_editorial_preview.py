"""Export isolated fixture pages to /tmp for browser interception; no HTTP server."""
from pathlib import Path
import json
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).parent))
from test_editorial_web import EditorialTests


def export(destination):
    root=Path(destination)
    root.mkdir(parents=True,exist_ok=True)
    EditorialTests.setUpClass()
    case=EditorialTests('test_home_uses_original_hero_and_honest_empty_history')
    try:
        case.setUp()
        case.session_user(1)
        pages={}
        for route in ('/','/decouvrir','/artistes','/a-propos','/morceaux/1','/connexion','/mon-espace','/espace-auditeur'):
            pages[route]=case.get(route).get_data(as_text=True)
        (root/'pages.json').write_text(json.dumps(pages))
    finally:
        case.doCleanups()
        EditorialTests.doClassCleanups()


if __name__=='__main__':
    export(sys.argv[1])
