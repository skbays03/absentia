"""Verify Python AST → Entity + FeatureSet extraction."""
from tree_sitter import Language, Parser
import tree_sitter_python

from absentia.extractors.python import extract_python_entities

PY = Language(tree_sitter_python.language())


def _parse(source: str):
    parser = Parser(PY)
    return parser.parse(source.encode()).root_node


def _non_module(root, file_path: str = "x.py"):
    """Yield only function/class/method entities from a parse tree.

    Item B added a module entity to every Python file's extraction.
    Tests that target function/class/method shapes don't want the
    module entity in their unpacking. The module-level tests below
    fish out the module entity explicitly."""
    return [
        (e, f) for e, f in extract_python_entities(root, file_path)
        if e.kind != "module"
    ]


def test_extracts_undecorated_top_level_function():
    root = _parse("def foo():\n    pass\n")
    results = _non_module(root)
    assert len(results) == 1
    entity, features = results[0]
    assert entity.kind == "function"
    assert entity.qualified_name == "x.py::foo"
    assert entity.line == 1
    assert features.get_set("decorator") == frozenset()


def test_extracts_decorated_function_with_decorator_set():
    src = "@audit\n@app.route\ndef create():\n    pass\n"
    root = _parse(src)
    [(_, features)] = _non_module(root)
    assert features.get_set("decorator") == frozenset({"@audit", "@app.route"})


def test_decorator_with_args_strips_call_suffix():
    src = '@app.route("/users")\ndef list_users():\n    pass\n'
    root = _parse(src)
    [(_, features)] = _non_module(root)
    assert features.get_set("decorator") == frozenset({"@app.route"})


def test_extracts_classes_and_methods_alongside_functions():
    src = "class Foo:\n    def bar(self):\n        pass\n\ndef baz():\n    pass\n"
    root = _parse(src)
    by_kind = {(e.kind, e.qualified_name) for e, _ in _non_module(root)}
    assert by_kind == {
        ("class",    "x.py::Foo"),
        ("method",   "x.py::Foo.bar"),
        ("function", "x.py::baz"),
    }


def test_class_carries_parent_class_feature():
    src = "class Foo(Base, Mixin):\n    pass\n"
    root = _parse(src)
    [(entity, features)] = _non_module(root)
    assert entity.kind == "class"
    assert features.get_set("parent_class") == frozenset({"Base", "Mixin"})


def test_class_with_dotted_parent_keeps_dotted_name():
    src = "class Foo(pkg.Base):\n    pass\n"
    root = _parse(src)
    [(_, features)] = _non_module(root)
    assert features.get_set("parent_class") == frozenset({"pkg.Base"})


def test_class_keyword_metaclass_not_counted_as_parent():
    src = "class Foo(Base, metaclass=Meta):\n    pass\n"
    root = _parse(src)
    [(_, features)] = _non_module(root)
    assert features.get_set("parent_class") == frozenset({"Base"})


def test_class_with_no_parents_has_empty_parent_class_set():
    src = "class Foo:\n    pass\n"
    root = _parse(src)
    [(_, features)] = _non_module(root)
    assert features.get_set("parent_class") == frozenset()


def test_decorated_class_collects_decorators_and_parent():
    src = "@registered\nclass Foo(Base):\n    pass\n"
    root = _parse(src)
    [(entity, features)] = _non_module(root)
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
    results = _non_module(root)
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
    [(_, features)] = _non_module(root)
    assert features.get_set("calls") == frozenset({"bar", "baz.qux", "nested"})


def test_function_with_no_calls_has_empty_calls_set():
    root = _parse("def foo():\n    pass\n")
    [(_, features)] = _non_module(root)
    assert features.get_set("calls") == frozenset()


def test_decorator_call_not_counted_as_function_body_call():
    src = '@app.route("/x")\ndef handler():\n    bar()\n'
    root = _parse(src)
    [(_, features)] = _non_module(root)
    # `app.route("/x")` is a call inside the decorator, NOT inside the
    # function body — only `bar` should appear.
    assert features.get_set("calls") == frozenset({"bar"})


# ── call_kwargs (Item C: logging/tracing call-marker gap) ─────────


