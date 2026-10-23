import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const $ = (id) => document.getElementById(id);
const state = { last_activity: 0, blender: null, viewport: null, model: null, gallery: [] };
let clockSkew = 0; // server time minus browser time, in seconds
const now = () => Date.now() / 1000 + clockSkew;

// ------------------------------------------------------------ live connection

// The page disconnects from the live feed while its tab is hidden, and reloads state on return.
let es = null;
function connect() {
  if (es) return;
  es = new EventSource("/events");
  es.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "hello") {
      clockSkew = msg.now - Date.now() / 1000;
      $("feed").replaceChildren();
      toolRows.clear();
      applyState(msg.state, true);
      addEvents(msg.events, true);
    } else if (msg.type === "state") applyState(msg.state);
    else if (msg.type === "events") addEvents(msg.events);
    else if (msg.type === "reset") { $("feed").replaceChildren(); toolRows.clear(); }
    setLive(true);
  };
  es.onerror = () => setLive(false); // EventSource retries on its own
}
function disconnect() {
  if (es) { es.close(); es = null; }
  setLive(false);
}
// Tell the server whether this tab is visible. Blender only captures frames while some tab is.
const tabId = Math.random().toString(36).slice(2);
function presence(visible) {
  const body = JSON.stringify({ tab: tabId, visible });
  if (!visible && navigator.sendBeacon) navigator.sendBeacon("/api/presence", body);
  else fetch("/api/presence", { method: "POST", body, keepalive: true }).catch(() => {});
}
setInterval(() => { if (!document.hidden) presence(true); }, 10000);
addEventListener("pagehide", () => presence(false));
document.addEventListener("visibilitychange", () => {
  presence(!document.hidden);
  if (document.hidden) disconnect();
  else connect();
  requestRender();
});

function setLive(on) {
  $("live").textContent = on ? "LIVE" : "OFFLINE";
  $("live").classList.toggle("on", on);
}

function applyState(changes, first = false) {
  Object.assign(state, changes);
  if ("session" in changes && changes.session) $("session").textContent = `session ${changes.session.name}`;
  if ("step" in changes) { $("step").hidden = !changes.step; $("step").textContent = changes.step || ""; }
  if ("viewport" in changes && changes.viewport) showViewport(changes.viewport);
  if ("viewport_enabled" in changes) {
    $("vp-toggle").classList.toggle("on", changes.viewport_enabled);
    $("vp-toggle").textContent = changes.viewport_enabled ? "Live: on" : "Live: off";
  }
  if ("model" in changes) loadModel(changes.model);
  if ("gallery" in changes) showGallery(changes.gallery, first);
  tick();
}

// --------------------------------------------------------------- status chips

function ago(t) {
  if (!t) return "";
  const s = Math.max(0, Math.round(now() - t));
  if (s < 5) return "just now";
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  return `${Math.round(s / 3600)}h ago`;
}

function tick() {
  const claudeOn = now() - (state.last_activity || 0) < 90;
  $("chip-claude").classList.toggle("on", claudeOn);
  $("chip-claude").lastElementChild.textContent = claudeOn ? "Claude working" : "Claude idle";
  const bt = state.blender?.t || 0;
  const blenderOn = now() - bt < 12;
  $("chip-blender").classList.toggle("on", blenderOn);
  $("chip-blender").lastElementChild.textContent = blenderOn ? "Blender connected" : "Blender not streaming";
  if (state.viewport) {
    $("vp-age").textContent = ago(state.viewport.t);
    $("vp-age").classList.toggle("stale", !blenderOn);
  }
  if (state.model) $("model-age").textContent = `${state.model.name} · ${ago(state.model.mtime)}`;
  $("feed-state").textContent = claudeOn ? "streaming" : (state.last_activity ? `last ${ago(state.last_activity)}` : "");
}
setInterval(tick, 1000);

// ------------------------------------------------------------------- viewport

function showViewport(vp) {
  const img = $("vp-img");
  const next = new Image();
  next.onload = () => { img.src = next.src; $("vp-empty").hidden = true; };
  next.src = `/viewport.jpg?t=${vp.t}`;
  const bits = [vp.mode, vp.frame && `frame ${vp.frame}`, vp.file].filter(Boolean);
  $("vp-badge").hidden = !bits.length;
  $("vp-badge").textContent = bits.join(" · ");
}

$("vp-toggle").onclick = () => {
  fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ viewport_enabled: !state.viewport_enabled }),
  });
};

// ----------------------------------------------------------------------- feed

const toolRows = new Map();
const feed = $("feed");
let stickToBottom = true;
feed.addEventListener("scroll", () => {
  stickToBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 40;
  if (stickToBottom) $("jump").hidden = true;
});
$("jump").onclick = () => { feed.scrollTop = feed.scrollHeight; };

