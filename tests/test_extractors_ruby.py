"""Verify Ruby AST → Entity + FeatureSet extraction."""
from tree_sitter import Language, Parser
import tree_sitter_ruby

from absentia.extractors.ruby import extract_ruby_entities

RUBY = Language(tree_sitter_ruby.language())


def _parse(source: str):
    return Parser(RUBY).parse(source.encode()).root_node


def test_extracts_class_with_superclass():
    src = "class Cat < Animal\nend\n"
    root = _parse(src)
    [(entity, features)] = list(extract_ruby_entities(root, "x.rb"))
    assert entity.kind == "class"
    assert entity.qualified_name == "x.rb::Cat"
    assert features.get_set("parent_class") == frozenset({"Animal"})


def test_class_with_no_superclass_has_empty_parent_class():
    src = "class Cat\nend\n"
    root = _parse(src)
    [(_, features)] = list(extract_ruby_entities(root, "x.rb"))
    assert features.get_set("parent_class") == frozenset()


def test_module_extracted_as_module_kind():
    src = "module Greetable\nend\n"
    root = _parse(src)
    [(entity, _)] = list(extract_ruby_entities(root, "x.rb"))
    assert entity.kind == "module"
    assert entity.qualified_name == "x.rb::Greetable"


def test_include_and_prepend_become_parent_class():
    src = (
        "class Cat < Animal\n"
        "  include Greetable\n"
        "  prepend Loggable\n"
        "  extend ClassMethods\n"
        "end\n"
    )
    root = _parse(src)
    [(_, features)] = list(extract_ruby_entities(root, "x.rb"))
    assert features.get_set("parent_class") == frozenset(
        {"Animal", "Greetable", "Loggable", "ClassMethods"}
    )


def test_methods_inside_class_qualified_with_class_name():
    src = (
        "class Cat\n"
        "  def greet\n"
        "    helper\n"
        "  end\n"
        "  def initialize(name)\n"
        "    @name = name\n"
        "  end\n"
        "end\n"
    )
    root = _parse(src)
    by_qn = {e.qualified_name: e for e, _ in extract_ruby_entities(root, "x.rb")}
    assert "x.rb::Cat" in by_qn
    assert "x.rb::Cat.greet" in by_qn
    assert "x.rb::Cat.initialize" in by_qn
    assert by_qn["x.rb::Cat.greet"].kind == "method"


def test_calls_include_dotted_receivers():
    src = (
        "class C\n"
        "  def run\n"
        "    helper(@name)\n"
        "    self.update\n"
        "    Logger.info(\"hi\")\n"
        "  end\n"
        "end\n"
    )
    root = _parse(src)
    by_qn = {e.qualified_name: f for e, f in extract_ruby_entities(root, "x.rb")}
    calls = by_qn["x.rb::C.run"].get_set("calls")
    assert "helper" in calls
    assert "self.update" in calls
    assert "Logger.info" in calls


def test_module_methods_extracted():
    src = (
        "module Greetable\n"
        "  def hello\n"
        "    puts(\"hi\")\n"
        "  end\n"
        "end\n"
    )
    root = _parse(src)
    by_qn = {e.qualified_name: e.kind for e, _ in extract_ruby_entities(root, "x.rb")}
    assert by_qn["x.rb::Greetable"] == "module"
    assert by_qn["x.rb::Greetable.hello"] == "method"
