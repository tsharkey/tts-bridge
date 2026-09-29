"""
previews.py — asset bundle models (Unity files a browser can't read) as OBJ meshes and PNG
textures, for the 3D preview on the Models page and in Scribe (app/static/viewer3d.js).

    import previews
    previews.bundle_preview(url)    # -> {"id", "parts": [{"mesh", "diffuse", "color"}]}, converting it once
    previews.preview_file(id, name) # the path of one of its files, for the hub to serve

A bundle is read from TTS's download cache (Mods/Assetbundles), or downloaded when TTS hasn't
loaded that model yet, and converted once into cache/previews/<id>/: each mesh with its
transforms applied, in the same handedness as a static model's OBJ files (layouts.read_bundle
does the same for terrain), and a PNG of its material's main texture (at most 1024px). A
bundle's material can live in a bundle shared between models, which TTS may not have
downloaded: that mesh is shown plain. An animated model is shown in the pose its mesh is saved
in; animations don't play.

A mesh file is binary, little-endian (a vehicle's meshes as OBJ text run to hundreds of MB):
"TTSB", uint32 vertices, uint32 triangles, float32 x y z per vertex, float32 u v per vertex,
uint32 three vertex indices per triangle. viewer3d.js reads it.
"""

import hashlib
import json
import re
import struct

import numpy as np

import data
import layouts

PREVIEWS = data.CACHE / "previews"
TEXTURE_SIZE = 1024
MAIN_TEXTURES = ("_MainTex", "_BaseMap", "_BaseColorMap", "_Albedo", "_AlbedoMap", "_Diffuse")
FILE_RE = re.compile(r"^[0-9a-f]{16}$|^part\d+\.(bin|png)$")


def bundle_id(url):
    return hashlib.sha1(url.encode()).hexdigest()[:16]


def components(go):
    """A GameObject's components (older bundles list them as (id, component) pairs)."""
    return [c.component if hasattr(c, "component") else c[-1] for c in go.m_Component]


def rotation(q):
    """A Unity quaternion as a 3x3 rotation matrix."""
    x, y, z, w = q.x, q.y, q.z, q.w
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def in_root_space(vertices, transform):
    """A mesh's vertices (n x 3, Unity space) with its transforms up to the root applied:
    scaled, rotated and moved by each, then x mirrored, so they read like an OBJ file TTS loads."""
    v = np.asarray(vertices, dtype=np.float64).reshape(-1, 3)
    tr = transform
    while tr.m_Father and tr.m_Father.path_id:   # every transform but the root's
        p, s = tr.m_LocalPosition, tr.m_LocalScale
        v = (v * [s.x, s.y, s.z]) @ rotation(tr.m_LocalRotation).T + [p.x, p.y, p.z]
        tr = tr.m_Father.read()
    v[:, 0] = -v[:, 0]
    return v


def mesh_file(mesh, transform):
    """A mesh as the binary preview file (see the module's docstring)."""
    from UnityPy.helpers.MeshHelper import MeshHandler
    h = MeshHandler(mesh)
    h.process()
    verts = in_root_space(h.m_Vertices, transform).astype("<f4")
    uv = np.asarray(h.m_UV0 or [(0.0, 0.0)] * len(verts), dtype="<f4").reshape(-1, 2)
    tris = np.asarray([t for sub in h.get_triangles() for t in sub], dtype="<u4").reshape(-1, 3)
    return b"TTSB" + struct.pack("<II", len(verts), len(tris)) + verts.tobytes() + uv.tobytes() + tris.tobytes()


def main_texture(material):
    """A material's colour texture: (its id, a PPtr to read it), or None."""
    envs = dict(material.m_SavedProperties.m_TexEnvs)
    for name in MAIN_TEXTURES + tuple(k for k in envs if "albedo" in k.lower() or "diffuse" in k.lower()):
        env = envs.get(name)
        if env is not None and env.m_Texture.path_id:
            return env.m_Texture.path_id, env.m_Texture
    return None


def tint(material):
    colours = dict(material.m_SavedProperties.m_Colors)
    c = colours.get("_Color") or colours.get("_BaseColor")
    return [round(c.r, 3), round(c.g, 3), round(c.b, 3)] if c is not None else [1, 1, 1]


def convert(path, out):
    """Write a bundle's meshes and main textures into the folder `out`. -> the parts."""
    import UnityPy
    env = UnityPy.load(str(path))
    out.mkdir(parents=True, exist_ok=True)
    parts, textures = [], {}
    for obj in env.objects:
        if obj.type.name not in ("MeshFilter", "SkinnedMeshRenderer"):
            continue
        holder = obj.read()
        if not holder.m_Mesh or not holder.m_Mesh.path_id:
            continue
        go = holder.m_GameObject.read()
        comps = components(go)
        transform = next(c.read() for c in comps if c.type.name == "Transform")
        renderer = holder if obj.type.name == "SkinnedMeshRenderer" else \
            next((c.read() for c in comps if c.type.name == "MeshRenderer"), None)
        n = len(parts)
        try:
            (out / f"part{n}.bin").write_bytes(mesh_file(holder.m_Mesh.read(), transform))
        except FileNotFoundError:
            continue   # the mesh itself is in a bundle TTS hasn't downloaded
        part = {"mesh": f"part{n}.bin", "diffuse": None, "color": [1, 1, 1]}
        materials = [m for m in (renderer.m_Materials if renderer else []) if m.path_id]
        try:
            if materials:
                material = materials[0].read()
                part["color"] = tint(material)
                texture = main_texture(material)
                if texture is not None:
                    tid, pptr = texture
                    if tid not in textures:
                        image = pptr.read().image
                        image.thumbnail((TEXTURE_SIZE, TEXTURE_SIZE))
                        image.save(out / f"part{n}.png")
                        textures[tid] = f"part{n}.png"
                    part["diffuse"] = textures[tid]
        except FileNotFoundError:
            pass   # its material is in a bundle shared between models that TTS hasn't downloaded: plain grey
        parts.append(part)
    if not parts:
        raise ValueError("That asset bundle has no meshes to show.")
    (out / "parts.json").write_text(json.dumps(parts))
    return parts


def bundle_preview(url):
    """A bundle as the viewer's parts, converting it the first time. -> {"id", "parts"}."""
    key = bundle_id(url)
    out = PREVIEWS / key
    done = out / "parts.json"
    if done.exists():
        return {"id": key, "parts": json.loads(done.read_text())}
    path = layouts.cached_path(url, "bundle")
    if not path.exists():
        try:
            layouts.download(url, path)
        except OSError as e:
            raise ValueError(f"Couldn't download that asset bundle: {e}") from e
    return {"id": key, "parts": convert(path, out)}


def preview_file(key, name):
    """The path of one converted file; ValueError for a name that isn't one of ours."""
    if not (FILE_RE.match(key) and FILE_RE.match(name)) or FILE_RE.match(name).group(1) is None:
        raise ValueError("No such preview file.")
    path = PREVIEWS / key / name
    if not path.exists():
        raise ValueError("No such preview file.")
    return path
