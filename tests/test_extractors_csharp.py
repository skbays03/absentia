"""Verify C# AST → Entity + FeatureSet extraction."""
from tree_sitter import Language, Parser
import tree_sitter_c_sharp

from absentia.extractors.csharp import extract_csharp_entities

CS = Language(tree_sitter_c_sharp.language())


def _parse(source: str):
    return Parser(CS).parse(source.encode()).root_node


def test_extracts_class_with_base_list():
    src = "public class Cat : Animal, IGreetable {}\n"
    root = _parse(src)
    [(entity, features)] = list(extract_csharp_entities(root, "x.cs"))
    assert entity.kind == "class"
    assert entity.qualified_name == "x.cs::Cat"
    assert features.get_set("parent_class") == frozenset({"Animal", "IGreetable"})


def test_attributes_become_decorators():
    src = (
        "[Serializable]\n"
        "[Obsolete(\"use V2\")]\n"
        "public class Foo {}\n"
    )
    root = _parse(src)
    [(_, features)] = list(extract_csharp_entities(root, "x.cs"))
    assert features.get_set("decorator") == frozenset(
        {"[Serializable]", "[Obsolete]"}
    )


def test_method_attributes_per_method():
    src = (
        "public class C {\n"
        "    [Test]\n"
        "    public void TestSomething() { Helper(); }\n"
        "    [Obsolete]\n"
        "    public void Old() {}\n"
        "}\n"
    )
    root = _parse(src)
    by_qn = {e.qualified_name: (e, f) for e, f in extract_csharp_entities(root, "x.cs")}
    test_e, test_f = by_qn["x.cs::C.TestSomething"]
    assert test_e.kind == "method"
    assert test_f.get_set("decorator") == frozenset({"[Test]"})
    assert "Helper" in test_f.get_set("calls")

    old_e, old_f = by_qn["x.cs::C.Old"]
    assert old_f.get_set("decorator") == frozenset({"[Obsolete]"})


def test_interface_extracted_as_interface_kind():
    src = "public interface IService : IBase, IExtra {}\n"
    root = _parse(src)
    [(entity, features)] = list(extract_csharp_entities(root, "x.cs"))
    assert entity.kind == "interface"
    assert features.get_set("parent_class") == frozenset({"IBase", "IExtra"})


def test_struct_record_enum_get_their_own_kinds():
    src = (
        "public struct Point { public int X; }\n"
        "public record Person(string Name);\n"
        "public enum Color { Red, Green }\n"
    )
    root = _parse(src)
    by_qn = {e.qualified_name: e.kind for e, _ in extract_csharp_entities(root, "x.cs")}
    assert by_qn == {
        "x.cs::Point": "struct",
        "x.cs::Person": "record",
        "x.cs::Color": "enum",
    }


def test_class_inside_block_scoped_namespace_extracted():
    src = (
        "namespace Foo {\n"
        "    public class Bar {}\n"
        "    public interface IBaz {}\n"
        "}\n"
    )
    root = _parse(src)
    by_qn = {e.qualified_name: e.kind for e, _ in extract_csharp_entities(root, "x.cs")}
    assert by_qn == {"x.cs::Bar": "class", "x.cs::IBaz": "interface"}


def test_class_under_file_scoped_namespace_extracted():
    src = (
        "namespace Foo;\n"
        "public class Bar {}\n"
    )
    root = _parse(src)
    [(entity, _)] = list(extract_csharp_entities(root, "x.cs"))
    assert entity.qualified_name == "x.cs::Bar"


def test_method_calls_include_constructor_and_member_access():
    src = (
        "public class C {\n"
        "    public void Run() {\n"
        "        helper();\n"
        "        Math.Abs(-5);\n"
        "        var x = new Logger();\n"
        "    }\n"
        "}\n"
    )
    root = _parse(src)
    by_qn = {e.qualified_name: f for e, f in extract_csharp_entities(root, "x.cs")}
    calls = by_qn["x.cs::C.Run"].get_set("calls")
    assert "helper" in calls
    assert "Math.Abs" in calls
    assert "new Logger" in calls
