// Typed client for the engine's HTTP API (engine/calco/server.py).

// Same origin when the engine serves the built UI or Vite proxies /api; absolute inside Tauri.
const BASE = location.hostname === "tauri.localhost" || location.protocol === "tauri:" ? "http://127.0.0.1:7878" : "";

export type StageState = "pending" | "running" | "done" | "failed";
export type JobState = "queued" | "running" | "done" | "failed" | "cancelled";

export interface Stage { name: string; label: string; state: StageState; seconds: number | null }

export interface Report {
  plan: { method: "lidar" | "photo"; isolate: boolean; quality: string; notes: string[] };
  warnings: string[];
  seconds: number;
  alignment?: { registered: number; total: number; reprojection_px: number; phone_pose_rms_mm: number | null };
  model: { vertices: number; faces: number; dimensions_mm: [number, number, number]; watertight: boolean; textured?: boolean; splat?: boolean };
  compute?: { features: "gpu" | "cpu"; dense: "gpu" | "cpu" };
  photos?: { dropped_blurry: number[]; used: number };
  combined?: { overlap_pct: number; distance_mm: number };
  scale: {
    source: "mat" | "markers" | "lidar" | "none";
    markers?: { count: number; spread_pct: number } | null;
    mat: { layout: string; tags: number; fit_rms_mm: number } | null;
    lidar_vs_mat_pct: number | null;
    scale_error_pct: number | null;
    surface_noise_mm: number;
    expected_error_mm: number | null;
  };
}

export interface Job {
  id: string;
  capture_id: string;
  capture_name: string;
  options: Record<string, unknown>;
  state: JobState;
  created: number;
  user_scale: number;
  progress: number | null;
  started: number | null;
  finished: number | null;
  eta_seconds: number | null;
  message: string | null;
  error: string | null;
  stages: Stage[] | null;
  report: Report | null;
  has_model: boolean;
}

export interface Capture {
  id: string;
  name: string;
  mode?: string;
  created?: string;
  frames?: number;
  has_poses?: boolean;
  has_depth?: boolean;
  has_crop_box?: boolean;
  device?: string;
  resolution?: [number, number];
  size_mb: number;
  error?: string;
  jobs: Job[];
}

export interface Quality { name: string; label: string; hint: string; method: string }
export interface Profile { name: string; label: string; hint: string; formats: string[]; units: string; textured: boolean }

export interface Info {
  version: string;
  library: string;
  port: number;
  addresses: string[];
  hostname: string;
  tools: { openmvs: boolean; colmap_cuda: boolean; splat?: boolean; gpu: string | null; installing: boolean;
    install_progress: { component: string; done: number; total: number } | null; install_error: string | null };
  presets: { quality: Quality[]; profiles: Profile[]; formats: string[]; units: string[] };
  demo_running: boolean;
  phone: PhoneStatus;
  network: { ok: boolean; checked: boolean; problems?: string[]; fix_script?: string; python?: string };
}

export interface PhoneStatus {
  connected: boolean;
  link?: "cable" | "wifi" | "wifi-sync";
  name?: string;
  address?: string | null;
  transfer?: { capture?: string; done?: number; total?: number; index?: number; count?: number; active?: boolean } | null;
  error?: string | null;
  usb_service: boolean | null;
}

export interface ChecklistItem { id: string | null; text: string; heading?: boolean }
export interface ChecklistSection { title: string; items: ChecklistItem[] }
export interface ChecklistData { sections: ChecklistSection[]; state: Record<string, { done: boolean; note: string }> }

export interface ExportResult { folder: string; files: { format: string; file: string; size: number }[] }

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(BASE + path, init);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* not JSON */ }
    throw new Error(detail);
  }
  return res.json();
}

const post = <T,>(path: string, body?: unknown) =>
  call<T>(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });

export const api = {
  info: () => call<Info>("/api/info"),
  captures: () => call<Capture[]>("/api/captures"),
  deleteCapture: (id: string) => call(`/api/captures/${id}`, { method: "DELETE" }),
  previewUrl: (id: string) => `${BASE}/api/captures/${id}/preview`,
  createJob: (capture_id: string, options: Record<string, unknown>) => post<Job>("/api/jobs", { capture_id, options }),
  cancelJob: (id: string) => post(`/api/jobs/${id}/cancel`),
  deleteJob: (id: string) => call(`/api/jobs/${id}`, { method: "DELETE" }),
  log: (id: string) => fetch(`${BASE}/api/jobs/${id}/log`).then((r) => r.text()),
  modelUrl: (job: Job) => `${BASE}/api/jobs/${job.id}/model.glb`,
  jobPhotosUrl: (job: Job) => `${BASE}/api/jobs/${job.id}/photos.zip`,
  capturePhotosUrl: (id: string) => `${BASE}/api/captures/${id}/photos.zip`,
  reportUrl: (job: Job) => `${BASE}/api/jobs/${job.id}/report.pdf`,
  splatUrl: (job: Job) => `${BASE}/api/jobs/${job.id}/splat.ply`,
  calibrate: (id: string, scale: number) => post<Job>(`/api/jobs/${id}/calibrate`, { scale }),
  exportJob: (id: string, formats: string[], units: string | null, textured: boolean) =>
    post<ExportResult>(`/api/jobs/${id}/export`, { formats, units, textured }),
  exportUrl: (id: string, file: string) => `${BASE}/api/jobs/${id}/exports/${encodeURIComponent(file)}`,
  reveal: (id: string) => post(`/api/jobs/${id}/reveal`),
  matUrl: (size: string) => `${BASE}/api/mat.pdf?size=${size}`,
  mat3dUrl: (size: string) => `${BASE}/api/mat.zip?size=${size}`,
  markersUrl: () => `${BASE}/api/markers.pdf`,
  demo: () => post<{ started: boolean }>("/api/demo"),
  recheckNetwork: () => post("/api/network/recheck"),
  fixNetwork: () => post<{ started: boolean }>("/api/network/fix"),
  checklist: () => call<ChecklistData>("/api/checklist"),
  check: (id: string, body: { done?: boolean; note?: string }) => post(`/api/checklist/${encodeURIComponent(id)}`, body),
  pairing: () => call<{ codes: { address: string; code: string }[]; pin: string; port: number }>("/api/pairing"),
  newPin: () => post<{ pin: string }>("/api/pairing/new"),
  importFile: (iid: string, file: File) =>
    call<{ ok: boolean }>(`/api/import/${iid}/files/${encodeURIComponent(file.name)}`, { method: "PUT", body: file }),
  importComplete: (iid: string, name: string | null) => post<{ capture: Capture }>(`/api/import/${iid}/complete`, { name }),
  installSplat: () => post<{ started: boolean }>("/api/tools/install-splat"),
  installTools: () => post<{ started: boolean }>("/api/tools/install"),
};
