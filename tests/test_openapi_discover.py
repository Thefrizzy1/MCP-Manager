"""OpenAPI spec parsing (offline — no network)."""
from core.openapi_discover import parse_spec, _candidates, SPEC_PATHS

SAMPLE = {
    "openapi": "3.0.2",
    "info": {"title": "Widget API", "version": "1.4.0", "description": "Does widgets."},
    "servers": [{"url": "http://widgets.local:9000"}],
    "paths": {
        "/widgets": {
            "get": {"summary": "List widgets", "operationId": "list_widgets",
                    "parameters": [{"name": "limit", "in": "query"}]},
            "post": {"summary": "Create widget", "requestBody": {"content": {}}},
        },
        "/widgets/{id}": {
            "get": {"operationId": "get_widget",
                    "parameters": [{"name": "id", "in": "path"}]},
            "delete": {"summary": "Delete"},
        },
        "/health": {"options": {"summary": "ignored method"}},  # non-CRUD -> skipped
    },
}


def test_parse_spec_basics():
    out = parse_spec(SAMPLE)
    assert out["title"] == "Widget API"
    assert out["version"] == "1.4.0"
    assert out["server"] == "http://widgets.local:9000"
    # 4 real operations (GET/POST /widgets, GET/DELETE /widgets/{id}); OPTIONS skipped
    assert out["operation_count"] == 4
    methods = {(o["method"], o["path"]) for o in out["operations"]}
    assert ("GET", "/widgets") in methods
    assert ("DELETE", "/widgets/{id}") in methods
    assert not any(o["method"] == "OPTIONS" for o in out["operations"])


def test_parse_spec_params_and_body():
    out = parse_spec(SAMPLE)
    by = {(o["method"], o["path"]): o for o in out["operations"]}
    assert by[("GET", "/widgets")]["params"] == ["limit"]
    assert by[("POST", "/widgets")]["has_body"] is True
    assert by[("GET", "/widgets/{id}")]["operation_id"] == "get_widget"


def test_parse_spec_empty_is_safe():
    out = parse_spec({})
    assert out["operation_count"] == 0
    assert out["operations"] == []


def test_candidates():
    # a plain host expands to the common spec paths
    c = _candidates("http://h:8000")
    assert c == ["http://h:8000" + p for p in SPEC_PATHS]
    # an explicit spec file is used as-is
    assert _candidates("http://h/openapi.json") == ["http://h/openapi.json"]
