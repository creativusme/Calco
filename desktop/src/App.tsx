import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Capture, type ExportResult, type Info, type Job } from "./api";
import { Viewer, type ViewMode } from "./Viewer";

const NBSP = " ";
const fmtTime = (s: number) => (s < 90 ? `${Math.round(s)}${NBSP}s` : s < 5400 ? `${Math.round(s / 60)}${NBSP}min` : `${(s / 3600).toFixed(1)}${NBSP}h`);
const fmtDate = (iso?: string) => (iso ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(iso)) : "");
const fmtMm = (v: number) => (v >= 1000 ? `${(v / 1000).toFixed(3)}${NBSP}m` : `${v.toFixed(v < 100 ? 2 : 1)}${NBSP}mm`);
const active = (j: Job) => j.state === "running" || j.state === "queued";
const jobTone = (j: Job) => (active(j) ? "running" : j.state === "done" ? "done" : "failed");

function useTheme() {
  const [theme, setTheme] = useState(() => localStorage.getItem("calco-theme") ?? "dark");
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("calco-theme", theme);
  }, [theme]);
  return [theme, () => setTheme((t) => (t === "dark" ? "light" : "dark"))] as const;
}

export default function App() {
  const [theme, toggleTheme] = useTheme();
  const [info, setInfo] = useState<Info | null>(null);
  const [captures, setCaptures] = useState<Capture[]>([]);
  const [offline, setOffline] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [i, c] = await Promise.all([api.info(), api.captures()]);
      setInfo(i);
      setCaptures(c);
      setOffline(false);
    } catch {
      setOffline(true);
    }
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 1500);
    return () => clearInterval(t);
  }, [refresh]);

  const capture = captures.find((c) => c.id === selected) ?? null;
  useEffect(() => {
    if (!capture && captures.length) setSelected(captures[0].id);
  }, [capture, captures]);

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true" />
          <span className="brand-name" translate="no">Calco</span>
          <button className="ghost icon" onClick={toggleTheme} aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}>
            <span aria-hidden="true">{theme === "dark" ? "☀" : "☾"}</span>
          </button>
        </div>
        <div className="label">Captures <span className="count">{captures.length || ""}</span></div>
        <nav className="capture-list" aria-label="Captures">
          {captures.map((c) => (
            <button key={c.id} className={`capture ${c.id === selected ? "is-selected" : ""}`} onClick={() => setSelected(c.id)}>
              <img src={api.previewUrl(c.id)} alt="" width={48} height={36} loading="lazy" />
              <span className="capture-text">
                <span className="capture-name">{c.name}</span>
                <span className="muted small">{c.error ? "Unreadable capture" : `${c.frames}${NBSP}photos · ${c.size_mb.toFixed(0)}${NBSP}MB`}</span>
              </span>
              <CaptureStatus capture={c} />
            </button>
          ))}
          {!captures.length && !offline && <p className="muted pad">No captures yet. Send one from the phone, or create a demo capture.</p>}
        </nav>
        <SidebarFooter info={info} offline={offline} />
      </aside>

      {offline ? (
        <main className="empty">
          <h1>Engine Not Running</h1>
          <p className="muted">Double-click <code>Start Calco.cmd</code>, or start the engine from the <code>engine</code> folder. This page reconnects by itself.</p>
          <pre>.venv\Scripts\python -m calco serve</pre>
        </main>
      ) : capture ? (
        <CaptureView key={capture.id} capture={capture} info={info} theme={theme} refresh={refresh} onDeleted={() => setSelected(null)} />
      ) : (
        <Welcome info={info} refresh={refresh} />
      )}
    </div>
  );
}

function CaptureStatus({ capture }: { capture: Capture }) {
  const jobs = capture.jobs;
  const state = jobs.some(active) ? "running" : jobs.some((j) => j.state === "done") ? "done" : jobs.some((j) => j.state === "failed") ? "failed" : "new";
  const label = { running: "Processing", done: "Processed", failed: "Failed", new: "Not processed" }[state];
  return <span className={`pip pip-${state}`} role="img" aria-label={label} title={label} />;
}

