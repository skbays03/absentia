"""Verify Kotlin AST → Entity + FeatureSet extraction."""
from tree_sitter import Language, Parser
import tree_sitter_kotlin

from absentia.extractors.kotlin import extract_kotlin_entities

KT = Language(tree_sitter_kotlin.language())


def _parse(source: str):
    return Parser(KT).parse(source.encode()).root_node


def test_extracts_top_level_function():
    src = "fun standalone() { helper() }\n"
    root = _parse(src)
    [(entity, features)] = list(extract_kotlin_entities(root, "x.kt"))
    assert entity.kind == "function"
    assert entity.qualified_name == "x.kt::standalone"
    assert "helper" in features.get_set("calls")


def test_class_with_annotation_and_delegation():
    src = (
        "@Composable\n"
        "class UserScreen : BaseScreen(), Greetable {\n"
        "    fun render() { renderHelper() }\n"
        "}\n"
    )
    root = _parse(src)
    by_qn = {e.qualified_name: (e, f) for e, f in extract_kotlin_entities(root, "x.kt")}
    cls_e, cls_f = by_qn["x.kt::UserScreen"]
    assert cls_e.kind == "class"
    assert cls_f.get_set("decorator") == frozenset({"@Composable"})
    assert cls_f.get_set("parent_class") == frozenset({"BaseScreen", "Greetable"})

    method_e, _ = by_qn["x.kt::UserScreen.render"]
    assert method_e.kind == "method"


def test_data_class_kind():
    src = "data class Person(val name: String)\n"
    root = _parse(src)
    [(entity, _)] = list(extract_kotlin_entities(root, "x.kt"))
    assert entity.kind == "data_class"


def test_interface_kind():
    src = "interface IService { fun find(): User }\n"
    root = _parse(src)
    by_qn = {e.qualified_name: e.kind for e, _ in extract_kotlin_entities(root, "x.kt")}
    assert by_qn["x.kt::IService"] == "interface"


def test_method_annotations_per_method():
    src = (
        "class C {\n"
        "    @Test\n"
        "    fun runTest() {}\n"
        "    fun plain() {}\n"
        "}\n"
    )
    root = _parse(src)
    by_qn = {e.qualified_name: f for e, f in extract_kotlin_entities(root, "x.kt")}
    assert by_qn["x.kt::C.runTest"].get_set("decorator") == frozenset({"@Test"})
    assert by_qn["x.kt::C.plain"].get_set("decorator") == frozenset()
