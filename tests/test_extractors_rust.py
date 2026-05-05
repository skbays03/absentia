"""Verify Rust AST → Entity + FeatureSet extraction."""
from tree_sitter import Language, Parser
import tree_sitter_rust

from lacuna.extractors.rust import extract_rust_entities

RS = Language(tree_sitter_rust.language())


def _parse(source: str):
    return Parser(RS).parse(source.encode()).root_node


def test_extracts_top_level_function():
    src = "fn standalone() { println!(\"hi\"); }\n"
    root = _parse(src)
    [(entity, features)] = list(extract_rust_entities(root, "x.rs"))
    assert entity.kind == "function"
    assert entity.qualified_name == "x.rs::standalone"
    assert "println!" in features.get_set("calls")


def test_extracts_struct():
    src = "pub struct Foo { name: String }\n"
    root = _parse(src)
    [(entity, _)] = list(extract_rust_entities(root, "x.rs"))
    assert entity.kind == "struct"
    assert entity.qualified_name == "x.rs::Foo"


def test_struct_with_derive_attribute():
    src = "#[derive(Debug, Clone)]\npub struct Foo {}\n"
    root = _parse(src)
    [(entity, features)] = list(extract_rust_entities(root, "x.rs"))
    assert entity.kind == "struct"
    assert features.get_set("decorator") == frozenset({"#[derive]"})


def test_attributes_dropped_for_args():
    src = "#[serde(rename = \"foo\")]\npub struct Foo {}\n"
    root = _parse(src)
    [(_, features)] = list(extract_rust_entities(root, "x.rs"))
    assert features.get_set("decorator") == frozenset({"#[serde]"})


def test_multiple_attributes_accumulate():
    src = "#[derive(Debug)]\n#[cfg(test)]\nstruct Foo {}\n"
    root = _parse(src)
    [(_, features)] = list(extract_rust_entities(root, "x.rs"))
    assert features.get_set("decorator") == frozenset(
        {"#[derive]", "#[cfg]"}
    )


def test_intrinsic_impl_emits_impl_entity_with_methods():
    src = (
        "struct Foo;\n"
        "impl Foo {\n"
        "    pub fn new() -> Self { Self }\n"
        "    fn helper(&self) {}\n"
        "}\n"
    )
    root = _parse(src)
    by_qn = {e.qualified_name: (e, f) for e, f in extract_rust_entities(root, "x.rs")}
    assert "x.rs::Foo (impl)" in by_qn
    assert by_qn["x.rs::Foo (impl)"][0].kind == "impl"
    assert "x.rs::Foo.new" in by_qn
    assert "x.rs::Foo.helper" in by_qn


def test_trait_impl_records_trait_as_parent_class():
    src = (
        "struct Foo;\n"
        "impl Display for Foo {\n"
        "    fn fmt(&self, f: &mut Formatter) -> Result<()> { write!(f, \"hi\") }\n"
        "}\n"
    )
    root = _parse(src)
    by_qn = {e.qualified_name: (e, f) for e, f in extract_rust_entities(root, "x.rs")}
    impl_entity, impl_features = by_qn["x.rs::Foo (impl Display)"]
    assert impl_entity.kind == "impl"
    assert impl_features.get_set("parent_class") == frozenset({"Display"})

    # Method qualified by the impl target, not the trait.
    assert "x.rs::Foo.fmt" in by_qn
    assert by_qn["x.rs::Foo.fmt"][0].kind == "method"


def test_trait_declaration_emitted():
    src = "trait Greet { fn greet(&self); }\n"
    root = _parse(src)
    by_qn = {e.qualified_name: (e, f) for e, f in extract_rust_entities(root, "x.rs")}
    assert "x.rs::Greet" in by_qn
    assert by_qn["x.rs::Greet"][0].kind == "trait"


def test_function_calls_extracted_with_macros():
    src = (
        "fn work() {\n"
        "    let v = vec![1, 2];\n"
        "    helper(v);\n"
        "    println!(\"done\");\n"
        "}\n"
    )
    root = _parse(src)
    [(_, features)] = list(extract_rust_entities(root, "x.rs"))
    calls = features.get_set("calls")
    assert "helper" in calls
    assert "vec!" in calls
    assert "println!" in calls
