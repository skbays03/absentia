"""Verify Java AST → Entity + FeatureSet extraction."""
from tree_sitter import Language, Parser
import tree_sitter_java

from absentia.extractors.java import extract_java_entities

JAVA = Language(tree_sitter_java.language())


def _parse(source: str):
    return Parser(JAVA).parse(source.encode()).root_node


def test_extracts_class_with_extends_and_implements():
    src = (
        "public class Cat extends Animal implements Greetable, Trainable {\n"
        "    public String greet() { return \"meow\"; }\n"
        "}\n"
    )
    root = _parse(src)
    by_qn = {e.qualified_name: (e, f) for e, f in extract_java_entities(root, "x.java")}
    assert "x.java::Cat" in by_qn
    cls_entity, cls_features = by_qn["x.java::Cat"]
    assert cls_entity.kind == "class"
    assert cls_features.get_set("parent_class") == frozenset(
        {"Animal", "Greetable", "Trainable"}
    )


def test_class_annotations_become_decorators():
    src = (
        "@Deprecated\n"
        "@SuppressWarnings(\"unused\")\n"
        "public class Foo {}\n"
    )
    root = _parse(src)
    [(_, features)] = list(extract_java_entities(root, "x.java"))
    assert features.get_set("decorator") == frozenset(
        {"@Deprecated", "@SuppressWarnings"}
    )


def test_method_annotations_collected_per_method():
    src = (
        "public class C {\n"
        "    @Override\n"
        "    public String greet() { return helper(); }\n"
        "    @Test\n"
        "    private void testIt() {}\n"
        "}\n"
    )
    root = _parse(src)
    by_qn = {e.qualified_name: (e, f) for e, f in extract_java_entities(root, "x.java")}
    greet_entity, greet_features = by_qn["x.java::C.greet"]
    assert greet_entity.kind == "method"
    assert greet_features.get_set("decorator") == frozenset({"@Override"})
    assert "helper" in greet_features.get_set("calls")

    test_entity, test_features = by_qn["x.java::C.testIt"]
    assert test_features.get_set("decorator") == frozenset({"@Test"})


def test_interface_with_extends():
    src = "public interface IFoo extends IBase, IExtra {}\n"
    root = _parse(src)
    [(entity, features)] = list(extract_java_entities(root, "x.java"))
    assert entity.kind == "interface"
    assert features.get_set("parent_class") == frozenset({"IBase", "IExtra"})


def test_interface_methods_extracted():
    src = (
        "public interface IFoo {\n"
        "    User findUser(int id);\n"
        "    void delete(int id);\n"
        "}\n"
    )
    root = _parse(src)
    by_qn = {e.qualified_name: e for e, _ in extract_java_entities(root, "x.java")}
    assert "x.java::IFoo.findUser" in by_qn
    assert "x.java::IFoo.delete" in by_qn
    assert by_qn["x.java::IFoo.findUser"].kind == "method"


def test_enum_extracted_as_enum_kind():
    src = "public enum Color { RED, GREEN, BLUE }\n"
    root = _parse(src)
    [(entity, _)] = list(extract_java_entities(root, "x.java"))
    assert entity.kind == "enum"
    assert entity.qualified_name == "x.java::Color"


def test_method_calls_include_object_method_form():
    src = (
        "public class C {\n"
        "    public void run() {\n"
        "        helper();\n"
        "        Math.abs(-5);\n"
        "        new Logger().info(\"hi\");\n"
        "    }\n"
        "}\n"
    )
    root = _parse(src)
    by_qn = {e.qualified_name: f for e, f in extract_java_entities(root, "x.java")}
    calls = by_qn["x.java::C.run"].get_set("calls")
    assert "helper" in calls
    assert "Math.abs" in calls
    assert "new Logger" in calls
