import json

import gdstk
from fastapi.testclient import TestClient

from gds_migrator.web.app import app, sessions


def _project_files(tmp_path):
    layout_path = tmp_path / "layout.gds"
    library = gdstk.Library(unit=1e-9, precision=1e-12)
    cell = gdstk.Cell("TOP")
    cell.add(
        gdstk.rectangle((0, 0), (5, 5), layer=1),
        gdstk.rectangle((7, 0), (12, 5), layer=1),
    )
    library.add(cell)
    library.write_gds(layout_path)

    technology = {
        "name": "test-tech",
        "grid": 1,
        "min_spacing": [{"layers": [1, 1], "value": 10}],
    }
    return layout_path.read_bytes(), json.dumps(technology).encode()


def _create_project(client, tmp_path, layer_file=None):
    layout, technology = _project_files(tmp_path)
    files = {
        "layout": ("layout.gds", layout, "application/octet-stream"),
        "technology": ("technology.json", technology, "application/json"),
    }
    if layer_file is not None:
        files["layers"] = layer_file
    response = client.post("/api/sessions", files=files)
    assert response.status_code == 200, response.text
    return response.json()


def test_upload_constraints_toggle_migration_and_download(tmp_path):
    with TestClient(app) as client:
        project = _create_project(client, tmp_path)
        session_id = project["id"]
        try:
            graph_response = client.get(f"/api/sessions/{session_id}/constraints", params={"cell": "TOP"})
            assert graph_response.status_code == 200
            spacing = next(edge for edge in graph_response.json()["edges"] if edge["kind"] == "minimum_spacing")
            assert spacing["axis"] == "x"
            assert spacing["value"] == 10

            toggle_response = client.post(
                f"/api/sessions/{session_id}/constraints/{spacing['id']}",
                json={"cell": "TOP", "excluded": True},
            )
            assert toggle_response.status_code == 200

            migration_response = client.post(f"/api/sessions/{session_id}/migrate")
            assert migration_response.status_code == 200, migration_response.text
            assert migration_response.json()["excluded_constraints"] == 1

            layout_response = client.get(
                f"/api/sessions/{session_id}/layout",
                params={"cell": "TOP", "version": "after"},
            )
            assert layout_response.status_code == 200
            rectangles = sorted(layout_response.json()["rectangles"], key=lambda item: item["xmin"])
            assert rectangles[1]["xmin"] == 7

            download_response = client.get(f"/api/sessions/{session_id}/download")
            assert download_response.status_code == 200
            assert len(download_response.content) > 4
        finally:
            client.delete(f"/api/sessions/{session_id}")
            sessions.pop(session_id, None)


def test_layer_properties_file_is_loaded_and_used(tmp_path):
    layer_file = (
        "layers.lyp",
        b"<layer-properties><properties><fill-color>#123456</fill-color>"
        b"<name>metal one</name><source>1/0@1</source></properties></layer-properties>",
        "application/xml",
    )
    with TestClient(app) as client:
        project = _create_project(client, tmp_path, layer_file)
        try:
            assert {"layer": 1, "name": "metal one", "color": "#123456"} in project["layers"]
        finally:
            client.delete(f"/api/sessions/{project['id']}")


def test_gf180_simplified_upload_uses_layer_datatype_rules(tmp_path):
    layout_path = tmp_path / "gf180.gds"
    library = gdstk.Library(unit=1e-9, precision=1e-12)
    cell = gdstk.Cell("TOP")
    cell.add(gdstk.rectangle((0, 0), (200, 200), layer=34, datatype=4))
    library.add(cell)
    library.write_gds(layout_path)

    technology = json.dumps([
        {
            "type": "width",
            "layer1": "metal1_dummy",
            "layer2": "metal1_dummy",
            "value": 0.4,
            "context": {"bound": "minimum"},
        },
    ]).encode()
    layer_file = (
        "gf180mcu.lyp",
        b"<layer-properties><properties><source>metal1_dummy 34/4@1</source>"
        b"</properties></layer-properties>",
        "application/xml",
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/sessions",
            files={
                "layout": ("gf180.gds", layout_path.read_bytes(), "application/octet-stream"),
                "technology": ("gf180_simplified.json", technology, "application/json"),
                "layers": layer_file,
            },
        )
        assert response.status_code == 200, response.text
        project = response.json()
        try:
            assert {
                "layer": 34,
                "name": "metal1_dummy",
                "color": "#778899",
            } in project["layers"]
            constraints = client.get(
                f"/api/sessions/{project['id']}/constraints",
                params={"cell": "TOP"},
            )
            assert constraints.status_code == 200
            assert any(edge["value"] == 400 for edge in constraints.json()["edges"])
        finally:
            client.delete(f"/api/sessions/{project['id']}")
            sessions.pop(project["id"], None)


def test_invalid_file_type_is_rejected(tmp_path):
    with TestClient(app) as client:
        response = client.post(
            "/api/sessions",
            files={
                "layout": ("layout.txt", b"not a layout", "text/plain"),
                "technology": ("technology.json", b'{"name":"test","grid":1}', "application/json"),
            },
        )
    assert response.status_code == 415


def test_invalid_technology_json_syntax_reports_line_number(tmp_path):
    layout, _ = _project_files(tmp_path)
    invalid_technology = b'{\n  "name": "test-tech",\n  "grid": 1,\n  "min_spacing": [\n    {"layers": [1, 1], "value": }\n  ]\n}'
    with TestClient(app) as client:
        response = client.post(
            "/api/sessions",
            files={
                "layout": ("layout.gds", layout, "application/octet-stream"),
                "technology": ("technology.json", invalid_technology, "application/json"),
            },
        )

    assert response.status_code == 422
    assert "line 5" in response.json()["detail"]
    assert "column" in response.json()["detail"]


def test_invalid_technology_rule_reports_field_line(tmp_path):
    layout, _ = _project_files(tmp_path)
    invalid_technology = (
        b'{\n  "name": "test-tech",\n  "grid": 1,\n  "min_width": {\n'
        b'    "1": "wide"\n  }\n}'
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/sessions",
            files={
                "layout": ("layout.gds", layout, "application/octet-stream"),
                "technology": ("technology.json", invalid_technology, "application/json"),
            },
        )

    assert response.status_code == 422
    assert "line 5" in response.json()["detail"]
    assert "non-negative integer" in response.json()["detail"]


def test_invalid_layer_color_is_reported(tmp_path):
    layer_file = (
        "layers.lyp",
        b"<layer-properties><properties><fill-color>red</fill-color>"
        b"<source>1/0@1</source></properties></layer-properties>",
        "application/xml",
    )
    with TestClient(app) as client:
        layout, technology = _project_files(tmp_path)
        response = client.post(
            "/api/sessions",
            files={
                "layout": ("layout.gds", layout, "application/octet-stream"),
                "technology": ("technology.json", technology, "application/json"),
                "layers": layer_file,
            },
        )
    assert response.status_code == 422
    assert "Invalid display color" in response.json()["detail"]
