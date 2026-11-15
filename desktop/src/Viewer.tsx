import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { fitCylinder, fitPlane, surfaceOf, type Fit, type Surface } from "./fit";

export type ViewMode = "textured" | "clay" | "wire" | "splat";

interface Props {
  url: string;
  /** Gaussian splat of the same result (same frame as the model), shown in "splat" mode. */
  splatUrl?: string | null;
  mode: ViewMode;
  grid: boolean;
  theme: string;
  /** When true, clicks on the model pick points (model units, metres); `picks` of them make a measurement. */
  measuring: boolean;
  picks?: number;
  onMeasure: (points: THREE.Vector3[]) => void;
  /** Instead of points: fit a cylinder or a plane to the surface around one click. */
  fitKind?: "cylinder" | "plane" | null;
  onFit?: (fit: Fit | null) => void;
}

interface Scene {
  renderer: THREE.WebGLRenderer;
  scene: THREE.Scene;
  camera: THREE.PerspectiveCamera;
  controls: OrbitControls;
  model: THREE.Object3D | null;
  grid: THREE.GridHelper | null;
  originals: Map<THREE.Mesh, THREE.Material>;
  marks: THREE.Group;
  points: THREE.Vector3[];
  render: () => void;
  live: { on: boolean }; // keep rendering every frame (splats are re-sorted as the view moves)
  splat: { url: string; viewer: THREE.Object3D & { dispose?: () => Promise<void> } } | null;
}

