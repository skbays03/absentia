"""Verify Lua AST → Entity + FeatureSet extraction."""
from tree_sitter import Language, Parser
import tree_sitter_lua

from lacuna.extractors.lua import extract_lua_entities

LUA = Language(tree_sitter_lua.language())


def _parse(source: str):
    return Parser(LUA).parse(source.encode()).root_node


def test_extracts_top_level_function():
    src = "function helper()\n  print('hi')\nend\n"
    root = _parse(src)
    [(entity, features)] = list(extract_lua_entities(root, "x.lua"))
    assert entity.kind == "function"
    assert entity.qualified_name == "x.lua::helper"
    assert "print" in features.get_set("calls")


def test_extracts_dot_function():
    src = "function M.greet(name)\n  do_thing(name)\nend\n"
    root = _parse(src)
    [(entity, features)] = list(extract_lua_entities(root, "x.lua"))
    assert entity.kind == "function"
    assert entity.qualified_name == "x.lua::M.greet"
    assert "do_thing" in features.get_set("calls")


def test_method_form_extracted_as_method():
    src = "function M:render()\nend\n"
    root = _parse(src)
    [(entity, _)] = list(extract_lua_entities(root, "x.lua"))
    assert entity.kind == "method"
    assert entity.qualified_name == "x.lua::M.render"


def test_calls_with_dotted_receiver():
    src = (
        "function go()\n"
        "  helper()\n"
        "  M.foo()\n"
        "end\n"
    )
    root = _parse(src)
    [(_, features)] = list(extract_lua_entities(root, "x.lua"))
    calls = features.get_set("calls")
    assert "helper" in calls
    assert "M.foo" in calls
