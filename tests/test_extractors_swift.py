"""Verify Swift AST → Entity + FeatureSet extraction."""
from tree_sitter import Language, Parser
import tree_sitter_swift

from absentia.extractors.swift import extract_swift_entities

SW = Language(tree_sitter_swift.language())


def _parse(source: str):
    return Parser(SW).parse(source.encode()).root_node


def test_extracts_top_level_function():
    src = "func greet() { print(\"hi\") }\n"
    root = _parse(src)
    [(entity, features)] = list(extract_swift_entities(root, "x.swift"))
    assert entity.kind == "function"
    assert entity.qualified_name == "x.swift::greet"
    assert features.get_set("calls") == frozenset({"print"})


def test_extracts_function_with_attribute():
    src = "@MainActor func work() { helper() }\n"
    root = _parse(src)
    [(_, features)] = list(extract_swift_entities(root, "x.swift"))
    assert features.get_set("decorator") == frozenset({"@MainActor"})
    assert features.get_set("calls") == frozenset({"helper"})


def test_attribute_with_args_drops_suffix():
    src = "@available(macOS 11.0, *)\nfunc qux() {}\n"
    root = _parse(src)
    [(_, features)] = list(extract_swift_entities(root, "x.swift"))
    assert features.get_set("decorator") == frozenset({"@available"})


def test_extracts_class_with_inheritance():
    src = "class Cat: Animal, Mascot {}\n"
    root = _parse(src)
    [(entity, features)] = list(extract_swift_entities(root, "x.swift"))
    assert entity.kind == "class"
    assert entity.qualified_name == "x.swift::Cat"
    assert features.get_set("parent_class") == frozenset({"Animal", "Mascot"})


def test_extracts_struct_as_struct_kind():
    src = "struct Point { var x: Int }\n"
    root = _parse(src)
    [(entity, _)] = list(extract_swift_entities(root, "x.swift"))
    assert entity.kind == "struct"
    assert entity.qualified_name == "x.swift::Point"


def test_extracts_protocol_as_protocol_kind():
    src = "protocol Greeter { func greet() }\n"
    root = _parse(src)
    [(entity, _)] = list(extract_swift_entities(root, "x.swift"))
    assert entity.kind == "protocol"


def test_extracts_extension_as_extension_kind():
    src = "extension Foo: Equatable {}\n"
    root = _parse(src)
    [(entity, features)] = list(extract_swift_entities(root, "x.swift"))
    assert entity.kind == "extension"
    assert entity.qualified_name == "x.swift::Foo"
    assert features.get_set("parent_class") == frozenset({"Equatable"})


def test_methods_inside_class_get_qualified_name():
    src = (
        "class Cat: Animal {\n"
        "    @objc func meow() { speak(\"meow\") }\n"
        "    func sleep() {}\n"
        "}\n"
    )
    root = _parse(src)
    by_qn = {
        e.qualified_name: (e, f)
        for e, f in extract_swift_entities(root, "x.swift")
    }
    assert "x.swift::Cat" in by_qn
    assert "x.swift::Cat.meow" in by_qn
    assert "x.swift::Cat.sleep" in by_qn

    meow_entity, meow_features = by_qn["x.swift::Cat.meow"]
    assert meow_entity.kind == "method"
    assert meow_features.get_set("decorator") == frozenset({"@objc"})
    assert meow_features.get_set("calls") == frozenset({"speak"})


def test_call_with_navigation_expression_keeps_dotted_form():
    src = (
        "func work() {\n"
        "    self.update()\n"
        "    Logger.shared.log()\n"
        "    bar()\n"
        "}\n"
    )
    root = _parse(src)
    [(_, features)] = list(extract_swift_entities(root, "x.swift"))
    calls = features.get_set("calls")
    assert "bar" in calls
    assert "self.update" in calls
    assert "Logger.shared.log" in calls
