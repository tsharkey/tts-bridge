// A 3D view of a Force Org model: a static one's OBJ files straight from Steam's CDN, an asset
// bundle as the hub converts it (/api/catalog/bundle, previews.py: binary meshes and PNGs).
// Pages that use it need the three.js import map (see tools/scribe/static/index.html).
//   import {viewer} from "/viewer3d.js";
//   const v = viewer(canvas);  v.show(entryInfo, status);  // status(text) reports progress; status(null) when shown
import * as THREE from "three";
import {OBJLoader} from "three/addons/loaders/OBJLoader.js";
import {OrbitControls} from "three/addons/controls/OrbitControls.js";

// A converted asset bundle mesh (previews.py): "TTSB", uint32 vertices, uint32 triangles, then
// float32 x y z and u v per vertex, and uint32 indices per triangle, little-endian.
async function loadBinary(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(r.statusText);
  const buf = await r.arrayBuffer(), dv = new DataView(buf);
  const n = dv.getUint32(4, true), t = dv.getUint32(8, true);
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(new Float32Array(buf, 12, n * 3), 3));
  geometry.setAttribute("uv", new THREE.BufferAttribute(new Float32Array(buf, 12 + n * 12, n * 2), 2));
  geometry.setIndex(new THREE.BufferAttribute(new Uint32Array(buf, 12 + n * 20, t * 3), 1));
  geometry.computeVertexNormals();
  return new THREE.Mesh(geometry);
}

export function viewer(canvas) {
  let renderer, scene, camera, controls, current = null, token = 0;
  function init() {
    renderer = new THREE.WebGLRenderer({canvas, antialias: true});
    renderer.setPixelRatio(window.devicePixelRatio);
    scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0d0e10);
    scene.add(new THREE.HemisphereLight(0xffffff, 0x333344, 2.2));
    const sun = new THREE.DirectionalLight(0xffffff, 2);
    sun.position.set(3, 5, 4);
    scene.add(sun);
    camera = new THREE.PerspectiveCamera(35, 1, 0.01, 1000);
    controls = new OrbitControls(camera, canvas);
    controls.autoRotate = true;
    (function loop() {
      requestAnimationFrame(loop);
      if (canvas.offsetParent === null) return;  // hidden: don't draw
      const w = canvas.clientWidth, h = canvas.clientHeight;
      if (w && h && (canvas.width !== Math.round(w * devicePixelRatio) || canvas.height !== Math.round(h * devicePixelRatio))) {
        renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix();
      }
      controls.update();
      renderer.render(scene, camera);
    })();
  }
  async function show(info, status = () => {}) {
    if (!renderer) init();
    const mine = ++token;
    if (current) { scene.remove(current); current = null; }
    if (!info) return;
    if (!info.preview && info.bundle) {
      status("Reading the asset bundle… the first time can take a few seconds for a big model.");
      try {
        const r = await fetch(`/api/catalog/bundle?pick=${encodeURIComponent(info.pick)}`);
        const body = await r.json();
        if (!r.ok) throw new Error(body.error || r.statusText);
        info = {...info, preview: body.preview};
      } catch (e) { if (mine === token) status("Couldn't read this model's asset bundle: " + e.message); return; }
      if (mine !== token) return;
    }
    if (!info.preview) { status(`${info.name} has nothing to preview.`); return; }
    status("Loading model…");
    try {
      const loader = new OBJLoader(), textures = new THREE.TextureLoader();
      // One group per TTS part: its own mesh plus its attached children, with the
      // part's local transform (Unity Euler angles apply Z, then X, then Y).
      async function build(part) {
        const group = new THREE.Group();
        group.position.set(...part.pos);
        group.rotation.set(...part.rot.map(d => THREE.MathUtils.degToRad(d)), "YXZ");
        group.scale.set(...part.scale);
        if (part.mesh) {
          const mesh = part.mesh.endsWith(".bin") ? await loadBinary(part.mesh) : await loader.loadAsync(part.mesh);
          let map = null;
          if (part.diffuse) {
            map = await textures.loadAsync(part.diffuse).catch(() => null);
            if (map) map.colorSpace = THREE.SRGBColorSpace;
          }
          const c = part.color, tint = c[0] + c[1] + c[2] < 0.05 ? [0.55, 0.55, 0.58] : c;  // untinted black reads as nothing
          const material = new THREE.MeshStandardMaterial({map, color: map ? 0xffffff : new THREE.Color(...tint),
                                                           roughness: 0.75, metalness: 0.1, side: THREE.DoubleSide});
          mesh.traverse(m => { if (m.isMesh) m.material = material; });
          group.add(mesh);
        }
        for (const child of await Promise.all(part.children.map(build))) group.add(child);
        return group;
      }
      const model = await build(info.preview);
      if (mine !== token) return;
      const obj = new THREE.Group();
      obj.add(model);
      obj.scale.x = -1;  // Unity (TTS) meshes are mirrored relative to three.js
      const box = new THREE.Box3().setFromObject(obj), size = box.getSize(new THREE.Vector3());
      obj.position.sub(box.getCenter(new THREE.Vector3()));
      const r = Math.max(size.x, size.y, size.z);
      camera.position.set(r * 1.1, r * 0.8, r * 1.4);
      camera.near = r / 100; camera.far = r * 20; camera.updateProjectionMatrix();
      controls.target.set(0, 0, 0);
      scene.add(obj);
      current = obj;
      status(null);
    } catch (e) { if (mine === token) status("Couldn't load this model: " + e.message); }
  }
  return {show};
}
