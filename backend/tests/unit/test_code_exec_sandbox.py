"""Sandbox hardening (pandas-ai #43 'exec is risky' adoption)."""
from app.agent.nodes.code_executor import validate_code


def test_import_whitelist_enforced():
    ok, msg = validate_code("import requests\nresult = requests.get('http://x')")
    assert not ok and "whitelist" in msg

    ok, msg = validate_code("from pathlib import Path\nresult = Path('.').read_text()")
    assert not ok and "whitelist" in msg

    ok, _ = validate_code("import pandas as pd\nresult = pd.DataFrame([1]).to_dict()")
    assert ok


def test_plain_open_blocked():
    ok, msg = validate_code("result = open('secrets.txt').read()")
    assert not ok and "open" in msg


def test_dunder_subclasses_escape_blocked():
    code = ("result = ().__class__.__base__.__subclasses__()"
            ".__len__()")
    ok, msg = validate_code(code)
    assert not ok


def test_globals_access_blocked():
    ok, msg = validate_code("result = list(globals().keys())")
    assert not ok


def test_legit_pandas_analysis_passes():
    code = (
        "import pandas as pd\n"
        "df = pd.DataFrame(_data)\n"
        "result = df.describe().to_dict()\n"
    )
    ok, msg = validate_code(code)
    assert ok, msg


def test_benign_dunder_len_allowed():
    ok, _ = validate_code("result = {'n': _data.__len__()}")
    assert ok