/** three.js viewport: orbit, textured / clay / wireframe, ground grid in real units, two-point measure. */
export function Viewer({ url, splatUrl, mode, grid, theme, measuring, picks = 2, onMeasure, fitKind = null, onFit }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const state = useRef<Scene | null>(null);
  const measuringRef = useRef(measuring);
  const onMeasureRef = useRef(onMeasure);
  const picksRef = useRef(picks);
  picksRef.current = picks;
  const fitRef = useRef({ kind: fitKind, onFit });
  fitRef.current = { kind: fitKind, onFit };
  measuringRef.current = measuring;
  onMeasureRef.current = onMeasure;
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
    scene.add(new THREE.HemisphereLight(0xffffff, 0x5a5a66, 1.6));
    const key = new THREE.DirectionalLight(0xffffff, 1.4);
    key.position.set(1, 2, 1.5);
    scene.add(key);
    const marks = new THREE.Group();
    scene.add(marks);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.12;

    let frame = 0;
    const live = { on: false };
    const tick = () => {
      if (controls.update() || live.on) frame = requestAnimationFrame(tick);
      renderer.render(scene, camera);
    };
    const render = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(tick);
    };
    controls.addEventListener("change", render);
    const resize = () => {
      renderer.setSize(el.clientWidth, el.clientHeight, false);
      camera.aspect = el.clientWidth / Math.max(el.clientHeight, 1);
      camera.updateProjectionMatrix();
      render();
    };
    const ro = new ResizeObserver(resize);
    ro.observe(el);
    const s: Scene = { renderer, scene, camera, controls, model: null, grid: null, originals: new Map(), marks, points: [], render, live, splat: null };
    state.current = s;

    // a click that did not drag picks a point while measuring
    let down: { x: number; y: number } | null = null;
    const onDown = (e: PointerEvent) => { down = { x: e.clientX, y: e.clientY }; };
    const onUp = (e: PointerEvent) => {
      if (!down || !measuringRef.current || !s.model) return;
      if (Math.hypot(e.clientX - down.x, e.clientY - down.y) > 4) return;
      const rect = renderer.domElement.getBoundingClientRect();
      const ndc = new THREE.Vector2(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
      const ray = new THREE.Raycaster();
      ray.setFromCamera(ndc, camera);
      const hit = ray.intersectObject(s.model, true)[0];
      if (!hit) return;
      const { kind, onFit: report } = fitRef.current;
      if (kind) {
        const surface: Surface = s.model.userData.surface ??= surfaceOf(s.model);
        const r = s.model.userData.radius as number;
        const n = hit.face ? hit.face.normal.clone().transformDirection(hit.object.matrixWorld) : new THREE.Vector3(0, 1, 0);
        const fit = kind === "cylinder" ? fitCylinder(surface, hit.point, 0.03 * r)
          : fitPlane(surface, hit.point, n, 0.06 * r, new THREE.Vector3(0, 1, 0));
        s.points = [];
        drawMarks(s, fit);
        report?.(fit);
        return;
      }
      if (s.points.length >= picksRef.current) s.points = [];
      s.points.push(hit.point.clone());
      drawMarks(s);
      onMeasureRef.current([...s.points]);
    };
    renderer.domElement.addEventListener("pointerdown", onDown);
    renderer.domElement.addEventListener("pointerup", onUp);

    resize();
    return () => {
      ro.disconnect();
      live.on = false;
      cancelAnimationFrame(frame);
      void s.splat?.viewer.dispose?.();
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
      s.points = [];
      drawMarks(s);
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
      const centre = box.getCenter(new THREE.Vector3());
      const r = Math.max(box.getSize(new THREE.Vector3()).length() / 2, 1e-4);
      const vfov = THREE.MathUtils.degToRad(s.camera.fov);
      const fov = Math.min(vfov, 2 * Math.atan(Math.tan(vfov / 2) * s.camera.aspect)); // narrowest side
      s.controls.target.copy(centre);
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
    const clay = new THREE.MeshStandardMaterial({ color: dark ? 0xb4b7bb : 0xa3a6aa, roughness: 0.85, metalness: 0 });
    const wire = new THREE.MeshBasicMaterial({ color: dark ? 0xd2f24a : 0x3d4a00, wireframe: true });
    s.originals.forEach((orig, mesh) => { mesh.material = mode === "textured" || mode === "splat" ? orig : mode === "clay" ? clay : wire; });
    if (s.grid) { s.scene.remove(s.grid); s.grid.dispose(); s.grid = null; }
    if (grid) {
      const r = s.model.userData.radius as number;
      const cell = 10 ** Math.floor(Math.log10(r)); // 1 cm cells for a ~10 cm object, 10 cm for ~1 m
      s.grid = new THREE.GridHelper(cell * 20, 20, dark ? 0x4b5058 : 0xa9adb3, dark ? 0x25282d : 0xdcdee1);
      s.scene.add(s.grid);
    }
    s.render();
    return () => { clay.dispose(); wire.dispose(); };
  }, [mode, grid, theme, status]);

  // splat mode: the Gaussian splat replaces the mesh on screen (measuring still picks on the mesh)
  useEffect(() => {
    const s = state.current;
    if (!s || status !== "ready" || !s.model) return;
    const show = mode === "splat" && !!splatUrl;
    s.model.visible = !show;
    if (s.splat) s.splat.viewer.visible = show;
    s.live.on = show;
    s.render();
    if (!show || (s.splat && s.splat.url === splatUrl)) return;
    let cancelled = false;
    (async () => {
      const GS = await import("@mkkellogg/gaussian-splats-3d");
      if (cancelled || !state.current) return;
      if (s.splat) { s.scene.remove(s.splat.viewer); await s.splat.viewer.dispose?.(); }
      const viewer = new GS.DropInViewer({ sharedMemoryForWorkers: false, gpuAcceleratedSort: false });
      await viewer.addSplatScene(splatUrl!, { showLoadingUI: false, progressiveLoad: false });
      if (cancelled || !state.current) { await viewer.dispose(); return; }
      s.splat = { url: splatUrl!, viewer };
      s.scene.add(viewer);
      s.render();
    })().catch(() => { /* no splat for this result: the mesh stays hidden behind an empty view */ });
    return () => { cancelled = true; };
  }, [mode, splatUrl, status]);

  // leaving measure mode, or switching between distance and diameter, clears the picked points
  useEffect(() => {
    const s = state.current;
    if (!s) return;
    s.points = [];
    drawMarks(s);
  }, [measuring, picks, fitKind]);

  return (
    <div className={`viewer ${measuring ? "is-measuring" : ""}`} ref={host}>
      {status === "loading" && <div className="viewer-note" aria-live="polite">Loading model…</div>}
      {status === "error" && <div className="viewer-note" aria-live="polite">Could not load the model. Process the capture again.</div>}
    </div>
  );
}

