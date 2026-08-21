const token = new URLSearchParams(location.search).get("token") || sessionStorage.getItem("robot-control-token") || "";
if (token) sessionStorage.setItem("robot-control-token", token);
if (location.search) history.replaceState({}, "", location.pathname);

const state = { data: null, profile: null, saved: null, section: "robot", expert: false, busy: false, query: "" };
const icons = { robot: "◇", run: "▶", surface: "▱", physics: "◉", motion: "↝", initialization: "⌖", randomization: "≋", actuators: "⌁", disturbance: "≈", rewards: "☆", ppo: "∿" };

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", "X-Control-Token": token, ...(options.headers || {}) },
  });
  const payload = await response.json();
  if (response.status === 401) {
    sessionStorage.removeItem("robot-control-token");
    throw new Error("This tab belongs to an expired local UI session. Reopen Robot Control Center once to reconnect it.");
  }
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
  return payload;
}

function clone(value) { return JSON.parse(JSON.stringify(value)); }
function getPath(object, path) { return path.split(".").reduce((value, part) => Array.isArray(value) ? value[Number(part)] : value[part], object); }
function setPath(object, path, value) {
  const parts = path.split(".");
  const leaf = parts.pop();
  const target = parts.reduce((current, part) => Array.isArray(current) ? current[Number(part)] : current[part], object);
  if (Array.isArray(target)) target[Number(leaf)] = value; else target[leaf] = value;
}
function changed() { return JSON.stringify(state.profile) !== JSON.stringify(state.saved); }

function renderNav() {
  const nav = document.querySelector("#section-nav");
  const sections = [{ id: "robot", title: "Robot setup" }, ...state.data.groups.map(group => ({ id: group.id, title: group.title, expert: group.expert }))];
  nav.innerHTML = sections.map(item => `<button class="nav-button ${item.id === state.section ? "active" : ""} ${item.expert && !state.expert ? "expert-hidden" : ""}" data-section="${item.id}"><span class="nav-icon">${icons[item.id] || "·"}</span>${item.title}</button>`).join("");
  nav.querySelectorAll("button").forEach(button => button.addEventListener("click", () => { state.section = button.dataset.section; render(); }));
}

function inputFor(field, value) {
  const expert = field.expert ? `<span class="expert-badge">Advanced</span>` : "";
  let control;
  if (field.type === "boolean") {
    control = `<label class="boolean-wrap"><input type="checkbox" data-path="${field.path}" ${value ? "checked" : ""}> Enabled</label>`;
  } else if (field.type === "select") {
    control = `<div class="input-wrap"><select data-path="${field.path}">${field.options.map(option => `<option ${option === value ? "selected" : ""}>${option}</option>`).join("")}</select></div>`;
  } else {
    const type = field.type === "text" ? "text" : "number";
    control = `<div class="input-wrap"><input type="${type}" data-path="${field.path}" value="${String(value).replaceAll('"', '&quot;')}" ${field.min !== undefined ? `min="${field.min}"` : ""} ${field.max !== undefined ? `max="${field.max}"` : ""} ${field.step !== undefined ? `step="${field.step}"` : ""}>${field.unit ? `<span class="unit">${field.unit}</span>` : ""}</div>`;
  }
  return `<article class="field-card ${field.expert && !state.expert ? "expert-hidden" : ""}"><label><span>${field.label}</span><span>${expert}<button class="help-button" type="button" title="Show explanation">?</button></span></label>${control}<p class="field-help">${field.description}</p></article>`;
}