function SidebarFooter({ info, offline }: { info: Info | null; offline: boolean }) {
  const menu = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const close = (e: Event) => {
      const el = menu.current;
      if (!el?.open) return;
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !el.contains(e.target as Node)) el.open = false;
    };
    addEventListener("pointerdown", close);
    addEventListener("keydown", close);
    return () => { removeEventListener("pointerdown", close); removeEventListener("keydown", close); };
  }, []);
  return (
    <div className="sidebar-foot">
      <details className="menu" ref={menu}>
        <summary className="button wide">Scan Mat</summary>
        <div className="menu-body">
          <div className="label">Print on paper, at 100%</div>
          <div className="menu-row">
            {["A5", "A4", "A3"].map((s) => <a key={s} className="button" href={api.matUrl(s)}>{s}</a>)}
          </div>
          <div className="label">3D print (STL, two colours)</div>
          <div className="menu-row">
            {["A5", "A4"].map((s) => <a key={s} className="button" href={api.mat3dUrl(s)}>{s}</a>)}
          </div>
          <p className="muted small">Glue paper mats to something rigid and flat. A curled mat still gives the right scale, a flat one is more accurate.</p>
        </div>
      </details>
      <div className="status-line">
        <span className={`pip ${offline ? "pip-failed" : "pip-done"}`} aria-hidden="true" />
        <span>{offline ? "Engine offline" : "Engine ready"}</span>
        {info && <span className="muted right mono">v{info.version}</span>}
      </div>
      {info && (
        <dl className="kv">
          <dt>Phone</dt>
          <dd className="mono">{info.addresses.length ? `${info.addresses[0]}:${info.port}` : "no network"}</dd>
          <dt>GPU</dt>
          <dd>{info.tools.gpu ? (info.tools.colmap_cuda ? "In use" : "Not used yet") : "None found"}</dd>
        </dl>
      )}
    </div>
  );
}

function Welcome({ info, refresh }: { info: Info | null; refresh: () => void }) {
  const [busy, setBusy] = useState(false);
  const demo = async () => { setBusy(true); await api.demo(); refresh(); };
  const running = busy || info?.demo_running;
  return (
    <main className="empty">
      <h1>Scan on Site. Rebuild Here.</h1>
      <ol className="steps">
        <li><strong>Print a scan mat</strong> (bottom left) and put the object on it for millimetre-true scale.</li>
        <li><strong>Capture with the phone</strong>, then tap Send. The phone finds this computer on the same Wi-Fi{info?.addresses.length ? <>, or type <span className="mono">{info.addresses[0]}:{info.port}</span> in its settings</> : null}.</li>
        <li><strong>Pick a quality and process.</strong> Everything runs on this computer, nothing is uploaded.</li>
      </ol>
      <button className="button" onClick={demo} disabled={running}>{running ? "Rendering Demo Capture… About a Minute" : "Create Demo Capture"}</button>
    </main>
  );
}

type Tab = "process" | "result" | "export";

