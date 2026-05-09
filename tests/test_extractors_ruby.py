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


def test_classes_nested_inside_module_are_extracted():
    """The Sinatra-style pattern — a top-level module containing
    classes that contain methods. Pre-fix, the extractor only
    emitted the outer module and missed everything inside it,
    losing 99% of entities on a typical lib/<gem>/base.rb file."""
    src = (
        "module Sinatra\n"
        "  class Request\n"
        "    def accept; end\n"
        "    def safe?; end\n"
        "  end\n"
        "  class Response\n"
        "    def status; end\n"
        "  end\n"
        "end\n"
    )
    root = _parse(src)
    by_qn = {e.qualified_name: e.kind for e, _ in extract_ruby_entities(root, "x.rb")}
    assert by_qn["x.rb::Sinatra"] == "module"
    assert by_qn["x.rb::Request"] == "class"
    assert by_qn["x.rb::Response"] == "class"
    assert by_qn["x.rb::Request.accept"] == "method"
    assert by_qn["x.rb::Request.safe?"] == "method"
    assert by_qn["x.rb::Response.status"] == "method"


def test_modules_nested_inside_module_are_extracted():
    """Two module levels deep — both should surface, plus the
    method inside the inner module."""
    src = (
        "module Outer\n"
        "  module Inner\n"
        "    def foo; end\n"
        "  end\n"
        "end\n"
    )
    root = _parse(src)
    by_qn = {e.qualified_name: e.kind for e, _ in extract_ruby_entities(root, "x.rb")}
    assert by_qn["x.rb::Outer"] == "module"
    assert by_qn["x.rb::Inner"] == "module"
    assert by_qn["x.rb::Inner.foo"] == "method"


def test_singleton_methods_emitted():
    """`def self.create` is a class method — should be extracted same
    as an instance method (not skipped). The qualified_name shape
    isn't the test target; visibility-to-mining is."""
    src = (
        "class Foo\n"
        "  def self.create; end\n"
        "  def name; end\n"
        "end\n"
    )
    root = _parse(src)
    qns = [e.qualified_name for e, _ in extract_ruby_entities(root, "x.rb")]
    # Both methods must surface; the file must yield at least 3
    # entities (class Foo + 2 methods).
    assert len(qns) >= 3
    assert any("create" in qn for qn in qns)
    assert any("name" in qn for qn in qns)