function bindInputs(root = document) {
  root.querySelectorAll("[data-path]").forEach(input => input.addEventListener("input", event => {
    const element = event.currentTarget;
    let value = element.type === "checkbox" ? element.checked : element.value;
    if (element.type === "number") value = element.value === "" ? "" : Number(element.value);
    setPath(state.profile, element.dataset.path, value);
    if (element.dataset.path.startsWith("actuators.")) {
      const key = element.dataset.path.split(".")[1];
      if (["stiffness", "damping", "effort_limit", "velocity_limit", "armature"].includes(key)) {
        state.profile.robot.joints.forEach(joint => { joint[key] = value; });
      }
    }
    if (element.dataset.path === "training.stage") {
      state.profile.environment.surface = value === "V2Rough" ? "Mixed curriculum" : "Flat";
    }
    if (element.dataset.path === "environment.surface") {
      if (value !== "Flat") state.profile.training.stage = "V2Rough";
      else if (state.profile.training.stage === "V2Rough") state.profile.training.stage = "V2Core";
    }
    updateDirtyState();
  }));
  root.querySelectorAll(".help-button").forEach(button => button.addEventListener("click", () => button.closest(".field-card").classList.toggle("help-open")));
}

function renderRobot() {
  const robot = state.profile.robot;
  const panel = document.querySelector("#robot-panel");
  panel.innerHTML = `<div class="robot-overview">
    <div class="robot-card"><h3>${state.profile.display_name}</h3><p>${robot.ready_for_training ? (robot.asset_source === "Isaac Lab built-in" ? "Official 12-DOF Isaac Lab reference. Its exact profile can be deployed to the GB10." : "Validated custom 12-DOF robot. The saved profile is ready to deploy and train on the GB10.") : "12-DOF replacement template. Replace every placeholder and validate the USD/standing pose before enabling training."}</p>${robot.reference_task ? `<p class="reference-task">Reference task · ${robot.reference_task}</p>` : ""}<div class="settings-grid robot-primary-settings">
      ${inputFor({path:"robot.asset_source",label:"Asset source",type:"select",options:["Isaac Lab built-in","Workspace USD"],description:"The reference uses Isaac Lab's installed Unitree Go2 asset. Your robot uses a published USD below the dedicated workspace assets directory."}, robot.asset_source)}
      ${inputFor({path:"robot.asset_usd",label:"Isaac USD asset",type:"text",description:"Isaac Lab asset URI for the reference or an absolute container path below /workspace/projects/assets/onshape for the custom robot."}, robot.asset_usd)}
      ${inputFor({path:"robot.ready_for_training",label:"Robot validated",type:"boolean",description:"Safety gate set only after USD graph, 12 joint mappings, four foot contacts, base link, collision behavior, and calibrated standing pose have passed validation."}, robot.ready_for_training)}
    </div></div>
    <div class="robot-card robot-figure"><div class="robot-wire"></div><p>${robot.expected_joint_count} actuated joints</p></div>
  </div>
  <div class="settings-grid robot-contact-settings">
    ${inputFor({path:"robot.forward_axis.0",label:"Forward axis X",type:"number",min:-1,max:1,step:1,description:"X component of the robot body direction that the task calls forward."}, robot.forward_axis[0])}
    ${inputFor({path:"robot.forward_axis.1",label:"Forward axis Y",type:"number",min:-1,max:1,step:1,description:"Y component of the semantic forward direction. Unitree Go2 uses body +X; change this to match the authored custom chassis."}, robot.forward_axis[1])}
    ${inputFor({path:"robot.contacts.base",label:"Base contact link",type:"text",description:"Exact USD body-name expression used to detect chassis contact and falls."}, robot.contacts.base)}
    ${inputFor({path:"robot.contacts.undesired",label:"Undesired contact links",type:"text",description:"USD body-name regular expression for links that should not touch the surface."}, robot.contacts.undesired)}
    ${Object.entries(robot.contacts.feet).map(([key,value]) => inputFor({path:`robot.contacts.feet.${key}`,label:`${key.replaceAll('_',' ')} foot`,type:"text",description:"Exact USD rigid-body name for this semantic foot. All four must resolve uniquely."}, value)).join("")}
  </div>
  <div class="joint-table-wrap"><table class="joint-table"><thead><tr><th>USD joint</th><th>Semantic role</th><th>Rest rad</th><th>Direction</th><th>Stiffness</th><th>Damping</th><th>Torque</th><th>Speed</th></tr></thead><tbody>
    ${robot.joints.map((joint, index) => `<tr>
      <td><input data-joint="${index}" data-key="name" value="${joint.name}"></td><td><input data-joint="${index}" data-key="semantic" value="${joint.semantic}"></td>
      <td><input type="number" step="0.001" data-joint="${index}" data-key="rest_position" value="${joint.rest_position}"></td><td><input type="number" min="-1" max="1" step="2" data-joint="${index}" data-key="direction" value="${joint.direction}"></td>
      <td><input type="number" min="0" step="0.1" data-joint="${index}" data-key="stiffness" value="${joint.stiffness}"></td><td><input type="number" min="0" step="0.1" data-joint="${index}" data-key="damping" value="${joint.damping}"></td>
      <td><input type="number" min="0" step="0.01" data-joint="${index}" data-key="effort_limit" value="${joint.effort_limit}"></td><td><input type="number" min="0" step="0.1" data-joint="${index}" data-key="velocity_limit" value="${joint.velocity_limit}"></td>
    </tr>`).join("")}
  </tbody></table></div>`;
  bindInputs(panel);
  panel.querySelectorAll("[data-joint]").forEach(input => input.addEventListener("input", event => {
    const element = event.currentTarget;
    const numeric = element.type === "number";
    state.profile.robot.joints[Number(element.dataset.joint)][element.dataset.key] = numeric ? Number(element.value) : element.value;
    updateDirtyState();
  }));
}

