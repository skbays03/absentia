"""Verify Python AST → Entity + FeatureSet extraction."""
from tree_sitter import Language, Parser
import tree_sitter_python

from lacuna.features import extract_python_functions

PY = Language(tree_sitter_python.language())


def _parse(source: str):
    parser = Parser(PY)
    return parser.parse(source.encode()).root_node


def test_extracts_undecorated_top_level_function():
    root = _parse("def foo():\n    pass\n")
    results = list(extract_python_functions(root, "x.py"))
    assert len(results) == 1
    entity, features = results[0]
    assert entity.kind == "function"
    assert entity.qualified_name == "x.py::foo"
    assert entity.line == 1
    assert features.get_set("decorator") == frozenset()


def test_extracts_decorated_function_with_decorator_set():
    src = "@audit\n@app.route\ndef create():\n    pass\n"
    root = _parse(src)
    [(_, features)] = list(extract_python_functions(root, "x.py"))
    assert features.get_set("decorator") == frozenset({"@audit", "@app.route"})


def test_decorator_with_args_strips_call_suffix():
    src = '@app.route("/users")\ndef list_users():\n    pass\n'
    root = _parse(src)
    [(_, features)] = list(extract_python_functions(root, "x.py"))
    assert features.get_set("decorator") == frozenset({"@app.route"})


def test_skips_methods_inside_classes():
    src = "class Foo:\n    def bar(self):\n        pass\n\ndef baz():\n    pass\n"
    root = _parse(src)
    names = [e.qualified_name for e, _ in extract_python_functions(root, "x.py")]
    assert names == ["x.py::baz"]


def test_handles_multiple_top_level_functions():
    src = "def a():\n    pass\n\n@d\ndef b():\n    pass\n\ndef c():\n    pass\n"
    root = _parse(src)
    results = list(extract_python_functions(root, "x.py"))
    assert [e.qualified_name for e, _ in results] == ["x.py::a", "x.py::b", "x.py::c"]
    assert results[1][1].get_set("decorator") == frozenset({"@d"})
