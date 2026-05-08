"""Verify Go AST → Entity + FeatureSet extraction."""
from tree_sitter import Language, Parser
import tree_sitter_go

from absentia.extractors.go import extract_go_entities

GO = Language(tree_sitter_go.language())


def _parse(source: str):
    return Parser(GO).parse(source.encode()).root_node


def test_extracts_top_level_function():
    src = (
        "package main\n"
        "func standalone() {\n"
        "    helper()\n"
        "}\n"
    )
    root = _parse(src)
    by_qn = {e.qualified_name: (e, f) for e, f in extract_go_entities(root, "x.go")}
    assert "x.go::standalone" in by_qn
    e, f = by_qn["x.go::standalone"]
    assert e.kind == "function"
    assert "helper" in f.get_set("calls")


def test_extracts_method_with_pointer_receiver():
    src = (
        "package main\n"
        "type Person struct { Name string }\n"
        "func (p *Person) Greet() string {\n"
        "    return fmt.Sprintf(\"hi %s\", p.Name)\n"
        "}\n"
    )
    root = _parse(src)
    by_qn = {e.qualified_name: (e, f) for e, f in extract_go_entities(root, "x.go")}
    assert "x.go::Person.Greet" in by_qn
    e, f = by_qn["x.go::Person.Greet"]
    assert e.kind == "method"
    assert "fmt.Sprintf" in f.get_set("calls")


def test_extracts_method_with_value_receiver():
    src = (
        "package main\n"
        "type Foo struct{}\n"
        "func (f Foo) Bar() {}\n"
    )
    root = _parse(src)
    by_qn = {e.qualified_name: e for e, _ in extract_go_entities(root, "x.go")}
    assert "x.go::Foo.Bar" in by_qn
    assert by_qn["x.go::Foo.Bar"].kind == "method"


def test_extracts_struct_as_struct_kind():
    src = "package main\ntype Person struct { Name string }\n"
    root = _parse(src)
    [(entity, _)] = list(extract_go_entities(root, "x.go"))
    assert entity.kind == "struct"
    assert entity.qualified_name == "x.go::Person"


def test_extracts_interface_as_interface_kind():
    src = (
        "package main\n"
        "type Greeter interface {\n"
        "    Greet() string\n"
        "}\n"
    )
    root = _parse(src)
    [(entity, _)] = list(extract_go_entities(root, "x.go"))
    assert entity.kind == "interface"
    assert entity.qualified_name == "x.go::Greeter"


def test_skips_type_alias_and_other_type_specs():
    src = (
        "package main\n"
        "type ID int\n"
    )
    root = _parse(src)
    # Type aliases are not interesting for MVP — we skip them.
    assert list(extract_go_entities(root, "x.go")) == []


def test_handles_grouped_type_declaration():
    src = (
        "package main\n"
        "type (\n"
        "    Foo struct{}\n"
        "    Bar interface{}\n"
        ")\n"
    )
    root = _parse(src)
    by_qn = {e.qualified_name: e.kind for e, _ in extract_go_entities(root, "x.go")}
    assert by_qn == {"x.go::Foo": "struct", "x.go::Bar": "interface"}
