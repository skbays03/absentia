"""Verify C++ AST → Entity + FeatureSet extraction."""
from tree_sitter import Language, Parser
import tree_sitter_cpp

from lacuna.extractors.cpp import extract_cpp_entities

CPP = Language(tree_sitter_cpp.language())


def _parse(source: str):
    return Parser(CPP).parse(source.encode()).root_node


def test_extracts_top_level_function():
    src = "int add(int a, int b) { return helper(a) + b; }\n"
    root = _parse(src)
    [(entity, features)] = list(extract_cpp_entities(root, "x.cpp"))
    assert entity.kind == "function"
    assert entity.qualified_name == "x.cpp::add"
    assert "helper" in features.get_set("calls")


def test_extracts_class_with_inheritance():
    src = (
        "class Animal { public: virtual void speak(); };\n"
        "class Cat : public Animal {\n"
        "public:\n"
        "    void speak() { meow(); }\n"
        "};\n"
    )
    root = _parse(src)
    by_qn = {e.qualified_name: (e, f) for e, f in extract_cpp_entities(root, "x.cpp")}
    cat_entity, cat_features = by_qn["x.cpp::Cat"]
    assert cat_entity.kind == "class"
    assert cat_features.get_set("parent_class") == frozenset({"Animal"})

    speak_e, speak_f = by_qn["x.cpp::Cat.speak"]
    assert speak_e.kind == "method"
    assert "meow" in speak_f.get_set("calls")


def test_extracts_struct_as_struct_kind():
    src = "struct Point { int x; int y; };\n"
    root = _parse(src)
    [(entity, _)] = list(extract_cpp_entities(root, "x.cpp"))
    assert entity.kind == "struct"


def test_template_function_extracted():
    src = "template<typename T> T add(T a, T b) { return a + b; }\n"
    root = _parse(src)
    [(entity, _)] = list(extract_cpp_entities(root, "x.cpp"))
    assert entity.kind == "function"
    assert entity.qualified_name == "x.cpp::add"


def test_namespace_recurses_into_contents():
    src = "namespace foo { void bar() { helper(); } }\n"
    root = _parse(src)
    [(entity, features)] = list(extract_cpp_entities(root, "x.cpp"))
    assert entity.qualified_name == "x.cpp::bar"
    assert "helper" in features.get_set("calls")


def test_multiple_inheritance():
    src = "class C : public A, public B {};\n"
    root = _parse(src)
    [(_, features)] = list(extract_cpp_entities(root, "x.cpp"))
    assert features.get_set("parent_class") == frozenset({"A", "B"})