function renderSettings() {
  const group = state.data.groups.find(item => item.id === state.section);
  const panel = document.querySelector("#settings-panel");
  panel.innerHTML = `<div class="settings-grid">${group.fields.map(field => inputFor(field, getPath(state.profile, field.path))).join("")}</div>`;
  bindInputs(panel);
}

function renderSearch() {
  const query = state.query.toLowerCase();
  const matches = state.data.groups.flatMap(group => group.fields
    .filter(field => `${field.label} ${field.description} ${field.path} ${group.title}`.toLowerCase().includes(query))
    .map(field => ({...field, label: `${group.title} · ${field.label}`})));
  const panel = document.querySelector("#settings-panel");
  panel.innerHTML = matches.length ? `<div class="settings-grid">${matches.map(field => inputFor(field, getPath(state.profile, field.path))).join("")}</div>` : `<div class="message warning">No settings match “${state.query}”. Try terms such as stiffness, surface, reward, reset, video, or PPO.</div>`;
  bindInputs(panel);
  return matches.length;
}

function updateDirtyState() {
  const dirty = changed();
  const save = document.querySelector("#save-state");
  save.className = `sync-item ${dirty ? "warn" : "good"}`;
  save.querySelector("small").textContent = dirty ? "Unsaved changes" : `SHA ${state.data.profile_hash.slice(0, 10)}`;
  document.querySelector("#save-button").disabled = !dirty || state.busy;
}

function renderMessages(validation = state.data.validation) {
  const messages = document.querySelector("#messages");
  messages.innerHTML = [
    ...validation.errors.map(text => `<div class="message error">${text}</div>`),
    ...validation.warnings.map(text => `<div class="message warning">${text}</div>`),
  ].join("");
}

function renderChecks(validation = state.data.launch_validation) {
  const list = document.querySelector("#launch-checks");
  const fixed = validation.errors.length ? [] : ["Profile schema and ranges valid", `${state.profile.robot.expected_joint_count} joint actions mapped`, "Deployable V2 observation contract", "Deterministic evaluation remains promotion gate"];
  list.innerHTML = [...fixed.map(text => `<li>${text}</li>`), ...validation.errors.map(text => `<li class="error">${text}</li>`), ...validation.warnings.map(text => `<li class="warning">${text}</li>`)].join("");
  document.querySelector("#start-training").disabled = validation.errors.length > 0 || changed() || state.busy;
}

