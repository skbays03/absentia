"""Verify Scala AST → Entity + FeatureSet extraction."""
from tree_sitter import Language, Parser
import tree_sitter_scala

from lacuna.extractors.scala import extract_scala_entities

SC = Language(tree_sitter_scala.language())


def _parse(source: str):
    return Parser(SC).parse(source.encode()).root_node


def test_extracts_class_with_extends_with():
    src = "class Cat extends Animal with Greetable {}\n"
    root = _parse(src)
    [(entity, features)] = list(extract_scala_entities(root, "x.scala"))
    assert entity.kind == "class"
    assert entity.qualified_name == "x.scala::Cat"
    assert features.get_set("parent_class") == frozenset({"Animal", "Greetable"})


def test_extracts_trait_and_object():
    src = (
        "trait Greeter {}\n"
        "object Foo {}\n"
    )
    root = _parse(src)
    by_qn = {e.qualified_name: e.kind for e, _ in extract_scala_entities(root, "x.scala")}
    assert by_qn == {"x.scala::Greeter": "trait", "x.scala::Foo": "object"}


def test_class_with_annotation():
    src = (
        "@deprecated(\"use V2\", \"1.0\")\n"
        "class OldThing {}\n"
    )
    root = _parse(src)
    [(_, features)] = list(extract_scala_entities(root, "x.scala"))
    assert features.get_set("decorator") == frozenset({"@deprecated"})


def test_method_inside_class_body():
    src = (
        "class Cat {\n"
        "  def greet: String = helper()\n"
        "}\n"
    )
    root = _parse(src)
    by_qn = {e.qualified_name: (e, f) for e, f in extract_scala_entities(root, "x.scala")}
    method_e, method_f = by_qn["x.scala::Cat.greet"]
    assert method_e.kind == "method"
    assert "helper" in method_f.get_set("calls")