function hhmmss(ts) {
  const d = ts ? new Date(ts) : new Date();
  return isNaN(d) ? "" : d.toLocaleTimeString([], { hour12: false });
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

function addEvents(events, initial = false) {
  for (const ev of events) {
    if (ev.kind === "note" || ev.kind === "user") {
      feed.append(el("div", ev.kind === "user" ? "note user" : "note", ev.text));
    } else if (ev.kind === "tool") {
      const row = el("div", "e");
      row.append(el("span", "t", hhmmss(ev.ts)), el("span", "k", ev.tool), document.createTextNode(ev.text || ""));
      feed.append(row);
      if (ev.id) { // logged tool calls get a ✓ or ✗ when their result arrives
        const mark = el("span", "pending", " …");
        row.append(mark);
        toolRows.set(ev.id, { row, mark });
      }
    } else if (ev.kind === "result") {
      const hit = toolRows.get(ev.id);
      if (hit) {
        hit.mark.className = ev.error ? "bad" : "ok";
        hit.mark.textContent = ev.error ? " ✗" : " ✓";
        if (ev.error) hit.row.classList.add("err");
        toolRows.delete(ev.id);
      }
    }
  }
  while (feed.childElementCount > 400) feed.firstElementChild.remove();
  if (stickToBottom || initial) feed.scrollTop = feed.scrollHeight;
  else if (events.length) $("jump").hidden = false;
}

// -------------------------------------------------------------------- gallery

let seenGallery = null;
function showGallery(items, first) {
  const box = $("gallery");
  box.replaceChildren();
  $("gal-count").textContent = items.length ? `${items.length} images · newest first` : "";
  if (!items.length) {
    box.append(el("div", "empty", "Renders, concept art and animation strips show up here."));
    return;
  }
  const before = seenGallery;
  seenGallery = new Set(items.map((i) => i.url));
  for (const it of items) {
    const fig = el("figure");
    const img = el("img");
    img.loading = "lazy";
    img.src = it.url;
    img.alt = it.name;
    fig.append(img, el("figcaption", null, `${it.name} · ${ago(it.mtime)}`));
    if (before && !first && !before.has(it.url)) fig.classList.add("new");
    fig.onclick = () => openLightbox(it);
    box.append(fig);
  }
}

function openLightbox(it) {
  const d = $("lightbox");
  d.querySelector("img").src = it.url;
  d.querySelector("p").textContent = it.name;
  d.showModal();
}
$("lightbox").onclick = () => $("lightbox").close();

// --------------------------------------------------------------- 3D model view

const canvas = $("model-canvas");
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false });
renderer.setPixelRatio(1); // full-res on hi-dpi screens costs 2-4x the GPU for little gain here
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.setClearColor(0x1a211f);

const scene = new THREE.Scene();
scene.add(new THREE.HemisphereLight(0xdfeee6, 0x2a2622, 1.6));
const sun = new THREE.DirectionalLight(0xffffff, 2.2);
sun.position.set(3, 6, 4);
scene.add(sun);
const rim = new THREE.DirectionalLight(0x9fd6ff, 0.8);
rim.position.set(-4, 3, -5);
scene.add(rim);

const camera = new THREE.PerspectiveCamera(35, 1, 0.01, 1000);
const controls = new OrbitControls(camera, canvas);
controls.enableDamping = true;

const loader = new GLTFLoader();
const clock = new THREE.Clock();
let current = null; // { root, mixer, clips, url, name, size, center }
let activeClip = null;
let camMode = "free";
let loadingUrl = null;
let paused = false;
try { paused = localStorage.getItem("studio.paused") === "1"; } catch {}
function setPaused(p) {
  paused = p;
  $("anim-pause").textContent = p ? "▶ Play" : "❚❚ Pause";
  $("anim-pause").classList.toggle("on", !p);
  try { localStorage.setItem("studio.paused", p ? "1" : "0"); } catch {}
  requestRender();
}
$("anim-pause").onclick = () => setPaused(!paused);

function resize() {
  const r = $("model-stage").getBoundingClientRect();
  renderer.setSize(r.width, r.height, false);
  requestRender();
  camera.aspect = r.width / Math.max(1, r.height);
  camera.updateProjectionMatrix();
}
new ResizeObserver(resize).observe($("model-stage"));

function frame(obj) {
  // precise=true measures skinned meshes in their posed shape, not the raw bind data
  obj.updateMatrixWorld(true);
  const box = new THREE.Box3().setFromObject(obj, true);
  const size = box.getSize(new THREE.Vector3()).length() || 1;
  const center = box.getCenter(new THREE.Vector3());
  return { size, center };
}