function render() {
  renderNav();
  if (state.query) {
    document.querySelector("#section-kicker").textContent = "ALL SETTINGS";
    document.querySelector("#section-title").textContent = "Search settings";
    document.querySelector("#section-summary").textContent = `Results for “${state.query}” across the complete control profile.`;
    document.querySelector("#robot-panel").classList.add("panel-hidden");
    document.querySelector("#settings-panel").classList.remove("panel-hidden");
    renderSearch(); updateDirtyState(); renderMessages(); renderChecks(); return;
  }
  const isRobot = state.section === "robot";
  const group = state.data.groups.find(item => item.id === state.section);
  document.querySelector("#section-kicker").textContent = isRobot ? "SETUP" : (group.expert ? "ADVANCED SETTINGS" : "TRAINING SETTINGS");
  document.querySelector("#section-title").textContent = isRobot ? "Robot setup" : group.title;
  document.querySelector("#section-summary").textContent = isRobot ? "The exact model and semantic mapping Isaac Lab will load." : group.summary;
  document.querySelector("#robot-panel").classList.toggle("panel-hidden", !isRobot);
  document.querySelector("#settings-panel").classList.toggle("panel-hidden", isRobot);
  if (isRobot) renderRobot(); else renderSettings();
  updateDirtyState(); renderMessages(); renderChecks();
}

function populateProfiles() {
  const select = document.querySelector("#profile-select");
  select.innerHTML = state.data.profiles.map(profile => `<option value="${profile.id}" ${profile.id === state.data.selected_profile_id ? "selected" : ""}>${profile.name}</option>`).join("");
  document.querySelector("#joint-count").textContent = `${state.profile.robot.expected_joint_count} DOF`;
}

async function refreshStatus(force = false) {
  const runtime = document.querySelector("#runtime-state");
  try {
    const result = await api(`/api/status${force ? "?force=1" : ""}`);
    const fields = result.fields || {};
    const training = fields.training || "unknown";
    const activeHash = fields.profile_sha || "";
    const synced = activeHash && state.data.profile_hash.startsWith(activeHash);
    runtime.className = `sync-item ${training === "running" ? (synced ? "good" : "warn") : (result.ok ? "good" : "bad")}`;
    runtime.querySelector("small").textContent = training === "running" ? (synced ? "Running saved profile" : "Running profile differs") : `Training ${training}`;
    document.querySelector("#run-title").textContent = training;
    document.querySelector("#run-light").className = `status-light ${training === "running" ? "running" : training.includes("failed") ? "failed" : ""}`;
    const values = [fields.container || "—", fields.task || "—", fields.surface || "—", fields.progress || fields.target || "—", fields.best || "—", fields.gpu || "—", activeHash ? activeHash.slice(0, 10) : "No active profile"];
    document.querySelectorAll("#run-details dd").forEach((node, index) => node.textContent = values[index]);
    document.querySelector("#video-context").textContent = `Newest run: ${fields.task || "unknown task"} · ${fields.surface || "unknown surface"}`;
  } catch (error) {
    runtime.className = "sync-item bad";
    runtime.querySelector("small").textContent = error.message;
    document.querySelector("#run-title").textContent = "unreachable";
  }
}

async function save() {
  state.busy = true; updateDirtyState();
  try {
    const result = await api("/api/profile/save", { method: "POST", body: JSON.stringify(state.profile) });
    if (!result.ok) { renderMessages(result.validation); return; }
    state.saved = clone(state.profile); state.data.profile_hash = result.profile_hash; state.data.validation = result.validation; state.data.launch_validation = result.launch_validation;
    renderMessages(result.validation); renderChecks(result.launch_validation); updateDirtyState();
    document.querySelector("#messages").insertAdjacentHTML("afterbegin", `<div class="message success">Saved. Isaac Lab will consume profile SHA ${result.profile_hash.slice(0, 10)} on the next launch.</div>`);
  } catch (error) { renderMessages({errors:[error.message],warnings:[]}); }
  finally { state.busy = false; updateDirtyState(); }
}