function CaptureView({ capture, info, theme, refresh, onDeleted }: {
  capture: Capture; info: Info | null; theme: string; refresh: () => void; onDeleted: () => void;
}) {
  const jobs = capture.jobs;
  const [jobId, setJobId] = useState<string | null>(null);
  const job = jobs.find((j) => j.id === jobId) ?? jobs.find(active) ?? jobs.find((j) => j.state === "done") ?? jobs[0] ?? null;
  const [mode, setMode] = useState<ViewMode>("textured");
  const [grid, setGrid] = useState(true);
  const [measuring, setMeasuring] = useState(false);
  const [distance, setDistance] = useState<number | null>(null);
  const [showLog, setShowLog] = useState(false);
  const hasModel = !!job?.has_model && job.state === "done";
  const [tab, setTab] = useState<Tab>(hasModel ? "result" : "process");

  // show the result as soon as a job finishes
  const lastState = useRef(job?.state);
  useEffect(() => {
    if (lastState.current && lastState.current !== "done" && job?.state === "done") setTab("result");
    lastState.current = job?.state;
  }, [job?.state]);
  useEffect(() => { setMeasuring(false); setDistance(null); }, [job?.id]);

  const remove = async () => {
    if (!confirm(`Delete “${capture.name}” with its photos and all results? This cannot be undone.`)) return;
    await api.deleteCapture(capture.id);
    onDeleted();
    refresh();
  };
  const removeJob = async (j: Job) => {
    if (!confirm(`Delete the ${String(j.options.quality ?? "")} result?`)) return;
    await api.deleteJob(j.id);
    refresh();
  };

  return (
    <>
      <main className="stage">
        <header className="stage-head">
          <div className="stage-title">
            <h1>{capture.name}</h1>
            <span className="muted small">
              {[capture.mode, capture.frames && `${capture.frames}${NBSP}photos`, capture.resolution?.join("×"), capture.device, fmtDate(capture.created)].filter(Boolean).join(" · ")}
            </span>
          </div>
          <div className="tags">
            {capture.has_depth && <span className="tag">LiDAR</span>}
            {capture.has_poses && <span className="tag">Poses</span>}
            {capture.has_crop_box && <span className="tag">Crop box</span>}
            <button className="ghost danger" onClick={remove}>Delete…</button>
          </div>
        </header>

        {jobs.length > 0 && (
          <div className="results" role="tablist" aria-label="Results">
            {jobs.map((j) => (
              <span key={j.id} className={`result ${j.id === job?.id ? "is-selected" : ""}`}>
                <button role="tab" aria-selected={j.id === job?.id} onClick={() => setJobId(j.id)}>
                  <span className={`pip pip-${jobTone(j)}`} aria-hidden="true" />
                  <span className="result-name">{String(j.options.quality ?? "medium")}</span>
                  <span className="muted mono">
                    {j.state === "done" && j.report ? fmtTime(j.report.seconds) : j.state === "running" ? `${Math.round((j.progress ?? 0) * 100)}%` : j.state}
                  </span>
                </button>
                <button className="ghost icon" aria-label={`Delete ${String(j.options.quality ?? "")} result`} onClick={() => removeJob(j)}><span aria-hidden="true">×</span></button>
              </span>
            ))}
          </div>
        )}

        <div className="viewport">
          {hasModel && job ? (
            <>
              <Viewer url={api.modelUrl(job)} mode={mode} grid={grid} theme={theme} measuring={measuring} onMeasure={setDistance} />
              <div className="toolbar" role="toolbar" aria-label="View">
                {(["textured", "clay", "wire"] as ViewMode[]).map((m) => (
                  <button key={m} aria-pressed={mode === m} onClick={() => setMode(m)}>{{ textured: "Texture", clay: "Clay", wire: "Wire" }[m]}</button>
                ))}
                <span className="sep" aria-hidden="true" />
                <button aria-pressed={grid} onClick={() => setGrid(!grid)}>Grid</button>
                <button aria-pressed={measuring} onClick={() => { setMeasuring(!measuring); setDistance(null); }}>Measure</button>
              </div>
              {measuring
                ? <MeasureBar job={job} distance={distance} refresh={refresh} />
                : job.report && <Dimensions job={job} />}
            </>
          ) : (
            <div className="preview">
              <img src={api.previewUrl(capture.id)} alt="First photo of the capture" />
              {!job && <div className="viewer-note">Not processed yet. Choose a quality on the right.</div>}
            </div>
          )}
        </div>

        {job && <Pipeline job={job} onLog={() => setShowLog(true)} refresh={refresh} />}
      </main>

      <aside className="inspector">
        <div className="tabs" role="tablist" aria-label="Inspector">
          {(["process", "result", "export"] as Tab[]).map((t) => (
            <button key={t} role="tab" aria-selected={tab === t} disabled={t !== "process" && !hasModel} onClick={() => setTab(t)}>
              {{ process: "Process", result: "Result", export: "Export" }[t]}
            </button>
          ))}
        </div>
        <div className="tab-body">
          {tab === "process" && <ProcessPanel capture={capture} info={info} onStarted={(j) => { setJobId(j.id); refresh(); }} />}
          {tab === "result" && hasModel && job?.report && <ResultPanel job={job} refresh={refresh} />}
          {tab === "export" && hasModel && job && info && <ExportPanel job={job} info={info} />}
        </div>
      </aside>

      {showLog && job && <LogDialog job={job} onClose={() => setShowLog(false)} />}
    </>
  );
}

function Dimensions({ job }: { job: Job }) {
  const d = job.report!.model.dimensions_mm.map((v) => v * job.user_scale);
  return (
    <div className="readout" aria-label="Model dimensions">
      <span><i>X</i>{fmtMm(d[0])}</span><span><i>Y</i>{fmtMm(d[1])}</span><span><i>Z</i>{fmtMm(d[2])}</span>
    </div>
  );
}

