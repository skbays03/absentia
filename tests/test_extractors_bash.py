"""Verify Bash AST → Entity + FeatureSet extraction."""
from tree_sitter import Language, Parser
import tree_sitter_bash

from absentia.extractors.bash import extract_bash_entities

BASH = Language(tree_sitter_bash.language())


def _parse(source: str):
    return Parser(BASH).parse(source.encode()).root_node


def test_extracts_function_with_keyword_form():
    src = (
        "function deploy() {\n"
        "    echo \"deploying\"\n"
        "    helper\n"
        "}\n"
    )
    root = _parse(src)
    [(entity, features)] = list(extract_bash_entities(root, "x.sh"))
    assert entity.kind == "function"
    assert entity.qualified_name == "x.sh::deploy"
    calls = features.get_set("calls")
    assert "echo" in calls
    assert "helper" in calls


def test_extracts_function_with_bare_form():
    src = (
        "build() {\n"
        "    make all\n"
        "}\n"
    )
    root = _parse(src)
    [(entity, features)] = list(extract_bash_entities(root, "x.sh"))
    assert entity.qualified_name == "x.sh::build"
    assert "make" in features.get_set("calls")


def test_multiple_functions():
    src = (
        "deploy() { make all; }\n"
        "test() { pytest; }\n"
        "clean() { rm -rf build; }\n"
    )
    root = _parse(src)
    by_qn = {e.qualified_name: e.kind for e, _ in extract_bash_entities(root, "x.sh")}
    assert by_qn == {
        "x.sh::deploy": "function",
        "x.sh::test": "function",
        "x.sh::clean": "function",
    }
