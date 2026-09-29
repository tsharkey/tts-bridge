"""Asset bundle models for the 3D preview (previews.py): the transforms, the mesh files, and
the hub's routes, with Unity's objects faked (no bundle files needed)."""

import json
import math
import struct
from types import SimpleNamespace as NS

import numpy as np
import pytest
from fastapi.testclient import TestClient

import army
import previews
from app import server
from app.core import lists


def vec(x, y, z, w=None):
    return NS(x=x, y=y, z=z, **({"w": w} if w is not None else {}))


def transform(pos=(0, 0, 0), rot=(0, 0, 0, 1), scale=(1, 1, 1), father=None):
    """A Unity Transform: its father a PPtr (path_id 0 at the root)."""
    pptr = NS(path_id=1, read=lambda: father) if father is not None else NS(path_id=0)
    return NS(m_LocalPosition=vec(*pos), m_LocalRotation=vec(*rot), m_LocalScale=vec(*scale), m_Father=pptr)


def test_in_root_space():
    root = transform(pos=(100, 100, 100))                     # the root's own transform isn't applied
    turn = math.sqrt(0.5)                                      # 90 degrees about y
    child = transform(pos=(1, 2, 3), rot=(0, turn, 0, turn), scale=(2, 2, 2), father=root)
    got = previews.in_root_space([(1, 0, 0), (0, 1, 0)], child)
    # scaled by 2, turned 90 about y ((x, y, z) -> (z, y, -x)), moved by (1, 2, 3), then x mirrored
    assert np.allclose(got, [[-1, 2, 1], [-1, 4, 3]])
    assert np.allclose(previews.in_root_space([(1, 2, 3)], root), [[-1, 2, 3]])   # at the root: only mirrored


def test_mesh_file(monkeypatch):
    class Handler:
        def __init__(self, mesh):
            self.m_Vertices, self.m_UV0 = [(1, 0, 0), (0, 1, 0), (0, 0, 1)], [(0, 0), (1, 0), (0, 1)]

        def process(self):
            pass

        def get_triangles(self):
            return [[(0, 1, 2)]]
    import UnityPy.helpers.MeshHelper as helper
    monkeypatch.setattr(helper, "MeshHandler", Handler)
    data = previews.mesh_file(None, transform())
    assert data[:4] == b"TTSB" and struct.unpack("<II", data[4:12]) == (3, 1)
    positions = np.frombuffer(data, "<f4", 9, 12).reshape(3, 3)
    assert np.allclose(positions, [[-1, 0, 0], [0, 1, 0], [0, 0, 1]])   # mirrored like an OBJ file
    assert np.allclose(np.frombuffer(data, "<f4", 6, 48), [0, 0, 1, 0, 0, 1])
    assert list(np.frombuffer(data, "<u4", 3, 72)) == [0, 1, 2] and len(data) == 84


def test_preview_file_names(monkeypatch, tmp_path):
    monkeypatch.setattr(previews, "PREVIEWS", tmp_path)
    (tmp_path / "0123456789abcdef").mkdir()
    (tmp_path / "0123456789abcdef" / "part0.bin").write_bytes(b"TTSB")
    assert previews.preview_file("0123456789abcdef", "part0.bin").read_bytes() == b"TTSB"
    for key, name in (("0123456789abcdef", "parts.json"), ("../etc", "part0.bin"), ("0123456789abcdef", "../x.bin"),
                      ("0123456789abcdef", "part1.png")):
        with pytest.raises(ValueError, match="No such preview file"):
            previews.preview_file(key, name)


def test_a_converted_bundle_is_kept(monkeypatch, tmp_path):
    monkeypatch.setattr(previews, "PREVIEWS", tmp_path)
    made = []

    def convert(path, out):
        made.append(path)
        out.mkdir(parents=True)
        parts = [{"mesh": "part0.bin", "diffuse": "part0.png", "color": [1, 1, 1]}]
        (out / "parts.json").write_text(json.dumps(parts))
        return parts
    monkeypatch.setattr(previews, "convert", convert)
    monkeypatch.setattr(previews.layouts, "cached_path", lambda url, kind: tmp_path)   # "in TTS's cache"
    first = previews.bundle_preview("https://steam/x.unity3d")
    assert first == previews.bundle_preview("https://steam/x.unity3d") and len(made) == 1   # converted once
    assert first["id"] == previews.bundle_id("https://steam/x.unity3d")


def test_bundle_routes(monkeypatch, tmp_path):
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    bundle = {"Name": "Custom_Assetbundle", "Nickname": "Bladeguard", "Transform": {},
              "CustomAssetbundle": {"AssetbundleURL": "https://steam/bladeguard.unity3d"}}
    static = {"Name": "Custom_Model", "Nickname": "Intercessor", "Transform": {}, "CustomMesh": {"MeshURL": "m.obj"}}
    (catalog / "1e84c2.json").write_text(json.dumps({"tile": "1e84c2", "sha1": "x", "objects": [bundle, static]}))
    monkeypatch.setattr(army, "CATALOG", catalog)
    monkeypatch.setattr(previews, "PREVIEWS", tmp_path / "previews")
    monkeypatch.setattr(previews, "bundle_preview", lambda url: {
        "id": "0123456789abcdef", "parts": [{"mesh": "part0.bin", "diffuse": None, "color": [0.2, 0.2, 0.2]}]})
    client = TestClient(server.create_app())
    info = client.get("/api/catalog/entry", params={"pick": "1e84c2:0"}).json()
    assert (info["bundle"], info["static"], "preview" in info) == (True, False, False)   # the viewer asks for it
    tree = client.get("/api/catalog/bundle", params={"pick": "1e84c2:0"}).json()["preview"]
    assert [(c["mesh"], c["diffuse"], c["color"]) for c in tree["children"]] == [
        ("/api/catalog/preview/0123456789abcdef/part0.bin", "", [0.2, 0.2, 0.2])]
    r = client.get("/api/catalog/bundle", params={"pick": "1e84c2:1"})
    assert r.status_code == 400 and "no asset bundle" in r.json()["error"]
    assert client.get("/api/catalog/preview/0123456789abcdef/part9.bin").status_code == 400
    assert lists.bundle_urls({**static, "ChildObjects": [bundle]}) == ["https://steam/bladeguard.unity3d"]
