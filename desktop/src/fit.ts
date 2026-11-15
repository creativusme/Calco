// Fit a cylinder (shaft, hole) or a plane (face) to the mesh around a clicked point.
// Uses every vertex of the scan near the click, so it is steadier than a few picked points.
import * as THREE from "three";

export interface Surface { p: Float32Array; n: Float32Array; count: number }
export interface CylinderFit { kind: "cylinder"; centre: THREE.Vector3; axis: THREE.Vector3; radius: number; rms: number; length: number; used: number }
export interface PlaneFit { kind: "plane"; centre: THREE.Vector3; normal: THREE.Vector3; rms: number; tiltDeg: number; size: number; used: number }
export type Fit = CylinderFit | PlaneFit;

/** World-space vertices and normals of every mesh under `root` (the model is static, so this is cached). */
export function surfaceOf(root: THREE.Object3D): Surface {
  const ps: number[] = [], ns: number[] = [];
  const v = new THREE.Vector3(), nn = new THREE.Vector3(), nm = new THREE.Matrix3();
  root.updateMatrixWorld(true);
  root.traverse((o) => {
    const m = o as THREE.Mesh;
    if (!m.isMesh) return;
    const pos = m.geometry.getAttribute("position"), nor = m.geometry.getAttribute("normal");
    if (!pos || !nor) return;
    nm.getNormalMatrix(m.matrixWorld);
    for (let i = 0; i < pos.count; i++) {
      v.fromBufferAttribute(pos, i).applyMatrix4(m.matrixWorld);
      nn.fromBufferAttribute(nor, i).applyMatrix3(nm).normalize();
      ps.push(v.x, v.y, v.z);
      ns.push(nn.x, nn.y, nn.z);
    }
  });
  return { p: Float32Array.from(ps), n: Float32Array.from(ns), count: ps.length / 3 };
}

/** Eigen-decomposition of a symmetric 3x3 matrix (Jacobi). Returns eigenvalues ascending with vectors. */
function eigen3(a: number[][]): { values: number[]; vectors: THREE.Vector3[] } {
  const m = a.map((r) => [...r]);
  const v = [[1, 0, 0], [0, 1, 0], [0, 0, 1]];
  for (let sweep = 0; sweep < 50; sweep++) {
    let off = 0;
    for (let i = 0; i < 3; i++) for (let j = i + 1; j < 3; j++) off += m[i][j] ** 2;
    if (off < 1e-30) break;
    for (let p = 0; p < 3; p++) for (let q = p + 1; q < 3; q++) {
      if (Math.abs(m[p][q]) < 1e-30) continue;
      const theta = (m[q][q] - m[p][p]) / (2 * m[p][q]);
      const t = Math.sign(theta || 1) / (Math.abs(theta) + Math.sqrt(theta * theta + 1));
      const c = 1 / Math.sqrt(t * t + 1), s = t * c;
      for (let k = 0; k < 3; k++) {
        const mkp = m[k][p], mkq = m[k][q];
        m[k][p] = c * mkp - s * mkq; m[k][q] = s * mkp + c * mkq;
      }
      for (let k = 0; k < 3; k++) {
        const mpk = m[p][k], mqk = m[q][k];
        m[p][k] = c * mpk - s * mqk; m[q][k] = s * mpk + c * mqk;
      }
      for (let k = 0; k < 3; k++) {
        const vkp = v[k][p], vkq = v[k][q];
        v[k][p] = c * vkp - s * vkq; v[k][q] = s * vkp + c * vkq;
      }
    }
  }
  const order = [0, 1, 2].sort((i, j) => m[i][i] - m[j][j]);
  return { values: order.map((i) => m[i][i]), vectors: order.map((i) => new THREE.Vector3(v[0][i], v[1][i], v[2][i]).normalize()) };
}

function near(s: Surface, centre: THREE.Vector3, r: number, keep?: (i: number) => boolean): number[] {
  const out: number[] = [];
  const r2 = r * r;
  for (let i = 0; i < s.count; i++) {
    const dx = s.p[3 * i] - centre.x, dy = s.p[3 * i + 1] - centre.y, dz = s.p[3 * i + 2] - centre.z;
    if (dx * dx + dy * dy + dz * dz <= r2 && (!keep || keep(i))) out.push(i);
  }
  return out;
}

const point = (s: Surface, i: number) => new THREE.Vector3(s.p[3 * i], s.p[3 * i + 1], s.p[3 * i + 2]);
const normal = (s: Surface, i: number) => new THREE.Vector3(s.n[3 * i], s.n[3 * i + 1], s.n[3 * i + 2]);

