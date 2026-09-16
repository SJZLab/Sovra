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
