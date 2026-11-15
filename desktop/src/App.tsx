import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Capture, type ChecklistData, type ExportResult, type Info, type Job } from "./api";
import type * as THREE from "three";
import { Viewer, circleThrough, type ViewMode } from "./Viewer";
import type { Fit } from "./fit";

type MeasureKind = "distance" | "diameter" | "cylinder" | "plane";
const PICKS: Record<MeasureKind, number> = { distance: 2, diameter: 3, cylinder: 1, plane: 1 };

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
  const [view, setView] = useState<"captures" | "checklist">("captures");

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
        <button className={`side-link ${view === "checklist" ? "is-selected" : ""}`} onClick={() => setView(view === "checklist" ? "captures" : "checklist")}>
          <span aria-hidden="true">☑</span> Field test
        </button>
        <div className="label">Captures <span className="count">{captures.length || ""}</span></div>
        <nav className="capture-list" aria-label="Captures">
          {captures.map((c) => (
            <button key={c.id} className={`capture ${c.id === selected && view === "captures" ? "is-selected" : ""}`} onClick={() => { setSelected(c.id); setView("captures"); }}>
              <img src={api.previewUrl(c.id)} alt="" width={48} height={36} loading="lazy" />
              <span className="capture-text">
                <span className="capture-name">{c.name}</span>
                <span className="muted small">{c.error ? "Unreadable capture" : `${c.frames}${NBSP}photos · ${c.size_mb.toFixed(0)}${NBSP}MB`}</span>
              </span>
              <CaptureStatus capture={c} />
            </button>
          ))}
          {!captures.length && !offline && <p className="muted pad">No captures yet. Send one from the phone, import photos or a video, or create a demo capture.</p>}
        </nav>
        {!offline && <ImportButton onImported={(id) => { refresh(); setSelected(id); setView("captures"); }} />}
        <SidebarFooter info={info} offline={offline} />
      </aside>

      {view === "checklist" && !offline ? (
        <FieldTest />
      ) : offline ? (
        <main className="empty">
          <h1>Starting the Engine…</h1>
          <p className="muted">The first start takes a few seconds. This page connects by itself. If it stays here, close Calco and open it again.</p>
        </main>
      ) : capture ? (
        <CaptureView key={capture.id} capture={capture} captures={captures} info={info} theme={theme} refresh={refresh} onDeleted={() => setSelected(null)} />
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

/** Photos or videos from any camera become a capture (no LiDAR, no poses: scale from the mat or a caliper). */
function ImportButton({ onImported }: { onImported: (captureId: string) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const upload = async (files: File[]) => {
    if (!files.length) return;
    setError(null);
    const iid = crypto.randomUUID();
    try {
      for (const [i, f] of files.entries()) {
        setBusy(`Uploading ${i + 1}/${files.length}`);
        await api.importFile(iid, f);
      }
      setBusy(files.some((f) => f.type.startsWith("video/")) ? "Picking sharp video frames…" : "Creating capture…");
      const { capture } = await api.importComplete(iid, null);
      onImported(capture.id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
      if (input.current) input.current.value = "";
    }
  };
  return (
    <div className="import">
      <input ref={input} type="file" multiple accept="image/jpeg,image/png,image/tiff,image/webp,video/*" hidden
        onChange={(e) => upload([...(e.target.files ?? [])])} />
      <button className="button wide" disabled={!!busy} onClick={() => input.current?.click()}>
        {busy ?? "Import Photos or Video…"}
      </button>
      {error && <p className="error-text small" aria-live="polite">{error}</p>}
    </div>
  );
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
          <div className="label">3D print (3MF for AMS + STL, two colours)</div>
          <div className="menu-row">
            {["A5", "A4"].map((s) => <a key={s} className="button" href={api.mat3dUrl(s)}>{s}</a>)}
          </div>
          <div className="label">Big parts: loose markers, 60 mm</div>
          <div className="menu-row"><a className="button" href={api.markersUrl()}>Markers PDF</a></div>
          <p className="muted small">Place 3 or more around the part, flat, where the photos see them.</p>
          <p className="muted small">Glue paper mats to something rigid and flat. A curled mat still gives the right scale, a flat one is more accurate.</p>
        </div>
      </details>
      {info && <PhoneCard info={info} />}
      <div className="status-line">
        <span className={`pip ${offline ? "pip-failed" : "pip-done"}`} aria-hidden="true" />
        <span>{offline ? "Engine offline" : "Engine ready"}</span>
        {info && <span className="muted right mono">v{info.version}</span>}
      </div>
      {info && (
        <dl className="kv">
          <dt>GPU</dt>
          <dd>{info.tools.gpu ? (info.tools.colmap_cuda ? "In use" : "Not used yet") : "None found"}</dd>
        </dl>
      )}
    </div>
  );
}

const LINK_LABEL = { cable: "Cable", wifi: "Wi-Fi", "wifi-sync": "Wi-Fi sync" } as const;

/** Network code + password the phone needs to connect (shown only on this computer). */
function PairingCodes() {
  const [data, setData] = useState<{ codes: { address: string; code: string }[]; pin: string } | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => { api.pairing().then(setData).catch(() => setError(true)); }, []);
  if (error) return <div className="error-text small">Pairing details unavailable.</div>;
  if (!data) return null;
  const regenerate = async () => {
    if (!confirm("Make a new password? Phones paired with the old one will have to type the new one.")) return;
    const { pin } = await api.newPin();
    setData({ ...data, pin });
  };
  return (
    <dl className="pairing">
      <dt>Network code</dt>
      <dd className="mono">{data.codes.length ? data.codes.map((c) => <span key={c.code} title={c.address}>{c.code}</span>) : "no network"}</dd>
      <dt>Password</dt>
      <dd className="mono">
        {data.pin.slice(0, 3)}{NBSP}{data.pin.slice(3)}
        <button className="link small" onClick={regenerate}>New</button>
      </dd>
    </dl>
  );
}

/** How the phone is connected, what it is sending, and why Wi-Fi may not work. */
function PhoneCard({ info }: { info: Info }) {
  const p = info.phone;
  const [help, setHelp] = useState(false);
  const t = p.transfer;
  const pct = t && t.total ? Math.round(((t.done ?? 0) / t.total) * 100) : null;
  const wifiBlocked = info.network.checked && !info.network.ok;
  return (
    <section className={`phone-card ${p.connected ? "is-on" : ""}`} aria-live="polite">
      <div className="phone-row">
        <span className={`pip ${p.connected ? (t ? "pip-running" : "pip-done") : ""}`} aria-hidden="true" />
        <strong className="phone-name">{p.connected ? p.name : "No phone connected"}</strong>
        {p.connected && p.link && <span className="tag tag-mat">{LINK_LABEL[p.link]}</span>}
      </div>
      {p.connected && t && (
        <>
          <div className="muted small phone-line">
            {t.capture ? `Receiving “${t.capture}”${t.count && t.count > 1 ? ` · ${t.index}/${t.count}` : ""}` : "Receiving a scan…"}
            {pct != null && <span className="mono right">{pct}%</span>}
          </div>
          {pct != null && <div className="phone-bar"><div style={{ width: `${pct}%` }} /></div>}
        </>
      )}
      {p.connected && !t && <div className="muted small">{p.error ? <span className="error-text">{p.error}</span> : "Up to date. New scans arrive by themselves."}</div>}
      {(!p.connected || p.error === "not paired") && (
        <>
          <div className="muted small">
            {p.error === "not paired"
              ? "The iPhone is plugged in but not paired: on the phone open Settings → Connect and type:"
              : "On the iPhone open Settings → Connect and type:"}
          </div>
          <PairingCodes />
          {p.usb_service === false && <div className="error-text small">Cable transfer needs Apple Devices or iTunes installed.</div>}
        </>
      )}
      {wifiBlocked && (
        <button className="warn-line" onClick={() => setHelp(true)}>
          <span aria-hidden="true">!</span> Wi-Fi transfer blocked by Windows · How to fix
        </button>
      )}
      {help && <WifiHelp info={info} onClose={() => setHelp(false)} />}
    </section>
  );
}

function WifiHelp({ info, onClose }: { info: Info; onClose: () => void }) {
  // full paths: an administrator PowerShell starts in System32, not in the Calco folder
  const script = info.network.fix_script ?? "tools\\allow-wifi.ps1";
  const cmd = `powershell -ExecutionPolicy Bypass -File "${script}"` + (info.network.python ? ` -Python "${info.network.python}"` : "");
  useEffect(() => {
    const key = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    addEventListener("keydown", key);
    return () => removeEventListener("keydown", key);
  }, [onClose]);
  return (
    <div className="dialog-backdrop" onClick={onClose}>
      <div className="dialog dialog-small" role="dialog" aria-modal="true" aria-label="Allow Wi-Fi transfers" onClick={(e) => e.stopPropagation()}>
        <header><strong>Allow Wi-Fi Transfers</strong><button className="ghost icon" onClick={onClose} aria-label="Close"><span aria-hidden="true">×</span></button></header>
        <div className="dialog-body">
          <p>Windows is refusing connections from the phone:</p>
          <ul>{(info.network.problems ?? []).map((x) => <li key={x}>{x}</li>)}</ul>
          <p><strong>The USB cable always works</strong>, without changing anything. To use Wi-Fi as well:</p>
          <button className="button primary wide" onClick={() => { api.fixNetwork(); onClose(); }}>Fix It</button>
          <p className="muted small">Windows asks for permission (administrator), then Calco marks your network as Private, removes the rule that blocks it and allows it on private networks only. The warning disappears within a minute.</p>
          <details className="muted small">
            <summary>Do it by hand instead</summary>
            <p>Run in PowerShell as administrator (the script is short and commented):</p>
            <pre>{cmd}</pre>
            <button className="button" onClick={() => navigator.clipboard?.writeText(cmd)}>Copy Command</button>
          </details>
          <button className="link" onClick={() => { api.recheckNetwork(); onClose(); }}>Check Again</button>
        </div>
      </div>
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

function CaptureView({ capture, captures, info, theme, refresh, onDeleted }: {
  capture: Capture; captures: Capture[]; info: Info | null; theme: string; refresh: () => void; onDeleted: () => void;
}) {
  const jobs = capture.jobs;
  const [jobId, setJobId] = useState<string | null>(null);
  const job = jobs.find((j) => j.id === jobId) ?? jobs.find(active) ?? jobs.find((j) => j.state === "done") ?? jobs[0] ?? null;
  const [mode, setMode] = useState<ViewMode>("textured");
  const [grid, setGrid] = useState(true);
  const [measuring, setMeasuring] = useState(false);
  const [points, setPoints] = useState<THREE.Vector3[]>([]);
  const [kind, setKind] = useState<MeasureKind>("distance");
  const [fit, setFit] = useState<Fit | null | undefined>(undefined); // undefined = nothing clicked yet
  const [showLog, setShowLog] = useState(false);
  const hasModel = !!job?.has_model && job.state === "done";
  // results processed without colour texture are plain white: show them as clay, no Texture button
  const coloured = job?.report?.model.textured ?? job?.options.texture !== false;
  const hasSplat = !!job?.report?.model.splat;
  const view: ViewMode = (!coloured && mode === "textured") || (!hasSplat && mode === "splat") ? "clay" : mode;
  const [tab, setTab] = useState<Tab>(hasModel ? "result" : "process");

  // show the result as soon as a job finishes
  const lastState = useRef(job?.state);
  useEffect(() => {
    if (lastState.current && lastState.current !== "done" && job?.state === "done") setTab("result");
    lastState.current = job?.state;
  }, [job?.state]);
  useEffect(() => { setMeasuring(false); setPoints([]); setFit(undefined); }, [job?.id]);

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
              <Viewer url={api.modelUrl(job)} splatUrl={hasSplat ? api.splatUrl(job) : null} mode={view} grid={grid} theme={theme} measuring={measuring} picks={PICKS[kind]} onMeasure={setPoints}
                fitKind={kind === "cylinder" || kind === "plane" ? kind : null} onFit={setFit} />
              <div className="toolbar" role="toolbar" aria-label="View">
                {([...(hasSplat ? ["splat"] : []), ...(coloured ? ["textured"] : []), "clay", "wire"] as ViewMode[]).map((m) => (
                  <button key={m} aria-pressed={view === m} onClick={() => setMode(m)}>{{ splat: "Splat", textured: "Texture", clay: "Clay", wire: "Wire" }[m]}</button>
                ))}
                <span className="sep" aria-hidden="true" />
                <button aria-pressed={grid} onClick={() => setGrid(!grid)}>Grid</button>
                <button aria-pressed={measuring} onClick={() => { setMeasuring(!measuring); setPoints([]); setFit(undefined); }}>Measure</button>
              </div>
              {measuring
                ? <MeasureBar job={job} points={points} kind={kind} fit={fit} setKind={(k) => { setKind(k); setPoints([]); setFit(undefined); }} refresh={refresh} />
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
          {tab === "process" && <ProcessPanel capture={capture} captures={captures} info={info} onStarted={(j) => { setJobId(j.id); refresh(); }} />}
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
function MeasureBar({ job, points, kind, fit, setKind, refresh }: {
  job: Job; points: THREE.Vector3[]; kind: MeasureKind; fit: Fit | null | undefined; setKind: (k: MeasureKind) => void; refresh: () => void;
}) {
  const [real, setReal] = useState("");
  const circle = kind === "diameter" && points.length === 3 ? circleThrough(points[0], points[1], points[2]) : null;
  const cyl = kind === "cylinder" && fit?.kind === "cylinder" ? fit : null;
  const plane = kind === "plane" && fit?.kind === "plane" ? fit : null;
  // millimetres before calibration: a distance, or a diameter
  const raw = kind === "distance" && points.length === 2 ? points[0].distanceTo(points[1]) * 1000
    : circle ? circle.radius * 2000 : cyl ? cyl.radius * 2000 : null;
  const hint = { distance: "Click two points on the model", diameter: "Click three points on the edge of a hole or shaft",
    cylinder: "Click on the side of a shaft or inside a hole", plane: "Click on a flat face" }[kind];
  const apply = async () => {
    const m = Number(real.replace(",", "."));
    if (!(m > 0) || !raw) return;
    await api.calibrate(job.id, m / raw);
    setReal("");
    refresh();
  };
  return (
    <div className="readout readout-measure">
      <span className="segmented" role="group" aria-label="Measure">
        {(["distance", "diameter", "cylinder", "plane"] as MeasureKind[]).map((k) => (
          <button key={k} aria-pressed={kind === k} onClick={() => setKind(k)}>{k[0].toUpperCase() + k.slice(1)}</button>
        ))}
      </span>
      {plane ? (
        <span><i>Flatness</i>{fmtMm(plane.rms * 1000 * job.user_scale)} RMS · <i>Tilt</i>{plane.tiltDeg.toFixed(1)}°</span>
      ) : fit === null ? (
        <span className="muted">No clear {kind} there: click closer to its middle</span>
      ) : raw == null ? (
        <span className="muted">{hint}</span>
      ) : (
        <>
          <span><i>{kind === "distance" ? "Distance" : "Diameter"}</i>{fmtMm(raw * job.user_scale)}
            {cyl && <span className="muted"> · fit ±{(cyl.rms * 1000 * job.user_scale).toFixed(2)}</span>}</span>
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
        {job.state === "running" && <JobClock job={job} />}
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

/** The field test sheet (docs/collaudo.md) with ticks and notes saved on this computer. */
function FieldTest() {
  const [data, setData] = useState<ChecklistData | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => { api.checklist().then(setData).catch(() => setError(true)); }, []);
  if (error) return <main className="empty"><h1>Field Test</h1><p className="muted">The test sheet could not be loaded.</p></main>;
  if (!data) return <main className="empty"><p className="muted">Loading…</p></main>;
  const items = data.sections.flatMap((s) => s.items.filter((i) => i.id));
  const done = items.filter((i) => data.state[i.id!]?.done).length;
  const update = (id: string, patch: { done?: boolean; note?: string }) => {
    setData({ ...data, state: { ...data.state, [id]: Object.assign({ done: false, note: "" }, data.state[id], patch) } });
    api.check(id, patch);
  };
  return (
    <main className="checklist">
      <header className="checklist-head">
        <h1>Field Test</h1>
        <span className="mono">{done}/{items.length} done</span>
        <div className="checklist-bar"><div style={{ width: `${(done / Math.max(items.length, 1)) * 100}%` }} /></div>
      </header>
      <div className="checklist-body">
        {data.sections.map((s) => {
          const own = s.items.filter((i) => i.id);
          const ok = own.filter((i) => data.state[i.id!]?.done).length;
          return (
            <section key={s.title}>
              <h2>{s.title} <span className="muted mono small">{ok}/{own.length}</span></h2>
              {s.items.map((i, k) => i.heading ? <h3 key={k}>{i.text}</h3> : (
                <CheckRow key={i.id} text={i.text} entry={data.state[i.id!]} onChange={(p) => update(i.id!, p)} />
              ))}
            </section>
          );
        })}
      </div>
    </main>
  );
}

function CheckRow({ text, entry, onChange }: { text: string; entry?: { done: boolean; note: string }; onChange: (p: { done?: boolean; note?: string }) => void }) {
  const [note, setNote] = useState(entry?.note ?? "");
  const [open, setOpen] = useState(!!entry?.note);
  return (
    <div className={`check-row ${entry?.done ? "is-done" : ""}`}>
      <label>
        <input type="checkbox" checked={!!entry?.done} onChange={(e) => onChange({ done: e.target.checked })} />
        <span>{richText(text)}</span>
      </label>
      {open ? (
        <input className="check-note" name="note" autoComplete="off" placeholder="Note…" value={note}
          onChange={(e) => setNote(e.target.value)} onBlur={() => onChange({ note })} />
      ) : (
        <button className="link small" onClick={() => setOpen(true)}>Add note</button>
      )}
    </div>
  );
}

/** **bold** and `code` from the markdown sheet, without injecting HTML. */
function richText(text: string) {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, i) =>
    part.startsWith("**") ? <strong key={i}>{part.slice(2, -2)}</strong>
      : part.startsWith("`") ? <code key={i}>{part.slice(1, -1)}</code>
      : part.replace(/\*([^*]+)\*/g, "$1"));
}

/** First-start setup: components download by themselves; this only shows how far along it is. */
function SetupNotice({ info }: { info: Info }) {
  const p = info.tools.install_progress;
  const pct = p && p.total ? Math.round((p.done / p.total) * 100) : null;
  if (info.tools.install_error) {
    return (
      <div className="notice">
        Setup could not download a component: {info.tools.install_error}
        <button className="button" onClick={() => api.installTools()}>Try Again</button>
      </div>
    );
  }
  return (
    <div className="notice" aria-live="polite">
      <span>Setting up {p?.component ?? "components"}{pct != null ? ` · ${pct}%` : "…"} <span className="muted">(once, on the first start)</span></span>
      {pct != null && <div className="phone-bar"><div style={{ width: `${pct}%` }} /></div>}
    </div>
  );
}

/** Elapsed time, time left and expected finish, ticking every second between status updates. */
function JobClock({ job }: { job: Job }) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(t);
  }, []);
  if (!job.started) return null;
  const elapsed = Math.max(0, now - job.started);
  const clock = (s: number) => {
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = Math.floor(s % 60);
    return h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `${m}:${String(sec).padStart(2, "0")}`;
  };
  const left = job.eta_seconds;
  const end = left != null ? new Intl.DateTimeFormat(undefined, { timeStyle: "short" }).format(new Date((now + left) * 1000)) : null;
  return (
    <span className="job-clock mono" title="Elapsed · estimated time left · expected finish">
      {clock(elapsed)}
      {left != null ? <> · ~{fmtTime(left)} left · done ≈ {end}</> : <span className="muted"> · estimating…</span>}
    </span>
  );
}

function ProcessPanel({ capture, captures, info, onStarted }: { capture: Capture; captures: Capture[]; info: Info | null; onStarted: (j: Job) => void }) {
  const [quality, setQuality] = useState(capture.has_depth ? "draft" : "medium");
  const [isolate, setIsolate] = useState(capture.mode !== "space");
  const [closeBottom, setCloseBottom] = useState(true);
  const [texture, setTexture] = useState(true);
  const [dropBlurry, setDropBlurry] = useState(true);
  const [splat, setSplat] = useState(false);
  const [method, setMethod] = useState("auto");
  const [bar, setBar] = useState("100.0");
  const [error, setError] = useState<string | null>(null);
  const qualities = info?.presets.quality ?? [];
  const q = qualities.find((x) => x.name === quality);
  const photo = method === "photo" || (method === "auto" && q?.method === "photo");
  const needsOpenMVS = photo && info && !info.tools.openmvs;

  const start = async () => {
    setError(null);
    try {
      const printScale = Number(bar.replace(",", ".")) / 100;
      onStarted(await api.createJob(capture.id, {
        quality, method, isolate, close_bottom: closeBottom, texture, drop_blurry: dropBlurry, splat: splat && photo,
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
      <label className="check"><input type="checkbox" checked={texture} onChange={(e) => setTexture(e.target.checked)} /><span>Colour texture<small>Turn off for 3D printing: faster, geometry only</small></span></label>
      {photo && info && !info.tools.splat && info.tools.gpu && (
        <p className="tight"><button className="link small" disabled={info.tools.installing} onClick={() => api.installSplat()}>
          {info.tools.installing ? "Setting up…" : "Add Gaussian splat (one-time 3 GB download)"}</button></p>
      )}
      {photo && info?.tools.splat && <label className="check"><input type="checkbox" checked={splat} onChange={(e) => setSplat(e.target.checked)} /><span>Gaussian splat<small>Photoreal 3D view on the GPU, adds 10–40 min</small></span></label>}
      <label className="check"><input type="checkbox" checked={dropBlurry} onChange={(e) => setDropBlurry(e.target.checked)} /><span>Skip blurred photos<small>Leaves out shaky or out-of-focus shots</small></span></label>

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

      <p className="tight"><a className="link small" href={api.capturePhotosUrl(capture.id)} download>Only export the photos, with the phone's camera positions</a></p>
      {info && (info.tools.installing || info.tools.install_error) && <SetupNotice info={info} />}
      <button className="button primary wide" onClick={start} disabled={!!capture.error || !!needsOpenMVS}>
        {needsOpenMVS ? "Waiting for setup…" : "Process"}
      </button>
      {error && <p className="error-text" aria-live="polite">{error}</p>}
      <CombinePanel capture={capture} captures={captures} onStarted={onStarted} />
    </div>
  );
}

/** Two finished results of the same part (e.g. front and back) aligned by shape and fused into one. */
function CombinePanel({ capture, captures, onStarted }: { capture: Capture; captures: Capture[]; onStarted: (j: Job) => void }) {
  const done = (c: Capture) => c.jobs.filter((j) => j.state === "done" && j.has_model);
  const mine = done(capture);
  const others = captures.flatMap((c) => done(c).map((j) => ({ j, c })));
  const [a, setA] = useState(mine[0]?.id ?? "");
  const [b, setB] = useState("");
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!mine.length || others.length < 2) return null;
  const label = (j: Job, c: Capture) => `${c.name} · ${String(j.options.quality ?? "")}`;
  const start = async () => {
    setError(null);
    try { onStarted(await api.createJob(capture.id, { combine: [a, b] })); setOpen(false); }
    catch (e) { setError((e as Error).message); }
  };
  return (
    <div className="combine">
      <button className="link" onClick={() => setOpen(!open)} aria-expanded={open}>{open ? "Hide" : "Combine with another side…"}</button>
      {open && (
        <div className="combine-body">
          <p className="hint tight">For parts scanned twice, e.g. upright and turned over. The second is aligned to the first by its shape, then both are fused into one closed model.</p>
          <label className="field">This side
            <select name="side-a" value={a} onChange={(e) => setA(e.target.value)}>
              {mine.map((j) => <option key={j.id} value={j.id}>{label(j, capture)}</option>)}
            </select>
          </label>
          <label className="field">Other side
            <select name="side-b" value={b} onChange={(e) => setB(e.target.value)}>
              <option value="">Choose a result…</option>
              {others.filter(({ j }) => j.id !== a).map(({ j, c }) => <option key={j.id} value={j.id}>{label(j, c)}</option>)}
            </select>
          </label>
          <button className="button wide" onClick={start} disabled={!a || !b}>Combine Sides</button>
          {error && <p className="error-text">{error}</p>}
        </div>
      )}
    </div>
  );
}

function ResultPanel({ job, refresh }: { job: Job; refresh: () => void }) {
  const r = job.report!;
  const dims = r.model.dimensions_mm;
  const [axis, setAxis] = useState(0);
  const [measured, setMeasured] = useState("");
  const source = { mat: "Scan mat", markers: "Markers", lidar: "LiDAR", none: "No reference" }[r.scale.source];
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
        <dt>Scale from</dt><dd><span className={`tag tag-${calibrated || r.scale.source === "markers" ? "mat" : r.scale.source}`}>{calibrated ? "Caliper" : source}</span></dd>
        {r.scale.expected_error_mm != null && !calibrated && (<><dt>Expected error</dt><dd className="mono">±{NBSP}{r.scale.expected_error_mm}{NBSP}mm</dd></>)}
        {r.scale.mat && (<><dt>Mat fit</dt><dd className="mono">{r.scale.mat.tags}{NBSP}tags · {r.scale.mat.fit_rms_mm}{NBSP}mm</dd></>)}
        {r.scale.markers && (<><dt>Markers</dt><dd className="mono">{r.scale.markers.count} · spread {r.scale.markers.spread_pct}%</dd></>)}
        {r.alignment && (<><dt>Photos aligned</dt><dd className="mono">{r.alignment.registered}/{r.alignment.total} · {r.alignment.reprojection_px}{NBSP}px</dd></>)}
        {r.combined && (<><dt>Sides overlap</dt><dd className="mono">{r.combined.overlap_pct}%</dd><dt>Sides distance</dt><dd className="mono">{r.combined.distance_mm}{NBSP}mm</dd></>)}
        {r.photos && r.photos.dropped_blurry.length > 0 && (<><dt>Blurred, skipped</dt><dd className="mono">{r.photos.dropped_blurry.length}{NBSP}photos</dd></>)}
        {r.alignment?.phone_pose_rms_mm != null && (<><dt>Phone vs photos</dt><dd className="mono">{r.alignment.phone_pose_rms_mm}{NBSP}mm</dd></>)}
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

      <div className="label spaced">Photos for Another Program</div>
      <a className="button wide" href={api.jobPhotosUrl(job)} download>Photos + Cameras (ZIP)</a>
      <p className="hint tight">Original photos with the cameras refined here, lined up with this model: COLMAP, Nerfstudio/splatting (transforms.json), RealityScan (XMP), Metashape (CSV).</p>

      <div className="label spaced">Report</div>
      <a className="button wide" href={api.reportUrl(job)} target="_blank" rel="noreferrer">PDF Report</a>
      <p className="hint tight">One page: views, dimensions and accuracy. For a site visit or a customer.</p>
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
