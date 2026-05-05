"""Verify Python AST → Entity + FeatureSet extraction."""
from tree_sitter import Language, Parser
import tree_sitter_python

from lacuna.extractors.python import extract_python_entities

PY = Language(tree_sitter_python.language())


def _parse(source: str):
    parser = Parser(PY)
    return parser.parse(source.encode()).root_node


def test_extracts_undecorated_top_level_function():
    root = _parse("def foo():\n    pass\n")
    results = list(extract_python_entities(root, "x.py"))
    assert len(results) == 1
    entity, features = results[0]
    assert entity.kind == "function"
    assert entity.qualified_name == "x.py::foo"
    assert entity.line == 1
    assert features.get_set("decorator") == frozenset()


def test_extracts_decorated_function_with_decorator_set():
    src = "@audit\n@app.route\ndef create():\n    pass\n"
    root = _parse(src)
    [(_, features)] = list(extract_python_entities(root, "x.py"))
    assert features.get_set("decorator") == frozenset({"@audit", "@app.route"})


def test_decorator_with_args_strips_call_suffix():
    src = '@app.route("/users")\ndef list_users():\n    pass\n'
    root = _parse(src)
    [(_, features)] = list(extract_python_entities(root, "x.py"))
    assert features.get_set("decorator") == frozenset({"@app.route"})


def test_extracts_classes_and_methods_alongside_functions():
    src = "class Foo:\n    def bar(self):\n        pass\n\ndef baz():\n    pass\n"
    root = _parse(src)
    by_kind = {(e.kind, e.qualified_name) for e, _ in extract_python_entities(root, "x.py")}
    assert by_kind == {
        ("class",    "x.py::Foo"),
        ("method",   "x.py::Foo.bar"),
        ("function", "x.py::baz"),
    }


def test_class_carries_parent_class_feature():
    src = "class Foo(Base, Mixin):\n    pass\n"
    root = _parse(src)
    [(entity, features)] = list(extract_python_entities(root, "x.py"))
    assert entity.kind == "class"
    assert features.get_set("parent_class") == frozenset({"Base", "Mixin"})


def test_class_with_dotted_parent_keeps_dotted_name():
    src = "class Foo(pkg.Base):\n    pass\n"
    root = _parse(src)
    [(_, features)] = list(extract_python_entities(root, "x.py"))
    assert features.get_set("parent_class") == frozenset({"pkg.Base"})


def test_class_keyword_metaclass_not_counted_as_parent():
    src = "class Foo(Base, metaclass=Meta):\n    pass\n"
    root = _parse(src)
    [(_, features)] = list(extract_python_entities(root, "x.py"))
    assert features.get_set("parent_class") == frozenset({"Base"})


def test_class_with_no_parents_has_empty_parent_class_set():
    src = "class Foo:\n    pass\n"
    root = _parse(src)
    [(_, features)] = list(extract_python_entities(root, "x.py"))
    assert features.get_set("parent_class") == frozenset()


def test_decorated_class_collects_decorators_and_parent():
    src = "@registered\nclass Foo(Base):\n    pass\n"
    root = _parse(src)
    [(entity, features)] = list(extract_python_entities(root, "x.py"))
    assert entity.kind == "class"
    assert features.get_set("decorator") == frozenset({"@registered"})
    assert features.get_set("parent_class") == frozenset({"Base"})


def test_method_decorators_recorded_separately_from_class_decorators():
    src = (
        "@registered\n"
        "class Foo(Base):\n"
        "    @cached\n"
        "    def bar(self):\n"
        "        return helper()\n"
    )
    root = _parse(src)
    by_qn = {e.qualified_name: (e, f) for e, f in extract_python_entities(root, "x.py")}
    cls_entity, cls_features = by_qn["x.py::Foo"]
    method_entity, method_features = by_qn["x.py::Foo.bar"]
    assert cls_features.get_set("decorator") == frozenset({"@registered"})
    assert method_entity.kind == "method"
    assert method_features.get_set("decorator") == frozenset({"@cached"})
    assert method_features.get_set("calls") == frozenset({"helper"})


def test_handles_multiple_top_level_functions():
    src = "def a():\n    pass\n\n@d\ndef b():\n    pass\n\ndef c():\n    pass\n"
    root = _parse(src)
    results = list(extract_python_entities(root, "x.py"))
    assert [e.qualified_name for e, _ in results] == ["x.py::a", "x.py::b", "x.py::c"]
    assert results[1][1].get_set("decorator") == frozenset({"@d"})


def test_extracts_calls_inside_function_body():
    src = (
        "def foo():\n"
        "    bar()\n"
        "    baz.qux(1, 2)\n"
        "    if True:\n"
        "        nested()\n"
    )
    root = _parse(src)
    [(_, features)] = list(extract_python_entities(root, "x.py"))
    assert features.get_set("calls") == frozenset({"bar", "baz.qux", "nested"})


def test_function_with_no_calls_has_empty_calls_set():
    root = _parse("def foo():\n    pass\n")
    [(_, features)] = list(extract_python_entities(root, "x.py"))
    assert features.get_set("calls") == frozenset()


def test_decorator_call_not_counted_as_function_body_call():
    src = '@app.route("/x")\ndef handler():\n    bar()\n'
    root = _parse(src)
    [(_, features)] = list(extract_python_entities(root, "x.py"))
    # `app.route("/x")` is a call inside the decorator, NOT inside the
    # function body — only `bar` should appear.
    assert features.get_set("calls") == frozenset({"bar"})
