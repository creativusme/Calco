import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";

export type ViewMode = "textured" | "clay" | "wire";

interface Props { url: string; mode: ViewMode; grid: boolean; theme: string }

/** three.js viewport: orbit, textured / clay / wireframe, ground grid in real units. */
export function Viewer({ url, mode, grid, theme }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const state = useRef<{
    renderer: THREE.WebGLRenderer; scene: THREE.Scene; camera: THREE.PerspectiveCamera;
    controls: OrbitControls; model: THREE.Object3D | null; grid: THREE.GridHelper | null;
    originals: Map<THREE.Mesh, THREE.Material | THREE.Material[]>; render: () => void;
  } | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");

  // one-time scene setup
  useEffect(() => {
    const el = host.current!;
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    el.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(35, 1, 0.001, 100);
    camera.position.set(0.3, 0.25, 0.3);
    scene.add(new THREE.HemisphereLight(0xffffff, 0x5a5a66, 1.6));
    const key = new THREE.DirectionalLight(0xffffff, 1.4);
    key.position.set(1, 2, 1.5);
    scene.add(key);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.12;

    let frame = 0;
    const render = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(tick); };
    const tick = () => { if (controls.update()) frame = requestAnimationFrame(tick); renderer.render(scene, camera); };
    controls.addEventListener("change", render);
    const resize = () => {
      const { clientWidth: w, clientHeight: h } = el;
      renderer.setSize(w, h, false);
      camera.aspect = w / Math.max(h, 1);
      camera.updateProjectionMatrix();
      render();
    };
    const ro = new ResizeObserver(resize);
    ro.observe(el);
    state.current = { renderer, scene, camera, controls, model: null, grid: null, originals: new Map(), render };
    resize();
    return () => {
      ro.disconnect();
      cancelAnimationFrame(frame);
      controls.dispose();
      renderer.dispose();
      el.removeChild(renderer.domElement);
      state.current = null;
    };
  }, []);

  // load the model
  useEffect(() => {
    const s = state.current!;
    let cancelled = false;
    setStatus("loading");
    new GLTFLoader().load(url, (gltf) => {
      if (cancelled || !state.current) return;
      if (s.model) s.scene.remove(s.model);
      s.originals.clear();
      s.model = gltf.scene;
      s.model.traverse((o) => {
        const m = o as THREE.Mesh;
        if (!m.isMesh) return;
        m.geometry.computeVertexNormals();
        // scanned textures already contain the scene lighting: show them unlit
        const src = m.material as THREE.MeshStandardMaterial;
        const unlit = new THREE.MeshBasicMaterial({ map: src.map, vertexColors: m.geometry.hasAttribute("color"), side: THREE.DoubleSide });
        m.material = unlit;
        s.originals.set(m, unlit);
      });
      s.scene.add(s.model);
      const box = new THREE.Box3().setFromObject(s.model);
      const size = box.getSize(new THREE.Vector3());
      const centre = box.getCenter(new THREE.Vector3());
      const r = Math.max(size.length() / 2, 1e-4);
      s.controls.target.copy(centre);
      const vfov = THREE.MathUtils.degToRad(s.camera.fov);
      const fov = Math.min(vfov, 2 * Math.atan(Math.tan(vfov / 2) * s.camera.aspect)); // narrowest side
      s.camera.position.copy(centre).add(new THREE.Vector3(1, 0.7, 1).normalize().multiplyScalar((r / Math.sin(fov / 2)) * 1.15));
      s.camera.near = r / 100;
      s.camera.far = r * 100;
      s.camera.updateProjectionMatrix();
      s.model.userData.radius = r;
      setStatus("ready");
    }, undefined, () => !cancelled && setStatus("error"));
    return () => { cancelled = true; };
  }, [url]);

  // display mode, grid and theme
  useEffect(() => {
    const s = state.current;
    if (!s || status !== "ready" || !s.model) return;
    const dark = theme === "dark";
    const clay = new THREE.MeshStandardMaterial({ color: dark ? 0xb9b4ab : 0xa9a49b, roughness: 0.85, metalness: 0 });
    const wire = new THREE.MeshBasicMaterial({ color: dark ? 0xe8a15a : 0xb5651d, wireframe: true });
    s.originals.forEach((orig, mesh) => { mesh.material = mode === "textured" ? orig : mode === "clay" ? clay : wire; });
    if (s.grid) { s.scene.remove(s.grid); s.grid.dispose(); s.grid = null; }
    if (grid) {
      const r = s.model.userData.radius as number;
      const cell = 10 ** Math.floor(Math.log10(r)); // 1 cm cells for a ~10 cm object, 10 cm for ~1 m
      const cells = 20;
      s.grid = new THREE.GridHelper(cell * cells, cells, dark ? 0x4a4f57 : 0xb0b4ba, dark ? 0x2a2d32 : 0xd9dbdf);
      s.scene.add(s.grid);
    }
    s.render();
    return () => { clay.dispose(); wire.dispose(); };
  }, [mode, grid, theme, status]);

  return (
    <div className="viewer" ref={host}>
      {status === "loading" && <div className="viewer-note">Loading model…</div>}
      {status === "error" && <div className="viewer-note">Could not load the model.</div>}
    </div>
  );
}