function setCamera(mode) {
  camMode = mode;
  $("cam-game").classList.toggle("on", mode === "game");
  $("cam-free").classList.toggle("on", mode === "free");
  if (!current) return;
  const { size, center } = current;
  // distance that fits the whole model (size is the bounding box diagonal)
  const fit = (fov) => (size * 0.5) / Math.tan(THREE.MathUtils.degToRad(fov / 2)) * 1.05;
  if (mode === "game") {
    // League looks down at roughly 56 degrees with a narrow lens. This is an approximation.
    camera.fov = 26;
    const pitch = THREE.MathUtils.degToRad(56);
    const dist = fit(camera.fov);
    camera.position.set(center.x, center.y + Math.sin(pitch) * dist, center.z + Math.cos(pitch) * dist);
    controls.enableRotate = false;
  } else {
    camera.fov = 35;
    const dir = new THREE.Vector3(0.55, 0.3, 0.75).normalize().multiplyScalar(fit(camera.fov));
    camera.position.copy(center).add(dir);
    controls.enableRotate = true;
  }
  camera.near = size / 100;
  camera.far = size * 100;
  camera.updateProjectionMatrix();
  controls.target.copy(center);
  controls.update();
  requestRender();
}
$("cam-game").onclick = () => setCamera("game");
$("cam-free").onclick = () => setCamera("free");

function pickDefaultClip(clips) {
  for (const re of [/run/i, /idle/i]) {
    const c = clips.find((c) => re.test(c.name));
    if (c) return c.name;
  }
  return clips[0]?.name;
}

function playClip(name) {
  if (!current) return;
  activeClip = name;
  current.mixer.stopAllAction();
  const clip = current.clips.find((c) => c.name === name);
  if (clip) current.mixer.clipAction(clip).reset().play();
  for (const b of $("anims").children) b.classList.toggle("on", b.dataset.clip === name);
  if (paused) { current.mixer.update(0); }
  requestRender();
}

function clipLabels(clips) {
  // "kogmaw_run", "kogmaw_idle1" -> "run", "idle1" (only a prefix that every clip shares)
  const names = clips.map((c) => c.name.split("|").pop());
  const pre = names.length > 1 ? names[0].match(/^[^_]+_/)?.[0] : null;
  const strip = pre && names.every((n) => n.startsWith(pre) && n.length > pre.length);
  return names.map((n) => (strip ? n.slice(pre.length) : n));
}

function loadModel(model) {
  if (!model) return;
  if (current && current.url === model.url) return;
  if (loadingUrl === model.url) return;
  loadingUrl = model.url;
  loader.load(
    model.url,
    (gltf) => {
      if (loadingUrl !== model.url) return; // a newer model arrived meanwhile
      const sameName = current && current.name === model.name;
      const keepCam = sameName ? { pos: camera.position.clone(), target: controls.target.clone() } : null;
      if (current) scene.remove(current.root);
      const root = gltf.scene;
      scene.add(root);
      const mixer = new THREE.AnimationMixer(root);
      current = { root, mixer, clips: gltf.animations, url: model.url, name: model.name };
      $("model-empty").hidden = true;

      const tabs = $("anims");
      tabs.replaceChildren();
      const labels = clipLabels(gltf.animations);
      for (const [i, c] of gltf.animations.entries()) {
        const b = el("button", null, labels[i]);
        b.dataset.clip = c.name;
        b.title = c.name;
        b.onclick = () => playClip(c.name);
        tabs.append(b);
      }
      const keep = activeClip && gltf.animations.some((c) => c.name === activeClip);
      if (gltf.animations.length) playClip(keep ? activeClip : pickDefaultClip(gltf.animations));
      mixer.update(0);
      Object.assign(current, frame(root));
      requestRender();

      if (keepCam && camMode === "free") {
        camera.position.copy(keepCam.pos);
        controls.target.copy(keepCam.target);
        controls.update();
      } else setCamera(camMode);
    },
    undefined,
    (err) => { console.warn("model load failed", err); loadingUrl = null; },
  );
}

// Draw only when something moves: at most 30 fps while an animation plays, otherwise only
// after the camera moves, a model loads or the panel resizes. Paused or hidden = no GPU work.
let needsRender = true;
let lastDraw = 0;
function requestRender() { needsRender = true; }
controls.addEventListener("change", requestRender);
function loop(t) {
  requestAnimationFrame(loop);
  if (document.hidden) return; // browsers mostly stop this loop in hidden tabs anyway
  const animating = current && activeClip && !paused;
  const damping = controls.update(); // true while the camera is still gliding
  if (!(animating || needsRender || damping)) { clock.getDelta(); return; }
  if (animating && t - lastDraw < 1000 / 30 && !needsRender) return;
  lastDraw = t;
  const dt = clock.getDelta();
  if (animating) current.mixer.update(Math.min(dt, 0.1));
  renderer.render(scene, camera);
  needsRender = false;
}
requestAnimationFrame(loop);

setPaused(paused);
if (!document.hidden) { connect(); presence(true); }
