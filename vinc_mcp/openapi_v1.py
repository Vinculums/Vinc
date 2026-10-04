"""Generate and serve the committed OpenAPI 3.1 contract from REST routes."""
from __future__ import annotations

import json
from pathlib import Path

from . import rest_routes, tools


FILE = Path(__file__).with_name("openapi_v1.json")


def generate() -> dict:
    specs = {item["name"]: item for item in tools.specs(allow_target=False)}
    paths: dict = {}
    for route in rest_routes.ROUTES:
        schema = specs[route.tool]["inputSchema"]
        props = schema.get("properties") or {}
        required = set(schema.get("required") or ())
        parameters = []
        for field, arg in route.path_args:
            parameters.append({"name": field, "in": "path", "required": True,
                               "schema": props.get(arg, {"type": "string"})})
        for name in route.query:
            parameters.append({"name": name, "in": "query", "required": name in required,
                               "schema": props.get(name, {"type": "string"})})
        parameters.append({"name": "space", "in": "query", "required": False,
                           "schema": {"type": "string", "default": "personal"}})
        operation = {"operationId": route.tool + "_" + route.method.lower() + "_" +
                     route.path.replace("/", "_").replace("{", "").replace("}", ""),
                     "summary": specs[route.tool]["description"].split("\n", 1)[0],
                     "security": [{"MemberKey": []}],
                     "parameters": parameters,
                     "responses": {
                         str(route.success): {"description": "Tool result",
                              "content": {"application/json": {"schema": {"type": "object",
                                                                   "additionalProperties": True}}}},
                         "default": {"$ref": "#/components/responses/Error"},
                     }}
        if route.tool == "vinc_doc_put" and route.method == "PUT":
            operation["responses"]["201"] = operation["responses"]["200"]
        if route.body:
            operation["requestBody"] = {"required": True, "content": {
                "application/json": {"schema": {
                    "type": "object",
                    "properties": {name: props.get(name, {"type": "string"}) for name in route.body},
                    "required": [name for name in route.body if name in required
                                 or (route.tool == "vinc_edges" and name == "nodes")],
                    "additionalProperties": False,
                }}}}
        paths.setdefault(route.path, {})[route.method.lower()] = operation
    paths["/v1/openapi.json"] = {"get": {"operationId": "openapi_v1",
        "summary": "OpenAPI 3.1 document", "security": [],
        "responses": {"200": {"description": "OpenAPI document"}}}}
    return {"openapi": "3.1.0", "info": {"title": "Vinc API", "version": "1.0.0"},
            "servers": [{"url": "https://mcp.vincs.io"}],
            "paths": paths,
            "components": {"securitySchemes": {"MemberKey": {
                "type": "http", "scheme": "bearer", "bearerFormat": "Vinc API key"}},
                "schemas": {"Error": {"type": "object", "required": ["error"],
                    "properties": {"error": {"type": "object",
                        "required": ["code", "message", "request_id", "details"],
                        "properties": {"code": {"type": "string"},
                                       "message": {"type": "string"},
                                       "request_id": {"type": "string"},
                                       "details": {"type": "object"}}}}}},
                "responses": {"Error": {"description": "Vinc API error",
                    "content": {"application/json": {"schema": {
                        "$ref": "#/components/schemas/Error"}}}}}}}


def serialized() -> str:
    return json.dumps(generate(), ensure_ascii=False, indent=2) + "\n"


def document() -> dict:
    return json.loads(FILE.read_text(encoding="utf-8"))
