import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type Capture, type ExportResult, type Info, type Job } from "./api";
import { Viewer, type ViewMode } from "./Viewer";

const fmtTime = (s: number) => (s < 90 ? `${Math.round(s)} s` : s < 5400 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(1)} h`);
const fmtDate = (iso?: string) => (iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "");
const fmtMm = (v: number) => (v >= 1000 ? `${(v / 1000).toFixed(3)} m` : `${v.toFixed(v < 100 ? 2 : 1)} mm`);
const active = (j: Job) => j.state === "running" || j.state === "queued";

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
          <span className="brand-mark" aria-hidden />
          <span>Calco</span>
          <button className="ghost icon" onClick={toggleTheme} title="Switch theme" aria-label="Switch theme">
            {theme === "dark" ? "☀" : "☾"}
          </button>
        </div>
        <div className="section-title">Captures</div>
        <div className="capture-list">
          {captures.map((c) => (
            <button key={c.id} className={`capture ${c.id === selected ? "is-selected" : ""}`} onClick={() => setSelected(c.id)}>
              <img src={api.previewUrl(c.id)} alt="" loading="lazy" />
              <span className="capture-text">
                <span className="capture-name">{c.name}</span>
                <span className="muted">{c.error ? "Unreadable capture" : `${c.frames} photos · ${c.size_mb.toFixed(0)} MB`}</span>
              </span>
              <StatusDot capture={c} />
            </button>
          ))}
          {!captures.length && !offline && <p className="muted pad">No captures yet. Send one from the phone, or create a demo capture below.</p>}
        </div>
        <Connection info={info} offline={offline} />
      </aside>

      {offline ? (
        <main className="empty">
          <h1>Engine not running</h1>
          <p className="muted">Start it from the <code>engine</code> folder, then this page reconnects by itself.</p>
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

function StatusDot({ capture }: { capture: Capture }) {
  const jobs = capture.jobs;
  const state = jobs.some(active) ? "running" : jobs.some((j) => j.state === "done") ? "done" : jobs.some((j) => j.state === "failed") ? "failed" : "new";
  const label = { running: "Processing", done: "Processed", failed: "Failed", new: "Not processed" }[state];
  return <span className={`dot dot-${state}`} title={label} aria-label={label} />;
}

function Connection({ info, offline }: { info: Info | null; offline: boolean }) {
  return (
    <div className="connection">
      <div className="row">
        <span className={`dot ${offline ? "dot-failed" : "dot-done"}`} />
        <strong>{offline ? "Engine offline" : "Engine ready"}</strong>
        {info && <span className="muted right">v{info.version}</span>}
      </div>
      {info && (
        <>
          <div className="muted small">Phone address</div>
          <div className="mono">{info.addresses.length ? info.addresses.map((a) => `${a}:${info.port}`).join("  ") : "no network"}</div>
          <div className="muted small">{info.tools.gpu ?? "No NVIDIA GPU detected"}</div>
        </>
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
      <h1>Scan on site. Rebuild here.</h1>
      <ol className="steps">
        <li><strong>Print the scan mat</strong> at 100% and put the object on it for millimetre-true scale.<br />
          <a className="button" href={api.matUrl("A4")}>Scan mat A4</a> <a className="button" href={api.matUrl("A3")}>Scan mat A3</a></li>
        <li><strong>Capture with the phone</strong>, then tap Send. The phone finds this computer on the same Wi-Fi{info?.addresses.length ? <> (or type <span className="mono">{info.addresses[0]}:{info.port}</span>)</> : null}.</li>
        <li><strong>Pick a quality and process.</strong> Everything runs on this computer.</li>
      </ol>
      <button className="button" onClick={demo} disabled={running}>{running ? "Rendering demo capture… about a minute" : "Create a demo capture"}</button>
    </main>
  );
}

function CaptureView({ capture, info, theme, refresh, onDeleted }: {
  capture: Capture; info: Info | null; theme: string; refresh: () => void; onDeleted: () => void;
}) {
  const jobs = capture.jobs;
  const [jobId, setJobId] = useState<string | null>(null);
  const job = jobs.find((j) => j.id === jobId) ?? jobs.find(active) ?? jobs.find((j) => j.state === "done") ?? jobs[0] ?? null;
  const [mode, setMode] = useState<ViewMode>("textured");
  const [grid, setGrid] = useState(true);
  const [showLog, setShowLog] = useState(false);

  const remove = async () => {
    if (!confirm(`Delete "${capture.name}" with its photos and all results? This cannot be undone.`)) return;
    await api.deleteCapture(capture.id);
    onDeleted();
    refresh();
  };

  return (
    <>
      <main className="stage">
        <header className="stage-head">
          <div>
            <h1>{capture.name}</h1>
            <span className="muted">
              {[capture.mode, capture.frames && `${capture.frames} photos`, capture.resolution?.join("×"), capture.device, fmtDate(capture.created)].filter(Boolean).join(" · ")}
            </span>
          </div>
          <div className="tags">
            {capture.has_depth && <span className="tag">LiDAR</span>}
            {capture.has_poses && <span className="tag">Poses</span>}
            {capture.has_crop_box && <span className="tag">Crop box</span>}
          </div>
        </header>

        <div className="viewport">
          {job?.has_model && job.state === "done" ? (
            <>
              <Viewer url={api.modelUrl(job)} mode={mode} grid={grid} theme={theme} />
              <div className="toolbar" role="toolbar" aria-label="View">
                {(["textured", "clay", "wire"] as ViewMode[]).map((m) => (
                  <button key={m} className={mode === m ? "is-on" : ""} onClick={() => setMode(m)}>{{ textured: "Texture", clay: "Clay", wire: "Wire" }[m]}</button>
                ))}
                <span className="sep" />
                <button className={grid ? "is-on" : ""} onClick={() => setGrid(!grid)}>Grid</button>
              </div>
              {job.report && <Dimensions job={job} />}
            </>
          ) : (
            <div className="preview">
              <img src={api.previewUrl(capture.id)} alt="First photo of the capture" />
              {!job && <div className="preview-note">Not processed yet. Choose a quality on the right.</div>}
            </div>
          )}
        </div>

        {job && <Pipeline job={job} onLog={() => setShowLog(true)} refresh={refresh} />}
      </main>

      <aside className="inspector">
        <ProcessPanel capture={capture} info={info} onStarted={(j) => { setJobId(j.id); refresh(); }} />
        {jobs.length > 0 && (
          <section>
            <div className="section-title">Results</div>
            {jobs.map((j) => (
              <div key={j.id} className={`result ${j.id === job?.id ? "is-selected" : ""}`}>
                <button className="result-main" onClick={() => setJobId(j.id)}>
                  <span className={`dot dot-${active(j) ? "running" : j.state === "done" ? "done" : "failed"}`} />
                  <span>{String(j.options.quality ?? "medium")}</span>
                  <span className="muted right">
                    {j.state === "done" && j.report ? fmtTime(j.report.seconds) : j.state === "running" ? `${Math.round((j.progress ?? 0) * 100)}%` : j.state}
                  </span>
                </button>
                <button className="ghost icon" title="Delete result" aria-label="Delete result"
                  onClick={async () => { await api.deleteJob(j.id); refresh(); }}>×</button>
              </div>
            ))}
          </section>
        )}
        {job?.state === "done" && job.report && <Quality job={job} refresh={refresh} />}
        {job?.state === "done" && job.has_model && info && <ExportPanel job={job} info={info} />}
        <section className="danger-zone">
          <button className="ghost danger" onClick={remove}>Delete capture…</button>
        </section>
      </aside>

      {showLog && job && <LogDialog job={job} onClose={() => setShowLog(false)} />}
    </>
  );
}

function Dimensions({ job }: { job: Job }) {
  const d = job.report!.model.dimensions_mm.map((v) => v * job.user_scale);
  return (
    <div className="dims" aria-label="Model dimensions">
      <span><i>X</i>{fmtMm(d[0])}</span><span><i>Y</i>{fmtMm(d[1])}</span><span><i>Z</i>{fmtMm(d[2])}</span>
    </div>
  );
}

function Pipeline({ job, onLog, refresh }: { job: Job; onLog: () => void; refresh: () => void }) {
  const stages = job.stages ?? [];
  return (
    <footer className="pipeline">
      <div className="pipeline-steps">
        {stages.map((s) => (
          <div key={s.name} className={`step step-${s.state}`}>
            <span className="step-bar" />
            <span className="step-label">{s.label}</span>
            <span className="muted small">{s.seconds != null ? fmtTime(s.seconds) : s.state === "running" ? "…" : ""}</span>
          </div>
        ))}
        {!stages.length && <span className="muted">{job.state === "queued" ? "Waiting in the queue…" : job.state}</span>}
      </div>
      <div className="pipeline-status">
        {active(job) && <span className="mono">{Math.round((job.progress ?? 0) * 100)}%</span>}
        <span className={job.state === "failed" ? "error-text" : "muted"}>
          {job.state === "failed" ? job.error ?? "Failed" : job.state === "cancelled" ? "Cancelled" : job.message}
        </span>
        <span className="right">
          <button className="ghost" onClick={onLog}>Log</button>
          {active(job) && <button className="ghost" onClick={async () => { await api.cancelJob(job.id); refresh(); }}>Cancel</button>}
        </span>
      </div>
      {active(job) && <div className="progress"><div style={{ width: `${(job.progress ?? 0) * 100}%` }} /></div>}
    </footer>
  );
}

function ProcessPanel({ capture, info, onStarted }: { capture: Capture; info: Info | null; onStarted: (j: Job) => void }) {
  const [quality, setQuality] = useState(capture.has_depth ? "draft" : "medium");
  const [isolate, setIsolate] = useState(capture.mode !== "space");
  const [closeBottom, setCloseBottom] = useState(true);
  const [advanced, setAdvanced] = useState(false);
  const [method, setMethod] = useState("auto");
  const [bar, setBar] = useState("100.0");
  const [error, setError] = useState<string | null>(null);
  const qualities = info?.presets.quality ?? [];
  const q = qualities.find((x) => x.name === quality);
  const needsTools = q?.method === "photo" && info && !info.tools.openmvs && method !== "lidar";

  const start = async () => {
    setError(null);
    try {
      const printScale = Number(bar) / 100;
      onStarted(await api.createJob(capture.id, {
        quality, method, isolate, close_bottom: closeBottom,
        print_scale: Number.isFinite(printScale) && printScale > 0.9 && printScale < 1.1 ? printScale : 1,
      }));
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <section>
      <div className="section-title">Process</div>
      <div className="segmented" role="radiogroup" aria-label="Quality">
        {qualities.map((x) => (
          <button key={x.name} role="radio" aria-checked={quality === x.name} className={quality === x.name ? "is-on" : ""} onClick={() => setQuality(x.name)}>{x.label}</button>
        ))}
      </div>
      <p className="hint">{q?.hint}</p>
      <label className="check"><input type="checkbox" checked={isolate} onChange={(e) => setIsolate(e.target.checked)} /> Isolate the object<span className="muted">crop, remove the surface it stands on</span></label>
      <label className="check"><input type="checkbox" checked={closeBottom} disabled={!isolate} onChange={(e) => setCloseBottom(e.target.checked)} /> Flat closed base<span className="muted">ready for 3D printing</span></label>
      <button className="link" onClick={() => setAdvanced(!advanced)} aria-expanded={advanced}>{advanced ? "Hide" : "Show"} advanced</button>
      {advanced && (
        <div className="advanced">
          <label>Method
            <select value={method} onChange={(e) => setMethod(e.target.value)}>
              <option value="auto">Automatic</option>
              <option value="lidar">LiDAR fusion</option>
              <option value="photo">Photogrammetry</option>
            </select>
          </label>
          <label>Mat calibration bar, measured (mm)
            <input value={bar} onChange={(e) => setBar(e.target.value)} inputMode="decimal" />
          </label>
        </div>
      )}
      {needsTools ? (
        <div className="notice">
          Photogrammetry needs OpenMVS (22 MB, downloaded once).
          <button className="button" disabled={info!.tools.installing} onClick={() => api.installTools()}>{info!.tools.installing ? "Installing…" : "Install OpenMVS"}</button>
        </div>
      ) : (
        <button className="button primary wide" onClick={start} disabled={!!capture.error}>Process</button>
      )}
      {error && <p className="error-text">{error}</p>}
    </section>
  );
}

function Quality({ job, refresh }: { job: Job; refresh: () => void }) {
  const r = job.report!;
  const dims = r.model.dimensions_mm;
  const [axis, setAxis] = useState(0);
  const [measured, setMeasured] = useState("");
  const source = { mat: "Scan mat", lidar: "LiDAR", none: "No reference" }[r.scale.source];
  const calibrated = Math.abs(job.user_scale - 1) > 1e-9;

  const apply = async () => {
    const m = Number(measured);
    if (!(m > 0)) return;
    await api.calibrate(job.id, m / dims[axis]);
    setMeasured("");
    refresh();
  };

  return (
    <section>
      <div className="section-title">Accuracy</div>
      <dl className="facts">
        <dt>Scale from</dt><dd><span className={`tag tag-${r.scale.source}`}>{calibrated ? "Calibrated by hand" : source}</span></dd>
        {r.scale.expected_error_mm != null && !calibrated && (<><dt>Expected error</dt><dd>± {r.scale.expected_error_mm} mm</dd></>)}
        {r.scale.mat && (<><dt>Mat fit</dt><dd>{r.scale.mat.tags} tags · {r.scale.mat.fit_rms_mm} mm</dd></>)}
        {r.alignment && (<><dt>Photos aligned</dt><dd>{r.alignment.registered} / {r.alignment.total} · {r.alignment.reprojection_px} px</dd></>)}
        <dt>Mesh</dt><dd>{r.model.faces.toLocaleString()} faces · {r.model.watertight ? "closed" : "open"}</dd>
      </dl>
      {r.warnings.map((w) => <p key={w} className="notice">{w}</p>)}
      <div className="calibrate">
        <span className="muted small">Calibrate with a caliper: measured size along</span>
        <div className="row">
          <select value={axis} onChange={(e) => setAxis(Number(e.target.value))} aria-label="Axis">
            {["X", "Y", "Z"].map((a, i) => <option key={a} value={i}>{a} ({fmtMm(dims[i] * job.user_scale)})</option>)}
          </select>
          <input placeholder="mm" value={measured} onChange={(e) => setMeasured(e.target.value)} inputMode="decimal" aria-label="Measured millimetres" />
          <button className="button" onClick={apply} disabled={!(Number(measured) > 0)}>Apply</button>
        </div>
        {calibrated && <button className="link" onClick={async () => { await api.calibrate(job.id, 1); refresh(); }}>Reset calibration (×{job.user_scale.toFixed(4)})</button>}
      </div>
    </section>
  );
}

function ExportPanel({ job, info }: { job: Job; info: Info }) {
  const profiles = info.presets.profiles;
  const [profile, setProfile] = useState(profiles[0]?.name ?? "print");
  const p = useMemo(() => profiles.find((x) => x.name === profile)!, [profiles, profile]);
  const [formats, setFormats] = useState<string[]>(p?.formats ?? []);
  const [units, setUnits] = useState(p?.units ?? "mm");
  const [result, setResult] = useState<ExportResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { if (p) { setFormats([...p.formats]); setUnits(p.units); } }, [p]);
  const toggle = (f: string) => setFormats((cur) => (cur.includes(f) ? cur.filter((x) => x !== f) : [...cur, f]));

  const run = async () => {
    setBusy(true);
    setError(null);
    try { setResult(await api.exportJob(job.id, formats, units, p.textured)); }
    catch (e) { setError((e as Error).message); }
    setBusy(false);
  };

  return (
    <section>
      <div className="section-title">Export</div>
      <div className="chips">
        {profiles.map((x) => <button key={x.name} className={profile === x.name ? "is-on" : ""} onClick={() => setProfile(x.name)} title={x.hint}>{x.label}</button>)}
      </div>
      <p className="hint">{p?.hint}</p>
      <div className="formats">
        {info.presets.formats.map((f) => (
          <label key={f} className="check inline"><input type="checkbox" checked={formats.includes(f)} onChange={() => toggle(f)} /> {f.toUpperCase()}</label>
        ))}
        <select value={units} onChange={(e) => setUnits(e.target.value)} aria-label="Units">
          {info.presets.units.map((u) => <option key={u}>{u}</option>)}
        </select>
      </div>
      <button className="button primary wide" onClick={run} disabled={busy || !formats.length}>{busy ? "Exporting…" : "Export"}</button>
      {error && <p className="error-text">{error}</p>}
      {result && (
        <div className="exports">
          {result.files.map((f) => (
            <a key={f.file} href={api.exportUrl(job.id, f.file)} download>{f.file}<span className="muted right">{(f.size / 1e6).toFixed(1)} MB</span></a>
          ))}
          <button className="link" onClick={() => api.reveal(job.id)}>Open export folder</button>
        </div>
      )}
    </section>
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
      <div className="dialog" role="dialog" aria-label="Processing log" onClick={(e) => e.stopPropagation()}>
        <header><strong>Processing log</strong><button className="ghost icon" onClick={onClose} aria-label="Close">×</button></header>
        <pre>{text}</pre>
      </div>
    </div>
  );
}
