"""Verify C AST → Entity + FeatureSet extraction."""
from tree_sitter import Language, Parser
import tree_sitter_c

from lacuna.extractors.c import extract_c_entities

C = Language(tree_sitter_c.language())


def _parse(source: str):
    return Parser(C).parse(source.encode()).root_node


def test_extracts_top_level_function():
    src = "int add(int a, int b) { return helper(a) + b; }\n"
    root = _parse(src)
    [(entity, features)] = list(extract_c_entities(root, "x.c"))
    assert entity.kind == "function"
    assert entity.qualified_name == "x.c::add"
    assert "helper" in features.get_set("calls")


def test_extracts_struct():
    src = "struct Point { int x; int y; };\n"
    root = _parse(src)
    [(entity, _)] = list(extract_c_entities(root, "x.c"))
    assert entity.kind == "struct"
    assert entity.qualified_name == "x.c::Point"


def test_anonymous_struct_skipped():
    src = "struct { int x; } ;\n"
    root = _parse(src)
    assert list(extract_c_entities(root, "x.c")) == []


def test_multiple_functions_and_struct():
    src = (
        "struct Foo { int a; };\n"
        "int one() { return 1; }\n"
        "int two() { return one(); }\n"
    )
    root = _parse(src)
    by_qn = {e.qualified_name: e.kind for e, _ in extract_c_entities(root, "x.c")}
    assert by_qn == {
        "x.c::Foo": "struct",
        "x.c::one": "function",
        "x.c::two": "function",
    }
