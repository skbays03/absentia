"""Verify PHP AST → Entity + FeatureSet extraction."""
from tree_sitter import Language, Parser
import tree_sitter_php

from absentia.extractors.php import extract_php_entities

PHP = Language(tree_sitter_php.language_php())


def _parse(source: str):
    return Parser(PHP).parse(source.encode()).root_node


def test_extracts_top_level_function():
    src = "<?php\nfunction helper(int $x): int { return $x * 2; }\n"
    root = _parse(src)
    [(entity, _)] = list(extract_php_entities(root, "x.php"))
    assert entity.kind == "function"
    assert entity.qualified_name == "x.php::helper"


def test_class_with_extends_and_implements():
    src = (
        "<?php\n"
        "class Cat extends Animal implements Greetable, Trainable {\n"
        "    public function meow(): void {}\n"
        "}\n"
    )
    root = _parse(src)
    by_qn = {e.qualified_name: (e, f) for e, f in extract_php_entities(root, "x.php")}
    cls_e, cls_f = by_qn["x.php::Cat"]
    assert cls_e.kind == "class"
    assert cls_f.get_set("parent_class") == frozenset(
        {"Animal", "Greetable", "Trainable"}
    )


def test_php8_attributes_become_decorators():
    src = (
        "<?php\n"
        "#[Route('/api/users')]\n"
        "#[ApiController]\n"
        "class UserController {}\n"
    )
    root = _parse(src)
    [(_, features)] = list(extract_php_entities(root, "x.php"))
    assert features.get_set("decorator") == frozenset(
        {"#[Route]", "#[ApiController]"}
    )


def test_method_attributes_per_method():
    src = (
        "<?php\n"
        "class C {\n"
        "    #[Inject]\n"
        "    public function __construct() {}\n"
        "    public function plain() {}\n"
        "}\n"
    )
    root = _parse(src)
    by_qn = {e.qualified_name: f for e, f in extract_php_entities(root, "x.php")}
    assert by_qn["x.php::C.__construct"].get_set("decorator") == frozenset({"#[Inject]"})
    assert by_qn["x.php::C.plain"].get_set("decorator") == frozenset()


def test_interface_and_trait_extracted():
    src = (
        "<?php\n"
        "interface IFoo { public function find(int $id): User; }\n"
        "trait Loggable { public function log() {} }\n"
    )
    root = _parse(src)
    by_qn = {e.qualified_name: e.kind for e, _ in extract_php_entities(root, "x.php")}
    assert by_qn["x.php::IFoo"] == "interface"
    assert by_qn["x.php::Loggable"] == "trait"


def test_calls_cover_member_scoped_and_constructors():
    src = (
        "<?php\n"
        "function go() {\n"
        "    foo();\n"
        "    Bar::baz();\n"
        "    $obj->method();\n"
        "    new Logger();\n"
        "}\n"
    )
    root = _parse(src)
    [(_, features)] = list(extract_php_entities(root, "x.php"))
    calls = features.get_set("calls")
    assert "foo" in calls
    assert "Bar.baz" in calls
    assert "method" in calls
    assert "new Logger" in calls
