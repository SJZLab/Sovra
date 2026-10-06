"""FTF-0003 — real pytest unit tests for scripts/product_truth_check.py's
pure helper logic (Law 16 gate), not throwaway template tests."""

from scripts.product_truth_check import is_test_file


def test_is_test_file_recognizes_test_directories():
    assert is_test_file("apps/web/src/tests/index.ts")
    assert is_test_file("apps/api/test/routes.py")


def test_is_test_file_recognizes_test_suffixes():
    assert is_test_file("apps/web/src/index.test.ts")
    assert is_test_file("apps/web/src/index.spec.ts")
    assert is_test_file("workers/training/train_test.py")
    assert is_test_file("tests/tasks/test_ftf_0002.py")


def test_is_test_file_rejects_production_source():
    assert not is_test_file("apps/web/src/index.ts")
    assert not is_test_file("packages/shared/src/index.ts")


# --- find_callers: a caller must USE the symbol (Law 5), not merely name it ---
import pathlib  # noqa: E402

from scripts.product_truth_check import find_callers  # noqa: E402


def _tree(tmp_path: pathlib.Path, files: dict[str, str | bytes]) -> str:
    root = tmp_path / "apps" / "api"
    for rel, body in files.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(body, bytes):
            f.write_bytes(body)
        else:
            f.write_text(body)
    return str(root)


def test_definition_docstring_comment_and_import_are_not_callers(tmp_path):
    root = _tree(
        tmp_path,
        {
            "defs.py": '"""Mentions build_key."""\n\ndef build_key():\n    return 1\n',
            "doc.py": '"""See build_key."""\n# build_key is used elsewhere\nX = "build_key"\n',
            "imp.py": "from defs import build_key  # noqa: F401\n",
            "shadow.py": "build_key = None\n",
            "pkg/__pycache__/defs.cpython-312.pyc": b"\x00build_key\x00",
            "README.md": "call build_key()\n",
        },
    )
    assert find_callers("build_key", [root]) == []


def test_real_python_use_is_a_caller_including_alias_and_attribute(tmp_path):
    root = _tree(
        tmp_path,
        {
            "defs.py": "def build_key():\n    return 1\n",
            "direct.py": "from defs import build_key\n\nVALUE = build_key()\n",
            "aliased.py": "from defs import build_key as _bk\n\nVALUE = _bk()\n",
            "attr.py": "import defs\n\nVALUE = defs.build_key()\n",
            "passed.py": "from defs import build_key\n\nHANDLERS = [build_key]\n",
        },
    )
    callers = {pathlib.Path(c).name for c in find_callers("build_key", [root])}
    assert callers == {"direct.py", "aliased.py", "attr.py", "passed.py"}


def test_symbol_match_is_whole_word(tmp_path):
    root = _tree(tmp_path, {"cfg.py": "class OidcSettings:\n    pass\n\nOidcSettings()\n"})
    assert find_callers("Settings", [root]) == []


def test_test_suites_in_source_tree_are_not_callers(tmp_path):
    root = _tree(
        tmp_path,
        {"auth/rbac_idor_security_tests.py": "from defs import build_key\n\nbuild_key()\n"},
    )
    assert find_callers("build_key", [root]) == []


def test_typescript_use_counts_but_declaration_import_and_comment_do_not(tmp_path):
    root = _tree(
        tmp_path,
        {
            "web/Selector.tsx": "export function RepoSelector() {\n  return null;\n}\n",
            "web/barrel.ts": "export { RepoSelector } from './Selector';\n",
            "web/note.ts": (
                "// RepoSelector is mounted later\n/* RepoSelector */\n"
                "import { RepoSelector } from './Selector';\n"
            ),
            "web/RepoSelectorLike.ts": "export const RepoSelectorX = 1;\n",
        },
    )
    assert find_callers("RepoSelector", [root]) == []
    page = tmp_path / "apps" / "api" / "web" / "page.tsx"
    page.write_text(
        "import { RepoSelector } from './Selector';\nexport default () => <RepoSelector />;\n"
    )
    assert [pathlib.Path(c).name for c in find_callers("RepoSelector", [root])] == ["page.tsx"]
