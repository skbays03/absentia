"""Verify TypeScript AST → Entity + FeatureSet extraction."""
from tree_sitter import Language, Parser
import tree_sitter_typescript

from lacuna.extractors.typescript import extract_typescript_entities

TS = Language(tree_sitter_typescript.language_typescript())


def _parse(source: str):
    return Parser(TS).parse(source.encode()).root_node


def test_extracts_typed_function_declaration():
    src = "function helper(x: number): number { return x * 2; }\n"
    root = _parse(src)
    [(entity, features)] = list(extract_typescript_entities(root, "x.ts"))
    assert entity.kind == "function"
    assert entity.qualified_name == "x.ts::helper"
    assert features.get_set("decorator") == frozenset()


def test_extracts_arrow_function_with_type_annotation():
    src = "const greet: () => string = () => world();\n"
    root = _parse(src)
    [(entity, features)] = list(extract_typescript_entities(root, "x.ts"))
    assert entity.qualified_name == "x.ts::greet"
    assert features.get_set("calls") == frozenset({"world"})


def test_decorated_class_collects_decorators():
    src = (
        "@injectable()\n"
        "class UserService {\n"
        "  find(id: number) { return repo.get(id); }\n"
        "}\n"
    )
    root = _parse(src)
    by_qn = {
        e.qualified_name: (e, f)
        for e, f in extract_typescript_entities(root, "x.ts")
    }
    cls_entity, cls_features = by_qn["x.ts::UserService"]
    assert cls_entity.kind == "class"
    assert cls_features.get_set("decorator") == frozenset({"@injectable"})


def test_method_decorators_collected_per_method():
    src = (
        "class C {\n"
        "  @log\n"
        "  @cached\n"
        "  greet() { hello(); }\n"
        "  plain() {}\n"
        "}\n"
    )
    root = _parse(src)
    by_qn = {
        e.qualified_name: (e, f)
        for e, f in extract_typescript_entities(root, "x.ts")
    }
    greet_entity, greet_features = by_qn["x.ts::C.greet"]
    assert greet_entity.kind == "method"
    assert greet_features.get_set("decorator") == frozenset({"@log", "@cached"})

    plain_entity, plain_features = by_qn["x.ts::C.plain"]
    assert plain_features.get_set("decorator") == frozenset()


def test_class_extends_and_implements_both_become_parent_class():
    src = "class Cat extends Animal implements Mascot, Trainable {}\n"
    root = _parse(src)
    [(_, features)] = list(extract_typescript_entities(root, "x.ts"))
    assert features.get_set("parent_class") == frozenset(
        {"Animal", "Mascot", "Trainable"}
    )


def test_interface_emitted_as_interface_kind():
    src = "interface IService { findUser(id: number): User; }\n"
    root = _parse(src)
    [(entity, _)] = list(extract_typescript_entities(root, "x.ts"))
    assert entity.kind == "interface"
    assert entity.qualified_name == "x.ts::IService"


def test_interface_extends_other_interfaces():
    src = "interface IAdmin extends IUser, ICredentialed {}\n"
    root = _parse(src)
    [(_, features)] = list(extract_typescript_entities(root, "x.ts"))
    assert features.get_set("parent_class") == frozenset(
        {"IUser", "ICredentialed"}
    )


def test_export_statement_with_decorated_class():
    src = (
        "@Component({selector: 'foo'})\n"
        "export class FooComponent {\n"
        "  ngOnInit() { init(); }\n"
        "}\n"
    )
    root = _parse(src)
    by_qn = {
        e.qualified_name: (e, f)
        for e, f in extract_typescript_entities(root, "x.ts")
    }
    cls_entity, cls_features = by_qn["x.ts::FooComponent"]
    assert cls_features.get_set("decorator") == frozenset({"@Component"})