def test_function_collects_keyword_argument_names():
    """Every keyword argument used in any call inside the function
    body should appear in call_kwargs, suffixed with ``=`` so the
    rendered gap reads "missing request_id=" instead of "missing
    request_id" (avoids confusion with a free identifier)."""
    src = (
        "def handler():\n"
        "    log.info('start', request_id=req, trace_id=t)\n"
        "    db.write(key='x')\n"
    )
    root = _parse(src)
    [(_, features)] = _non_module(root)
    assert features.get_set("call_kwargs") == frozenset(
        {"request_id=", "trace_id=", "key="}
    )


def test_function_with_no_kwargs_has_empty_call_kwargs():
    src = "def silent():\n    log.info('msg')\n    db.write('x')\n"
    root = _parse(src)
    [(_, features)] = _non_module(root)
    assert features.get_set("call_kwargs") == frozenset()


def test_method_carries_call_kwargs_too():
    """Methods need the same coverage — the logging convention
    typically sits on instance methods of a Handler / Service
    class, not free functions."""
    src = (
        "class H:\n"
        "    def post(self):\n"
        "        log.info('m', request_id=self.rid)\n"
    )
    root = _parse(src)
    method = next(
        f for e, f in _non_module(root) if e.kind == "method"
    )
    assert "request_id=" in method.get_set("call_kwargs")


# ── module entity + has_all_export (Item B: __all__ gap) ───────────


def test_every_file_emits_a_module_entity():
    """One module entity per file, regardless of whether it declares
    __all__. Empty files still emit (the missing-__all__ gap is
    interesting precisely because the file lacks something)."""
    src = "def foo():\n    pass\n"
    root = _parse(src)
    entities = list(extract_python_entities(root, "x.py"))
    [(mod_entity, mod_features)] = [
        (e, f) for e, f in entities if e.kind == "module"
    ]
    assert mod_entity.qualified_name == "x.py::__module__"
    assert mod_features.get_set("has_all_export") == frozenset()


def test_module_with_list_all_export_marks_feature():
    src = '__all__ = ["foo"]\n\ndef foo():\n    pass\n'
    root = _parse(src)
    entities = list(extract_python_entities(root, "x.py"))
    mod_features = next(
        f for e, f in entities if e.kind == "module"
    )
    assert mod_features.get_set("has_all_export") == frozenset({"__all__"})


def test_module_with_tuple_all_export_marks_feature():
    """``__all__ = ("a", "b")`` is just as valid as the list form;
    declaring the variable at all is the convention we mine."""
    src = '__all__ = ("foo", "bar")\n'
    root = _parse(src)
    entities = list(extract_python_entities(root, "x.py"))
    mod_features = next(
        f for e, f in entities if e.kind == "module"
    )
    assert mod_features.get_set("has_all_export") == frozenset({"__all__"})


def test_module_with_typed_all_export_marks_feature():
    """PEP 526 type-annotated form: ``__all__: list[str] = [...]``."""
    src = '__all__: list[str] = ["foo"]\n'
    root = _parse(src)
    entities = list(extract_python_entities(root, "x.py"))
    mod_features = next(
        f for e, f in entities if e.kind == "module"
    )
    assert mod_features.get_set("has_all_export") == frozenset({"__all__"})


# ── has_post_init (Item A: config-validation gap) ───────────────────


def test_class_with_post_init_marks_has_post_init():
    src = (
        "class Cfg:\n"
        "    x: int\n"
        "    def __post_init__(self):\n"
        "        if self.x < 0:\n"
        "            raise ValueError(self.x)\n"
    )
    root = _parse(src)
    entities = _non_module(root)
    cls = next(features for entity, features in entities
               if entity.kind == "class")
    assert cls.get_set("has_post_init") == frozenset({"__post_init__"})


def test_class_without_post_init_has_empty_marker():
    src = "class Cfg:\n    x: int\n"
    root = _parse(src)
    entities = _non_module(root)
    cls = next(features for entity, features in entities
               if entity.kind == "class")
    assert cls.get_set("has_post_init") == frozenset()


def test_decorated_post_init_is_still_recognized():
    """A `@some_decorator` on `__post_init__` shouldn't hide it from
    the marker — we walk into decorated_definition the same way the
    rest of the class member dispatch does."""
    src = (
        "class Cfg:\n"
        "    @staticmethod\n"
        "    def __post_init__():\n"
        "        pass\n"
    )
    root = _parse(src)
    entities = _non_module(root)
    cls = next(features for entity, features in entities
               if entity.kind == "class")
    assert cls.get_set("has_post_init") == frozenset({"__post_init__"})