/** Plane through the vertices within `r` of the click whose normal agrees with the clicked face. */
export function fitPlane(s: Surface, at: THREE.Vector3, atNormal: THREE.Vector3, r: number, up: THREE.Vector3): PlaneFit | null {
  const idx = near(s, at, r, (i) => normal(s, i).dot(atNormal) > 0.8);
  if (idx.length < 20) return null;
  const c = new THREE.Vector3();
  idx.forEach((i) => c.add(point(s, i)));
  c.divideScalar(idx.length);
  const cov = [[0, 0, 0], [0, 0, 0], [0, 0, 0]];
  const d = new THREE.Vector3();
  for (const i of idx) {
    d.copy(point(s, i)).sub(c);
    const a = [d.x, d.y, d.z];
    for (let j = 0; j < 3; j++) for (let k = 0; k < 3; k++) cov[j][k] += a[j] * a[k];
  }
  const nrm = eigen3(cov).vectors[0];
  if (nrm.dot(atNormal) < 0) nrm.negate();
  let ss = 0;
  for (const i of idx) ss += (point(s, i).sub(c).dot(nrm)) ** 2;
  const tilt = THREE.MathUtils.radToDeg(Math.acos(Math.min(1, Math.abs(nrm.dot(up)))));
  return { kind: "plane", centre: c, normal: nrm, rms: Math.sqrt(ss / idx.length), tiltDeg: tilt, size: r, used: idx.length };
}

/** Circle through 2D points (algebraic least squares). */
function circle2d(xs: number[], ys: number[]): { cx: number; cy: number; r: number } | null {
  let sx = 0, sy = 0, sxx = 0, syy = 0, sxy = 0, sxz = 0, syz = 0, sz = 0;
  const n = xs.length;
  for (let i = 0; i < n; i++) {
    const x = xs[i], y = ys[i], z = x * x + y * y;
    sx += x; sy += y; sxx += x * x; syy += y * y; sxy += x * y; sxz += x * z; syz += y * z; sz += z;
  }
  // solve [sxx sxy sx; sxy syy sy; sx sy n] [a b c] = [sxz syz sz], circle x^2+y^2 = a x + b y + c
  const A = new THREE.Matrix3().set(sxx, sxy, sx, sxy, syy, sy, sx, sy, n);
  if (Math.abs(A.determinant()) < 1e-30) return null;
  const sol = new THREE.Vector3(sxz, syz, sz).applyMatrix3(A.invert());
  const cx = sol.x / 2, cy = sol.y / 2, r2 = sol.z + cx * cx + cy * cy;
  return r2 > 0 ? { cx, cy, r: Math.sqrt(r2) } : null;
}

/** Cylinder through the surface around the click: axis from the normals, radius from a circle fit. */
export function fitCylinder(s: Surface, at: THREE.Vector3, r0: number): CylinderFit | null {
  let r = r0;
  let best: CylinderFit | null = null;
  let line: { c: THREE.Vector3; axis: THREE.Vector3; radius: number } | null = null;
  for (let iter = 0; iter < 4; iter++) {
    // first pass: a sphere around the click; then the points close to the current cylinder
    const idx = line
      ? near(s, at, Math.max(r, 2.5 * line.radius), (i) => {
        const d = point(s, i).sub(line!.c);
        const radial = d.sub(line!.axis.clone().multiplyScalar(d.dot(line!.axis))).length();
        return Math.abs(radial - line!.radius) < 0.25 * line!.radius;
      })
      : near(s, at, r);
    if (idx.length < 30) return best;
    const cov = [[0, 0, 0], [0, 0, 0], [0, 0, 0]];
    for (const i of idx) {
      const a = [s.n[3 * i], s.n[3 * i + 1], s.n[3 * i + 2]];
      for (let j = 0; j < 3; j++) for (let k = 0; k < 3; k++) cov[j][k] += a[j] * a[k];
    }
    const axis = eigen3(cov).vectors[0]; // the direction the normals never point along
    const u = new THREE.Vector3(1, 0, 0);
    if (Math.abs(u.dot(axis)) > 0.9) u.set(0, 1, 0);
    u.sub(axis.clone().multiplyScalar(u.dot(axis))).normalize();
    const v = axis.clone().cross(u);
    const xs: number[] = [], ys: number[] = [], ts: number[] = [];
    for (const i of idx) {
      const q = point(s, i).sub(at);
      xs.push(q.dot(u)); ys.push(q.dot(v)); ts.push(q.dot(axis));
    }
    const circ = circle2d(xs, ys);
    if (!circ || circ.r > 20 * r0) return best;
    const c = at.clone().addScaledVector(u, circ.cx).addScaledVector(v, circ.cy);
    let ss = 0;
    for (let k = 0; k < xs.length; k++) ss += (Math.hypot(xs[k] - circ.cx, ys[k] - circ.cy) - circ.r) ** 2;
    const tmin = Math.min(...ts), tmax = Math.max(...ts);
    best = { kind: "cylinder", centre: c.clone().addScaledVector(axis, (tmin + tmax) / 2), axis, radius: circ.r,
      rms: Math.sqrt(ss / xs.length), length: tmax - tmin, used: idx.length };
    line = { c, axis, radius: circ.r };
    r = Math.max(r0, 1.5 * circ.r);
  }
  return best;
}
