"""Verify Lua AST → Entity + FeatureSet extraction."""
from tree_sitter import Language, Parser
import tree_sitter_lua

from absentia.extractors.lua import extract_lua_entities

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


def test_table_assignment_function_extracted_as_method():
    """`M.foo = function() end` — common module-of-functions pattern.
    Pre-fix the extractor missed these entirely."""
    src = (
        "local M = {}\n"
        "M.greet = function(name)\n"
        "  return name\n"
        "end\n"
        "return M\n"
    )
    root = _parse(src)
    items = list(extract_lua_entities(root, "x.lua"))
    by_qn = {e.qualified_name: e.kind for e, _ in items}
    assert "x.lua::M.greet" in by_qn
    assert by_qn["x.lua::M.greet"] == "method"


def test_local_assignment_function_extracted_as_function():
    """`local foo = function() end` — local-bound function expression.
    Pre-fix the extractor missed these too."""
    src = "local foo = function() return 1 end\n"
    root = _parse(src)
    [(entity, _)] = list(extract_lua_entities(root, "x.lua"))
    assert entity.kind == "function"
    assert entity.qualified_name == "x.lua::foo"


def test_table_assigned_function_captures_calls():
    """The new table-assignment path must walk the function body
    for calls just like the function-declaration path does."""
    src = (
        "local M = {}\n"
        "M.process = function(items)\n"
        "  helper()\n"
        "  return list.map(items)\n"
        "end\n"
    )
    root = _parse(src)
    [(_, features)] = list(extract_lua_entities(root, "x.lua"))
    calls = features.get_set("calls")
    assert "helper" in calls
    assert "list.map" in calls


def test_non_function_assignment_skipped():
    """`local M = {}` shouldn't yield an entity — only assignments
    whose RHS is a function definition produce entities."""
    src = "local M = {}\nlocal x = 42\n"
    root = _parse(src)
    items = list(extract_lua_entities(root, "x.lua"))
    assert items == []
