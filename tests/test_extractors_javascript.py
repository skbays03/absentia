"""Verify JavaScript AST → Entity + FeatureSet extraction."""
from tree_sitter import Language, Parser
import tree_sitter_javascript

from absentia.extractors.javascript import extract_javascript_entities

JS = Language(tree_sitter_javascript.language())


def _parse(source: str):
    return Parser(JS).parse(source.encode()).root_node


def test_extracts_top_level_function_declaration():
    src = "function foo(x) { bar(x); return baz.qux(); }\n"
    root = _parse(src)
    [(entity, features)] = list(extract_javascript_entities(root, "x.js"))
    assert entity.kind == "function"
    assert entity.qualified_name == "x.js::foo"
    assert features.get_set("calls") == frozenset({"bar", "baz.qux"})


def test_extracts_arrow_function_assigned_to_const():
    src = "const greet = (name) => helper(name);\n"
    root = _parse(src)
    [(entity, features)] = list(extract_javascript_entities(root, "x.js"))
    assert entity.kind == "function"
    assert entity.qualified_name == "x.js::greet"
    assert features.get_set("calls") == frozenset({"helper"})


def test_extracts_function_expression_assigned_to_const():
    src = "const adder = function(a, b) { return a + b; };\n"
    root = _parse(src)
    [(entity, _)] = list(extract_javascript_entities(root, "x.js"))
    assert entity.qualified_name == "x.js::adder"


def test_skips_non_function_lexical_declarations():
    src = "const PI = 3.14;\nlet name = 'lacuna';\n"
    root = _parse(src)
    assert list(extract_javascript_entities(root, "x.js")) == []


def test_extracts_class_with_extends():
    src = "class Cat extends Animal { meow() { speak('meow'); } }\n"
    root = _parse(src)
    by_qn = {
        e.qualified_name: (e, f)
        for e, f in extract_javascript_entities(root, "x.js")
    }
    cls_e, cls_f = by_qn["x.js::Cat"]
    assert cls_e.kind == "class"
    assert cls_f.get_set("parent_class") == frozenset({"Animal"})

    method_e, method_f = by_qn["x.js::Cat.meow"]
    assert method_e.kind == "method"
    assert method_f.get_set("calls") == frozenset({"speak"})


def test_extracts_class_with_dotted_extends():
    src = "class Foo extends pkg.Base {}\n"
    root = _parse(src)
    [(_, features)] = list(extract_javascript_entities(root, "x.js"))
    assert features.get_set("parent_class") == frozenset({"pkg.Base"})


def test_class_with_no_extends_has_empty_parent_class():
    src = "class Foo { bar() {} }\n"
    root = _parse(src)
    by_qn = {
        e.qualified_name: (e, f)
        for e, f in extract_javascript_entities(root, "x.js")
    }
    assert by_qn["x.js::Foo"][1].get_set("parent_class") == frozenset()
    assert by_qn["x.js::Foo.bar"][0].kind == "method"


def test_static_methods_are_extracted_as_methods():
    src = "class Math2 { static abs(x) { return Math.abs(x); } }\n"
    root = _parse(src)
    by_qn = {
        e.qualified_name: e
        for e, _ in extract_javascript_entities(root, "x.js")
    }
    assert "x.js::Math2.abs" in by_qn
    assert by_qn["x.js::Math2.abs"].kind == "method"