async function runAction(action) {
  state.busy = true; renderChecks();
  const output = document.querySelector("#console-output"); output.textContent = action === "start_training" ? "Deploying the saved profile and starting Isaac Lab…" : "Stopping the Isaac Lab workload…";
  try {
    const result = await api("/api/action", { method: "POST", body: JSON.stringify({action}) });
    output.textContent = result.output || JSON.stringify(result, null, 2);
    if (result.validation) { renderMessages(result.validation); renderChecks(result.validation); }
  } catch (error) { output.textContent = error.message; }
  finally { state.busy = false; renderChecks(); await refreshStatus(true); }
}

async function refreshVideo() {
  const empty = document.querySelector("#video-empty");
  const video = document.querySelector("#training-video");
  const button = document.querySelector("#refresh-video");
  if (button.disabled) return;
  button.disabled = true;
  empty.textContent = "Fetching the newest rollout, or rendering it from the latest checkpoint…";
  try {
    const result = await api("/api/action", { method: "POST", body: JSON.stringify({action:"refresh_video"}) });
    if (!result.ok) throw new Error(result.output || "No video is available.");
    video.src = `/api/video/latest?token=${encodeURIComponent(token)}&t=${Date.now()}`;
    video.classList.add("video-visible"); empty.classList.add("panel-hidden"); video.load();
    setLargeVideo(true);
    document.querySelector("#console-output").textContent = result.output || "Newest video loaded.";
  } catch (error) { empty.textContent = error.message; video.classList.remove("video-visible"); empty.classList.remove("panel-hidden"); }
  finally { button.disabled = false; }
}

function setLargeVideo(expanded) {
  const card = document.querySelector("#video-card");
  const button = document.querySelector("#expand-video");
  card.classList.toggle("expanded", expanded);
  button.textContent = expanded ? "Close large view" : "Large view";
  button.setAttribute("aria-expanded", String(expanded));
  document.body.classList.toggle("video-expanded", expanded);
}

async function selectProfile(profileId) {
  if (changed() && !confirm("Discard unsaved changes and switch profiles?")) { populateProfiles(); return; }
  const data = await api("/api/profile/select", { method: "POST", body: JSON.stringify({profile_id: profileId}) });
  state.data = data; state.profile = clone(data.profile); state.saved = clone(data.profile); state.section = "robot";
  populateProfiles(); render(); await refreshStatus(true);
}

async function init() {
  try {
    state.data = await api("/api/bootstrap"); state.profile = clone(state.data.profile); state.saved = clone(state.data.profile);
    populateProfiles(); render(); await refreshStatus(true);
    setInterval(() => refreshStatus(false), 10000);
  } catch (error) {
    document.body.innerHTML = `<main class="fatal-error"><h1>Control center unavailable</h1><p>${error.message}</p><p>Open the exact tokenized URL printed by Start-RobotControlCenter.ps1.</p></main>`;
  }
}

document.querySelector("#expert-toggle").addEventListener("change", event => { state.expert = event.target.checked; render(); });
document.querySelector("#settings-search").addEventListener("input", event => { state.query = event.target.value.trim(); render(); });
document.querySelector("#profile-select").addEventListener("change", event => selectProfile(event.target.value));
document.querySelector("#save-button").addEventListener("click", save);
document.querySelector("#refresh-status").addEventListener("click", () => refreshStatus(true));
document.querySelector("#start-training").addEventListener("click", () => runAction("start_training"));
document.querySelector("#refresh-video").addEventListener("click", refreshVideo);
document.querySelector("#expand-video").addEventListener("click", () => setLargeVideo(!document.querySelector("#video-card").classList.contains("expanded")));
document.querySelector("#training-video").addEventListener("loadeddata", event => event.target.play().catch(() => {}));
document.addEventListener("keydown", event => { if (event.key === "Escape") setLargeVideo(false); });
document.querySelector("#stop-training").addEventListener("click", () => document.querySelector("#confirm-dialog").showModal());
document.querySelector("#confirm-dialog").addEventListener("close", event => { if (event.target.returnValue === "confirm") runAction("stop_training"); });
document.querySelector("#clear-console").addEventListener("click", () => { document.querySelector("#console-output").textContent = "No action yet."; });
init();