/** Draw the picked points and the line between them, always on top of the model. */
function drawMarks(s: Scene, fit: Fit | null = null) {
  s.marks.clear();
  if (fit) {
    const lime = new THREE.LineBasicMaterial({ color: 0xd2f24a, depthTest: false });
    const loop = (c: THREE.Vector3, n: THREE.Vector3, r: number, square = false) => {
      const u = new THREE.Vector3(1, 0, 0);
      if (Math.abs(u.dot(n)) > 0.9) u.set(0, 1, 0);
      u.sub(n.clone().multiplyScalar(u.dot(n))).normalize();
      const v = n.clone().cross(u);
      const pts = square
        ? [[-1, -1], [1, -1], [1, 1], [-1, 1], [-1, -1]].map(([a, b]) => c.clone().addScaledVector(u, a * r).addScaledVector(v, b * r))
        : Array.from({ length: 65 }, (_, i) => { const t = (i / 64) * Math.PI * 2; return c.clone().addScaledVector(u, Math.cos(t) * r).addScaledVector(v, Math.sin(t) * r); });
      const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), lime);
      line.renderOrder = 10;
      s.marks.add(line);
    };
    if (fit.kind === "cylinder") {
      const half = fit.axis.clone().multiplyScalar(Math.max(fit.length, fit.radius) / 2);
      loop(fit.centre.clone().add(half), fit.axis, fit.radius);
      loop(fit.centre.clone().sub(half), fit.axis, fit.radius);
      const axisLine = new THREE.Line(new THREE.BufferGeometry().setFromPoints([fit.centre.clone().add(half), fit.centre.clone().sub(half)]), lime);
      axisLine.renderOrder = 10;
      s.marks.add(axisLine);
    } else {
      loop(fit.centre, fit.normal, fit.size, true);
    }
    s.render();
    return;
  }
  const r = ((s.model?.userData.radius as number) ?? 0.1) * 0.012;
  const material = new THREE.MeshBasicMaterial({ color: 0xd2f24a, depthTest: false });
  for (const p of s.points) {
    const dot = new THREE.Mesh(new THREE.SphereGeometry(r, 16, 12), material);
    dot.position.copy(p);
    dot.renderOrder = 10;
    s.marks.add(dot);
  }
  const ring = s.points.length === 3 ? circleThrough(s.points[0], s.points[1], s.points[2]) : null;
  const path = ring ? ring.outline : s.points.length === 2 ? s.points : null;
  if (path) {
    const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(path), new THREE.LineBasicMaterial({ color: 0xd2f24a, depthTest: false }));
    line.renderOrder = 10;
    s.marks.add(line);
  }
  s.render();
}

/** The circle through three points (a hole edge, a shaft): centre, radius and a drawable outline. */
export function circleThrough(p1: THREE.Vector3, p2: THREE.Vector3, p3: THREE.Vector3) {
  const a = p1.clone().sub(p3), b = p2.clone().sub(p3);
  const axb = a.clone().cross(b);
  const d = 2 * axb.lengthSq();
  if (d < 1e-18) return null; // the points are on a line
  const centre = b.clone().multiplyScalar(a.lengthSq()).sub(a.clone().multiplyScalar(b.lengthSq())).cross(axb).divideScalar(d).add(p3);
  const radius = centre.distanceTo(p1);
  const u = p1.clone().sub(centre).normalize(), v = axb.clone().normalize().cross(u);
  const outline = Array.from({ length: 65 }, (_, i) => {
    const t = (i / 64) * Math.PI * 2;
    return centre.clone().addScaledVector(u, Math.cos(t) * radius).addScaledVector(v, Math.sin(t) * radius);
  });
  return { centre, radius, outline };
}
