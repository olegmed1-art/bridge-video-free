from .source_closure import inventory


def test_closure_includes_function_imports_package_initializers_and_resources(tmp_path):
    (tmp_path/"pkg").mkdir()
    (tmp_path/"pkg/__init__.py").write_text("import json\n")
    (tmp_path/"pkg/entry.py").write_text(
        "from . import guard\n\ndef main():\n import pkg.runtime\n __import__('pkg.dynamic')\n")
    (tmp_path/"pkg/guard.py").write_text("from .runtime import bound\n")
    (tmp_path/"pkg/runtime.py").write_text("bound = 1\n")
    (tmp_path/"pkg/dynamic.py").write_text("import hashlib\n")
    result=inventory(tmp_path, roots=("pkg.entry",), resources=())
    assert set(result["files"])=={
        "pkg/__init__.py","pkg/entry.py","pkg/guard.py","pkg/runtime.py","pkg/dynamic.py"}
    assert result["external_import_roots"]==["hashlib","json"]
    assert result["status"]=="SOURCE_REVIEW_REQUIRED"
    assert result["source_review_complete"] is False


def test_dynamic_import_is_explicit_obligation(tmp_path):
    (tmp_path/"entry.py").write_text(
        "import importlib\n\ndef call(name):\n __import__(name)\n importlib.import_module(name)\n")
    result=inventory(tmp_path,roots=("entry",),resources=())
    assert [site["kind"] for site in result["dynamic_import_sites"]]==["__import__","import_module"]
    assert result["source_review_complete"] is False


def test_root_only_module_includes_its_package_initializer_dependencies(tmp_path):
    (tmp_path/"pkg").mkdir()
    (tmp_path/"pkg/__init__.py").write_text("from . import initializer_guard\n")
    (tmp_path/"pkg/initializer_guard.py").write_text("import hashlib\n")
    (tmp_path/"pkg/entry.py").write_text("bound = 1\n")
    result=inventory(tmp_path,roots=("pkg.entry",),resources=())
    assert set(result["files"])=={
        "pkg/__init__.py","pkg/initializer_guard.py","pkg/entry.py"}
    assert result["external_import_roots"]==["hashlib"]