/** Two-point measure: shows the distance and can calibrate the model from a known length. */
function MeasureBar({ job, distance, refresh }: { job: Job; distance: number | null; refresh: () => void }) {
  const [real, setReal] = useState("");
  const raw = distance != null ? distance * 1000 : null; // millimetres before calibration
  const apply = async () => {
    const m = Number(real.replace(",", "."));
    if (!(m > 0) || !raw) return;
    await api.calibrate(job.id, m / raw);
    setReal("");
    refresh();
  };
  return (
    <div className="readout readout-measure">
      {raw == null ? (
        <span className="muted">Click two points on the model</span>
      ) : (
        <>
          <span><i>Distance</i>{fmtMm(raw * job.user_scale)}</span>
          <label className="inline-field">
            <span className="muted">Real length</span>
            <input name="real-length" autoComplete="off" inputMode="decimal" placeholder="mm…" value={real} onChange={(e) => setReal(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && apply()} />
          </label>
          <button className="button primary" onClick={apply} disabled={!(Number(real.replace(",", ".")) > 0)}>Calibrate</button>
        </>
      )}
    </div>
  );
}

function Pipeline({ job, onLog, refresh }: { job: Job; onLog: () => void; refresh: () => void }) {
  const stages = job.stages ?? [];
  return (
    <footer className="pipeline">
      <ol className="pipeline-steps">
        {stages.map((s, i) => (
          <li key={s.name} className={`step step-${s.state}`}>
            <span className="step-bar" />
            <span className="step-num mono">{String(i + 1).padStart(2, "0")}</span>
            <span className="step-label">{s.label}</span>
            <span className="muted small mono">{s.seconds != null ? fmtTime(s.seconds) : s.state === "running" ? "…" : ""}</span>
          </li>
        ))}
      </ol>
      <div className="pipeline-status" aria-live="polite">
        {active(job) && <span className="mono">{Math.round((job.progress ?? 0) * 100)}%</span>}
        <span className={`status-text ${job.state === "failed" ? "error-text" : "muted"}`}>
          {job.state === "failed" ? job.error ?? "Failed" : job.state === "cancelled" ? "Cancelled" : job.state === "queued" ? "Waiting in the queue…" : job.message}
        </span>
        <span className="right">
          <button className="ghost" onClick={onLog}>Log</button>
          {active(job) && <button className="ghost" onClick={async () => { await api.cancelJob(job.id); refresh(); }}>Cancel</button>}
        </span>
      </div>
    </footer>
  );
}

function ProcessPanel({ capture, info, onStarted }: { capture: Capture; info: Info | null; onStarted: (j: Job) => void }) {
  const [quality, setQuality] = useState(capture.has_depth ? "draft" : "medium");
  const [isolate, setIsolate] = useState(capture.mode !== "space");
  const [closeBottom, setCloseBottom] = useState(true);
  const [method, setMethod] = useState("auto");
  const [bar, setBar] = useState("100.0");
  const [error, setError] = useState<string | null>(null);
  const qualities = info?.presets.quality ?? [];
  const q = qualities.find((x) => x.name === quality);
  const photo = method === "photo" || (method === "auto" && q?.method === "photo");
  const needsOpenMVS = photo && info && !info.tools.openmvs;
  const canUseGpu = photo && info && info.tools.gpu && !info.tools.colmap_cuda;

  const start = async () => {
    setError(null);
    try {
      const printScale = Number(bar.replace(",", ".")) / 100;
      onStarted(await api.createJob(capture.id, {
        quality, method, isolate, close_bottom: closeBottom,
        print_scale: Number.isFinite(printScale) && printScale > 0.9 && printScale < 1.1 ? printScale : 1,
      }));
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <div className="panel">
      <div className="label">Quality</div>
      <div className="quality" role="radiogroup" aria-label="Quality">
        {qualities.map((x) => (
          <button key={x.name} role="radio" aria-checked={quality === x.name} onClick={() => setQuality(x.name)}>
            <span>{x.label}</span>
            <span className="muted small">{{ draft: "LiDAR", medium: "Photos", high: "Photos", ultra: "Photos" }[x.name] ?? ""}</span>
          </button>
        ))}
      </div>
      <p className="hint">{q?.hint}</p>

      <label className="check"><input type="checkbox" checked={isolate} onChange={(e) => setIsolate(e.target.checked)} /><span>Isolate the object<small>Crop, remove the surface it stands on</small></span></label>
      <label className="check"><input type="checkbox" checked={closeBottom} disabled={!isolate} onChange={(e) => setCloseBottom(e.target.checked)} /><span>Flat closed base<small>Ready for 3D printing</small></span></label>

      <div className="field-row">
        <label className="field">Method
          <select name="method" value={method} onChange={(e) => setMethod(e.target.value)}>
            <option value="auto">Automatic</option>
            <option value="lidar">LiDAR fusion</option>
            <option value="photo">Photogrammetry</option>
          </select>
        </label>
        <label className="field" title="Measure the 100 mm bar printed on the mat. Enter what the caliper reads.">Mat bar (mm)
          <input name="mat-bar" autoComplete="off" value={bar} onChange={(e) => setBar(e.target.value)} inputMode="decimal" />
        </label>
      </div>

      {needsOpenMVS ? (
        <div className="notice">
          Photogrammetry needs OpenMVS (22{NBSP}MB, downloaded once).
          <button className="button" disabled={info!.tools.installing} onClick={() => api.installTools("openmvs")}>{info!.tools.installing ? "Installing…" : "Install OpenMVS"}</button>
        </div>
      ) : (
        <>
          {canUseGpu && (
            <div className="notice">
              The graphics card is not used yet. The GPU engine is a 415{NBSP}MB download, once.
              <button className="button" disabled={info!.tools.installing} onClick={() => api.installTools("colmap")}>{info!.tools.installing ? "Downloading…" : "Enable GPU"}</button>
            </div>
          )}
          <button className="button primary wide" onClick={start} disabled={!!capture.error}>Process</button>
        </>
      )}
      {error && <p className="error-text" aria-live="polite">{error}</p>}
    </div>
  );
}

function ResultPanel({ job, refresh }: { job: Job; refresh: () => void }) {
  const r = job.report!;
  const dims = r.model.dimensions_mm;
  const [axis, setAxis] = useState(0);
  const [measured, setMeasured] = useState("");
  const source = { mat: "Scan mat", lidar: "LiDAR", none: "No reference" }[r.scale.source];
  const calibrated = Math.abs(job.user_scale - 1) > 1e-9;
  const value = Number(measured.replace(",", "."));

  const apply = async () => {
    if (!(value > 0)) return;
    await api.calibrate(job.id, value / dims[axis]);
    setMeasured("");
    refresh();
  };

  return (
    <div className="panel">
      <div className="label">Accuracy</div>
      <dl className="facts">
        <dt>Scale from</dt><dd><span className={`tag tag-${calibrated ? "mat" : r.scale.source}`}>{calibrated ? "Caliper" : source}</span></dd>
        {r.scale.expected_error_mm != null && !calibrated && (<><dt>Expected error</dt><dd className="mono">±{NBSP}{r.scale.expected_error_mm}{NBSP}mm</dd></>)}
        {r.scale.mat && (<><dt>Mat fit</dt><dd className="mono">{r.scale.mat.tags}{NBSP}tags · {r.scale.mat.fit_rms_mm}{NBSP}mm</dd></>)}
        {r.alignment && (<><dt>Photos aligned</dt><dd className="mono">{r.alignment.registered}/{r.alignment.total} · {r.alignment.reprojection_px}{NBSP}px</dd></>)}
        <dt>Mesh</dt><dd className="mono">{new Intl.NumberFormat().format(r.model.faces)}{NBSP}faces · {r.model.watertight ? "closed" : "open"}</dd>
        {r.compute && (<><dt>Computed on</dt><dd>{r.compute.dense === "gpu" ? "GPU" : "CPU"}</dd></>)}
      </dl>
      {r.warnings.map((w) => <p key={w} className="notice">{w}</p>)}

      <div className="label spaced">Calibrate With a Caliper</div>
      <p className="hint tight">Type the real size of one side. To use any two points instead, press <strong>Measure</strong> above the model.</p>
      <div className="field-row calibrate">
        <label className="field">Side
          <select name="axis" value={axis} onChange={(e) => setAxis(Number(e.target.value))}>
            {["X", "Y", "Z"].map((a, i) => <option key={a} value={i}>{a} · {fmtMm(dims[i] * job.user_scale)}</option>)}
          </select>
        </label>
        <label className="field">Real size (mm)
          <input name="measured" autoComplete="off" placeholder="80.00…" value={measured} onChange={(e) => setMeasured(e.target.value)} inputMode="decimal"
            onKeyDown={(e) => e.key === "Enter" && apply()} />
        </label>
      </div>
      <button className="button wide" onClick={apply} disabled={!(value > 0)}>Apply Calibration</button>
      {calibrated && <button className="link" onClick={async () => { await api.calibrate(job.id, 1); refresh(); }}>Reset calibration (×{job.user_scale.toFixed(4)})</button>}
    </div>
  );
}

function ExportPanel({ job, info }: { job: Job; info: Info }) {
  const profiles = info.presets.profiles;
  const [profile, setProfile] = useState(profiles[0]?.name ?? "print");
  const [formats, setFormats] = useState<string[]>(() => [...(profiles[0]?.formats ?? [])]);
  const [units, setUnits] = useState(profiles[0]?.units ?? "mm");
  const [result, setResult] = useState<ExportResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const p = profiles.find((x) => x.name === profile);

  // a profile is only a starting point: picking one fills the choices, after that they are the user's
  const pick = (name: string) => {
    const next = profiles.find((x) => x.name === name);
    if (!next) return;
    setProfile(name);
    setFormats([...next.formats]);
    setUnits(next.units);
  };
  const toggle = (f: string) => setFormats((cur) => (cur.includes(f) ? cur.filter((x) => x !== f) : [...cur, f]));

  const run = async () => {
    setBusy(true);
    setError(null);
    try { setResult(await api.exportJob(job.id, formats, units, p?.textured ?? true)); }
    catch (e) { setError((e as Error).message); }
    setBusy(false);
  };

  return (
    <div className="panel">
      <div className="label">Made For</div>
      <div className="chips">
        {profiles.map((x) => <button key={x.name} aria-pressed={profile === x.name} onClick={() => pick(x.name)}>{x.label}</button>)}
      </div>
      <p className="hint">{p?.hint}</p>

      <div className="label">Files</div>
      <div className="formats">
        {info.presets.formats.map((f) => (
          <label key={f} className={`format ${formats.includes(f) ? "is-on" : ""}`}>
            <input type="checkbox" checked={formats.includes(f)} onChange={() => toggle(f)} />{f.toUpperCase()}
          </label>
        ))}
      </div>
      <label className="field">Units
        <select name="units" value={units} onChange={(e) => setUnits(e.target.value)}>
          {info.presets.units.map((u) => <option key={u}>{u}</option>)}
        </select>
      </label>
      <button className="button primary wide" onClick={run} disabled={busy || !formats.length}>
        {busy ? "Exporting…" : formats.length ? `Export ${formats.map((f) => f.toUpperCase()).join(" + ")}` : "Choose a Format"}
      </button>
      {error && <p className="error-text" aria-live="polite">{error}</p>}
      {result && (
        <div className="exports" aria-live="polite">
          {result.files.map((f) => (
            <a key={f.file} href={api.exportUrl(job.id, f.file)} download>
              <span className="file-name">{f.file}</span><span className="muted mono">{(f.size / 1e6).toFixed(1)}{NBSP}MB</span>
            </a>
          ))}
          <button className="link" onClick={() => api.reveal(job.id)}>Open Export Folder</button>
        </div>
      )}
    </div>
  );
}

function LogDialog({ job, onClose }: { job: Job; onClose: () => void }) {
  const [text, setText] = useState("Loading…");
  useEffect(() => {
    const load = () => api.log(job.id).then(setText).catch(() => setText("Log unavailable."));
    load();
    const t = setInterval(load, 2000);
    const key = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    addEventListener("keydown", key);
    return () => { clearInterval(t); removeEventListener("keydown", key); };
  }, [job.id, onClose]);
  return (
    <div className="dialog-backdrop" onClick={onClose}>
      <div className="dialog" role="dialog" aria-modal="true" aria-label="Processing log" onClick={(e) => e.stopPropagation()}>
        <header><strong>Processing Log</strong><button className="ghost icon" onClick={onClose} aria-label="Close"><span aria-hidden="true">×</span></button></header>
        <pre>{text}</pre>
      </div>
    </div>
  );
}
