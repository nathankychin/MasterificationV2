const app = document.querySelector("#app");
const toastRegion = document.querySelector("#toast-region");
const state = { user: null, page: "dashboard", dashboard: null, skill: null, scenario: null, startedAt: 0, seconds: 0, timers: [], alertDismissed: false, categoryManuallySelected: false, scenarioLoading: false, submitting: false, aiTestLoading: false, aiConfigured: false, scenarioProvider: "standard", submissionId: null };
const categories = ["Healthcare/Nursing", "Technical & Data", "Language", "Sciences & Math", "Humanities", "Engineering/Field", "Other"];
let activeRecognition = null;
const categoryPatterns = {
  "Healthcare/Nursing": [/\bnurs(?:e|es|ing)\b/, /\bclinical\b/, /\btriage\b/, /\bpatient\b/, /\bmedication\b/, /\bcpr\b/, /\bfirst aid\b/, /\banatomy\b/, /\bvital signs\b/, /\bwound care\b/],
  "Technical & Data": [/\bsql\b/, /\bpython\b/, /\bjavascript\b/, /\bprogramming\b/, /\bcoding\b/, /\bdata analysis\b/, /\bsoftware\b/, /\bdebugging\b/, /\bnetworking\b/, /\bcybersecurity\b/],
  Language: [/\blanguage\b/, /\bspanish\b/, /\bfrench\b/, /\bjapanese\b/, /\bmandarin\b/, /\bchinese\b/, /\barabic\b/, /\bmalay\b/, /\bgerman\b/, /\bitalian\b/, /\bkorean\b/, /\bportuguese\b/, /\bconversation(?:al)?\b/, /\btranslation\b/],
  "Sciences & Math": [/\bmathematics\b/, /\bmaths?\b/, /\balgebra\b/, /\bcalculus\b/, /\bgeometry\b/, /\bstatistics\b/, /\bphysics\b/, /\bchemistry\b/, /\bbiology\b/, /\bscience\b/, /\bstoichiometry\b/],
  Humanities: [/\bhistory\b/, /\bliterature\b/, /\bphilosophy\b/, /\bethics\b/, /\bpolitics\b/, /\beconomics\b/, /\bgeography\b/, /\bsociology\b/, /\bpsychology\b/, /\banthropology\b/],
  "Engineering/Field": [/\bengineering\b/, /\belectrician\b/, /\belectrical\b/, /\bmechanical\b/, /\bfield technician\b/, /\bhvac\b/, /\bwelding\b/, /\bworkshop\b/, /\bsite safety\b/],
  Other: [/\bpiano\b/, /\bmusic\b/, /\bguitar\b/, /\bviolin\b/, /\bpainting\b/, /\bdrawing\b/, /\bsculpture\b/, /\bpottery\b/, /\bcooking\b/, /\bwoodworking\b/, /\bphotography\b/, /\bdance\b/]
};

function detectSkillCategory(name) {
  const matches = Object.entries(categoryPatterns)
    .filter(([, patterns]) => patterns.some(pattern => pattern.test(name)));
  return matches.length === 1 ? matches[0][0] : null;
}

function updateCategorySuggestion() {
  const name = document.querySelector("#skill-name")?.value.trim() || "";
  const select = document.querySelector("#category");
  const guidance = document.querySelector("#category-guidance");
  if (!select || !guidance) return;
  const suggested = detectSkillCategory(name);
  if (!suggested) {
    guidance.hidden = true;
    return;
  }
  const selected = select.value;
  if (!state.categoryManuallySelected) {
    select.value = suggested;
    document.querySelector("#language-field").hidden = suggested !== "Language";
    guidance.textContent = `Detected category: ${suggested}.`;
    guidance.classList.remove("category-mismatch");
    guidance.hidden = false;
    return;
  }
  if (selected !== suggested) {
    guidance.innerHTML = `This skill looks like <strong>${esc(suggested)}</strong>. <button class="text-button" type="button" data-action="apply-suggested-category">Use suggestion</button>`;
    guidance.classList.add("category-mismatch");
  } else {
    guidance.textContent = `Detected category: ${suggested}.`;
    guidance.classList.remove("category-mismatch");
  }
  guidance.hidden = false;
}

function esc(value = "") {
  return String(value).replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
}
function icon(name) {
  const paths = {
    eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
    eyeoff: '<path d="m3 3 18 18M10.6 10.6a2 2 0 0 0 2.8 2.8"/><path d="M9.9 5.2A11 11 0 0 1 12 5c6.5 0 10 7 10 7a15 15 0 0 1-3.1 3.8M6.2 6.2C3.5 8 2 12 2 12s3.5 7 10 7c1.4 0 2.7-.3 3.8-.8"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    arrow: '<path d="M5 12h14m-7-7 7 7-7 7"/>',
    back: '<path d="m15 18-6-6 6-6"/>',
    mic: '<rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10v2a7 7 0 0 0 14 0v-2m-7 9v3m-4 0h8"/>',
    close: '<path d="m18 6-12 12M6 6l12 12"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>'
  };
  return `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || ""}</svg>`;
}
async function api(path, options = {}) {
  const response = await fetch(path, { credentials: "same-origin", headers: { "Content-Type": "application/json", ...(options.headers || {}) }, ...options });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
  return payload;
}
function toast(message, error = false) {
  const node = document.createElement("div");
  node.className = `toast${error ? " error" : ""}`;
  node.textContent = message;
  toastRegion.append(node);
  setTimeout(() => node.remove(), 5000);
}
function setPage(page) { state.page = page; render(); }
function shell(content) {
  const navItems = [["dashboard", "Overview"], ["settings", "Settings"]];
  if (state.user?.is_admin) navItems.push(["admin", "Admin"]);
  const accountLabel = state.page === "admin" ? "Administrator" : state.user.email;
  return `<div class="app-shell"><header class="topbar"><button class="brand" data-action="home"><span class="brand-mark">R</span><span><span class="brand-name">RECALL / READINESS</span><span class="brand-tag">SKILL RETENTION SYSTEM</span></span></button><div class="top-actions"><nav class="nav" aria-label="Main navigation">${navItems.map(([page, label]) => `<button class="${state.page === page ? "active" : ""}" data-page="${page}">${label}</button>`).join("")}</nav><span class="user-chip">${esc(accountLabel)}</span><button class="quiet-button" data-action="logout">Sign out</button></div></header>${content}</div>`;
}
function authView(error = "") {
  const signup = state.page === "signup";
  return `<div class="auth-layout"><section class="auth-copy"><div class="eyebrow">Readiness, made visible</div><h1>Keep critical skills <span>ready when it matters.</span></h1><p>Practice applied recall, see where fluency is fading, and build a review rhythm around the skills you cannot afford to forget.</p><div class="auth-points"><span><i>✓</i> Risk-weighted readiness, not streaks</span><span><i>✓</i> Scenario practice with saved evaluations</span><span><i>✓</i> Your skill history stays in your account</span></div></section><section class="auth-card"><div class="eyebrow">Your workspace</div><h2>${signup ? "Create your account" : "Welcome back"}</h2><p class="subheading">${signup ? "Start a private readiness workspace." : "Sign in to continue your practice."}</p>${error ? `<div class="alert" role="alert">${esc(error)}</div>` : ""}<form id="auth-form" style="margin-top:24px"><div class="field"><label for="email">Email address</label><input id="email" name="email" type="email" autocomplete="email" required maxlength="254" placeholder="you@example.com"></div><div class="field"><label for="password">Password${signup ? " · at least 10 characters" : ""}</label><div class="password-wrap"><input id="password" name="password" type="password" autocomplete="${signup ? "new-password" : "current-password"}" minlength="${signup ? 10 : 1}" required><button class="password-toggle" type="button" aria-label="Show password" title="Show password" data-action="toggle-password">${icon("eye")}</button></div></div><label class="remember"><input type="checkbox" name="remember"><span>Remember this device for 30 days</span></label><button class="button primary" type="submit" style="width:100%">${signup ? "Create account" : "Sign in"} ${icon("arrow")}</button></form><p class="auth-switch">${signup ? "Already have an account?" : "New to readiness tracking?"}<button class="text-button" data-action="auth-switch">${signup ? "Sign in" : "Create an account"}</button></p><p class="footer-note">Evaluation is a practice aid, not professional certification or official exam-board marking.</p></section></div>`;
}
function stat(label, value, suffix = "") {
  return `<div class="stat"><span class="stat-label">${label}</span><strong class="stat-value">${value}<small>${suffix}</small></strong></div>`;
}
function skillCard(skill) {
  return `<article class="skill-card ${skill.status}" style="--score:${skill.readiness}%"><div class="skill-top"><div><div class="skill-name">${esc(skill.name)}</div><div class="skill-category">${esc(skill.category)} · risk ${skill.risk_level}/10</div></div><span class="risk-pill">${skill.status}</span></div><div class="score-row"><span class="score">${skill.readiness}<small>%</small></span><span class="score-caption">readiness</span></div><div class="meter"><span></span></div><div class="skill-actions"><span class="score-caption">Difficulty ${skill.difficulty}/10</span><div class="page-actions"><button class="icon-button" title="Remove skill" aria-label="Remove ${esc(skill.name)}" data-delete-skill="${skill.id}">${icon("close")}</button><button class="button secondary" data-practice="${skill.id}">Practice ${icon("arrow")}</button></div></div></article>`;
}
function dashboardView() {
  const data = state.dashboard || { skills: [], recent: [], study_board: "" };
  const skills = data.skills;
  const critical = skills.filter(skill => skill.readiness < 40).length;
  const avg = skills.length ? (skills.reduce((sum, skill) => sum + skill.readiness, 0) / skills.length).toFixed(0) : "--";
  const content = `<main class="main"><section class="page-heading"><div><div class="eyebrow">Your readiness overview</div><h1>Good to see you.</h1><p class="subheading">Readiness shifts quietly. Your next useful practice is right here.</p></div><button class="button primary" data-action="add-skill">${icon("plus")} Add a skill</button></section><section class="stats-grid">${stat("TRACKED SKILLS", skills.length, "skills")}${stat("AVERAGE READINESS", avg, avg === "--" ? "" : "%")}${stat("NEEDS ATTENTION", critical, critical === 1 ? "skill" : "skills")}</section><section><div class="section-heading"><div><h2>Skill readiness</h2><p class="subheading">Exponential decay adjusted by skill risk and applied fluency</p></div><span class="score-caption">Critical &lt;40 · Warning 40–69 · Optimal ≥70</span></div>${skills.length ? `<div class="skill-grid">${skills.map(skillCard).join("")}</div>` : `<div class="empty-state"><strong>Your workspace starts with one skill.</strong>Add a high-stakes skill to begin tracking readiness and practice history.<br><br><button class="button secondary" data-action="add-skill">${icon("plus")} Add your first skill</button></div>`}</section><section class="panel recent-panel"><div class="section-heading"><div><h2>Recent revisions</h2><p class="subheading">Open any attempt to review its response and evaluation</p></div></div>${data.recent.length ? `<div class="recent-list">${data.recent.map(item => `<button class="recent-row" data-history="${item.id}"><span class="recent-name">${esc(item.skill_name)}</span><span class="recent-date">${new Date(item.created_at).toLocaleString()}</span><span class="recent-meta">Practice evaluation</span><span class="recent-score">${Number(item.readiness_score).toFixed(1)}%</span></button>`).join("")}</div>` : `<div class="empty-state" style="border:0;padding:26px 8px 12px">Completed practices and detailed scorecards will appear here.</div>`}</section><p class="footer-note">Readiness uses elapsed-time decay and applied-fluency estimates. It is a planning signal, not a safety or professional competence guarantee.</p></main>`;
  return shell(content);
}
function addSkillModal() {
  return `<div class="modal-backdrop" data-action="backdrop"><section class="modal" role="dialog" aria-modal="true" aria-labelledby="skill-modal-title"><div class="modal-head"><div><div class="eyebrow">Personal readiness profile</div><h2 id="skill-modal-title">Add a skill</h2><p class="subheading">Set how quickly this skill becomes risky without use.</p></div><button class="icon-button" data-action="close-modal" aria-label="Close">${icon("close")}</button></div><form id="skill-form"><div class="field"><label for="skill-name">Skill name</label><input id="skill-name" name="name" required maxlength="100" placeholder="e.g. IV medication calculation"></div><div class="field"><label for="category">Category</label><select id="category" name="category">${categories.map(category => `<option>${esc(category)}</option>`).join("")}</select></div><div class="field" id="language-field" hidden><label for="language-code">Practice language</label><select id="language-code" name="language_code"><option value="en-US">English</option><option value="es-ES">Spanish</option><option value="ja-JP">Japanese</option><option value="fr-FR">French</option><option value="de-DE">German</option><option value="it-IT">Italian</option><option value="pt-PT">Portuguese</option><option value="ko-KR">Korean</option><option value="zh-CN">Mandarin Chinese</option><option value="ms-MY">Malay</option></select></div><div class="field"><label for="skill-description">Skill context (optional)</label><textarea id="skill-description" name="description" maxlength="1000" placeholder="What do you want to practise or find difficult?"></textarea><span class="field-help">If Gemini is enabled, this context is sent to Google to tailor scenarios and qualitative feedback.</span></div><div class="form-grid"><div class="field"><label for="risk">Risk if forgotten · 1–10</label><div class="range-wrap"><input id="risk" name="risk_level" type="range" min="1" max="10" value="5"><output for="risk">5</output></div></div><div class="field"><label for="difficulty">Recall difficulty · 1–10</label><div class="range-wrap"><input id="difficulty" name="difficulty" type="range" min="1" max="10" value="5"><output for="difficulty">5</output></div></div></div><div class="field"><label for="criteria">Marking criteria / standards (optional)</label><textarea id="criteria" name="marking_criteria" maxlength="4000" placeholder="One criterion per line, copied from your syllabus, SOP, or standard."></textarea><span class="field-help">These user-entered criteria are checked transparently. This app does not fetch or claim to reproduce official exam-board mark schemes.</span></div><button class="button primary" type="submit">Save skill ${icon("arrow")}</button></form></section></div>`;
}
function practiceView() {
  const skill = state.skill;
  if (!skill || !state.scenario) return shell('<main class="main"><div class="loading">Preparing scenario...</div></main>');
  const draftKey = `readiness-draft:${state.user.id}:${skill.id}`;
  const draft = localStorage.getItem(draftKey) || "";
  const showAdvisory = !state.alertDismissed;
  const speech = state.scenario.input_mode === "speech";
  const providerLabel = state.scenarioProvider === "gemini" ? "AI-enhanced scenario · Gemini" : "Standard scenario";
  const privacyNotice = state.aiConfigured ? `<aside class="draft-advisory"><span>AI</span><span>Gemini is enabled. On submission, your answer and this scenario are sent to Google for qualitative feedback. Your Masterify score is calculated separately.</span></aside>` : "";
  const content = `<main class="main"><section class="practice-layout"><div class="practice-toolbar"><button class="quiet-button" data-action="back-dashboard">${icon("back")} Dashboard</button><span class="practice-meta">${esc(skill.category)} · ${esc(skill.name)}</span></div><div class="eyebrow">${providerLabel}</div><h1>Practice in context.</h1><p class="subheading">Work through the situation as you would in a real setting. Your draft stays on this device while you think.</p><section class="scenario-box"><div class="scenario-label">Scenario · ${esc(state.scenario.scenario_type)}</div><p class="scenario-prompt">${esc(state.scenario.prompt)}</p></section>${privacyNotice}${showAdvisory ? `<aside class="draft-advisory"><span>◉</span><span>Long sessions are automatically drafted on this device every 3 seconds. If the server sleeps, refresh when it is available; your draft will be restored in this browser.</span><button data-action="dismiss-advisory" aria-label="Dismiss advisory">×</button></aside>` : ""}<div class="field"><label for="practice-response">Your response</label><textarea id="practice-response" class="practice-input" placeholder="Think aloud in writing: what do you notice, what do you do, and how do you check the outcome?">${esc(draft)}</textarea></div><div class="practice-bottom"><div class="practice-controls"><span class="timer" id="timer">${formatTime(state.seconds)}</span>${speech ? `<button class="button secondary" data-action="speech" title="Dictate language response">${icon("mic")} Speak</button>` : ""}<span class="score-caption" id="draft-state">Draft stored locally</span></div><button class="button primary" data-action="submit-practice" ${state.submitting ? "disabled" : ""}>${state.submitting ? "Analysing your response..." : `Submit for evaluation ${icon("arrow")}`}</button></div></section></main>`;
  return shell(content);
}
function formatTime(seconds) { return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`; }
function evaluationView(result, historical = false) {
  const evaluation = result.evaluation || {};
  const readinessScore = historical ? result.readiness_after : result.readiness;
  const alignment = evaluation.alignment || {};
  const deductions = evaluation.deductions || [];
  const pastResponse = historical ? `<section class="evaluation-block wide" style="margin-bottom:13px"><h3>Original scenario and response</h3><div class="eyebrow">Scenario</div><p class="response-copy">${esc(result.scenario)}</p><div class="eyebrow" style="margin-top:18px">Your response</div><p class="response-copy">${esc(result.response)}</p></section>` : "";
  const guidance = evaluation.ai_guidance;
  const aiBlock = guidance ? `<section class="evaluation-block wide"><h3>Gemini qualitative feedback <span class="score-caption">${esc(guidance.confidence)} confidence · score unchanged</span></h3>${guidance.explanation ? `<p class="response-copy">${esc(guidance.explanation)}</p>` : ""}${guidance.weaknesses.length ? `<div class="alignment-row"><span>Areas to strengthen</span><strong>${guidance.weaknesses.map(esc).join(" · ")}</strong></div>` : ""}${guidance.where_marks_were_lost.length ? `<div class="alignment-row"><span>AI observations (not official deductions)</span><strong>${guidance.where_marks_were_lost.map(esc).join(" · ")}</strong></div>` : ""}</section>` : evaluation.ai_notice ? `<div class="notice wide">${esc(evaluation.ai_notice)}</div>` : "";
  const aiStrengths = guidance?.strengths || [];
  const aiSteps = guidance?.improvement_steps || [];
  const content = `<main class="main"><section class="practice-layout"><div class="practice-toolbar"><span class="practice-meta">${historical ? `Past attempt · ${new Date(result.created_at).toLocaleString()}` : "Practice complete"}</span><button class="quiet-button" data-action="back-dashboard">${icon("back")} Dashboard</button></div><div class="eyebrow">${historical ? "Historical evaluation" : "Practice evaluation"} · ${evaluation.ai_provider === "gemini" ? "AI-enhanced guidance" : "Standard evaluation"}</div><h1>${esc(result.skill_name || state.skill?.name || "Readiness updated")}</h1><p class="subheading">Your attempt is saved to your practice history.</p><section class="panel evaluation-hero"><div><div class="eyebrow">Updated readiness</div><div class="evaluation-score">${Number(readinessScore || 0).toFixed(1)}<small>%</small></div></div><div><span class="score-caption">Attempt score · Masterify deterministic</span><div class="recent-score">${Number(evaluation.score || result.readiness_score || 0).toFixed(1)}%</div></div></section>${pastResponse}<div class="evaluation-grid"><section class="evaluation-block wide"><h3>Score components</h3><div class="alignment-row"><span>Accuracy / criteria coverage · 80%</span><strong>${Number(evaluation.accuracy || 0).toFixed(1)}%</strong></div><div class="alignment-row"><span>Response time · 20%</span><strong>${Number(evaluation.speed || 0).toFixed(1)}%</strong></div><div class="alignment-row"><span>Combined attempt score</span><strong>${Number(evaluation.score || result.readiness_score || 0).toFixed(1)}%</strong></div></section>${aiBlock}<section class="evaluation-block"><h3><span class="eval-mark">+</span> What You Did Well</h3><ul>${(evaluation.strengths || ["Practice attempt recorded."]).map(item => `<li>${esc(item)}</li>`).join("")}${aiStrengths.map(item => `<li>${esc(item)} <span class="score-caption">Gemini</span></li>`).join("")}</ul></section><section class="evaluation-block"><h3><span class="eval-deduct">−</span> Where Marks Were Deducted</h3>${deductions.length ? `<ul>${deductions.map(item => `<li>${esc(item.criterion)} · ${Number(item.deduction).toFixed(1)} points</li>`).join("")}</ul>` : `<p class="subheading">${evaluation.alignment?.criteria_total ? "No user-entered criteria were missed." : "No line-by-line criteria were supplied for this skill. Add criteria to enable explicit criterion deductions."}</p>`}</section><section class="evaluation-block wide"><h3>Actionable steps to improve</h3><ul>${(evaluation.improvements || []).map(item => `<li>${esc(item)}</li>`).join("")}${aiSteps.map(item => `<li>${esc(item)} <span class="score-caption">Gemini</span></li>`).join("")}</ul></section><section class="evaluation-block wide"><h3>Board / standard alignment</h3><div class="alignment-row"><span>Selected standard</span><strong>${esc(alignment.selected_standard || "General rubric")}</strong></div><div class="alignment-row"><span>Evaluation basis</span><strong>${esc(alignment.basis || "General rubric")}</strong></div><div class="alignment-row"><span>Criteria met</span><strong>${alignment.criteria_total ? `${alignment.criteria_met} / ${alignment.criteria_total}` : "No criteria entered"}</strong></div><div class="notice">${esc(alignment.note || evaluation.rubric_note || "Automated practice feedback is not an official mark or certification.")}</div></section></div><div style="display:flex;gap:10px;margin-top:18px"><button class="button primary" data-action="back-dashboard">Back to Dashboard ${icon("arrow")}</button>${!historical && state.skill ? `<button class="button secondary" data-practice="${state.skill.id}">Practice again</button>` : ""}</div><p class="footer-note">${esc(evaluation.rubric_note || "This evaluation is a practice aid, not professional sign-off.")}</p></section></main>`;
  return shell(content);
}
async function adminView() {
  try {
    const [data, ai] = await Promise.all([api("/api/admin/analytics"), api("/api/admin/ai-diagnostics")]);
    const cards = `${stat("ACTIVE ACCOUNTS", data.active_users, "sessions")}${stat("TOTAL ACCOUNTS", data.users_total, "")}${stat("PRACTICE SESSIONS", data.practice_sessions, "completed")}${stat("AVG RESPONSE TIME", data.avg_response_ms, "ms")}`;
    const formatTime = value => value ? new Date(value).toLocaleString() : "No recorded event";
    const latestFailure = ai.latest_failure;
    const events = ai.recent_events.length ? `<div class="ai-events">${ai.recent_events.map(event => `<div class="ai-event-row"><span class="ai-event-state ${event.success ? "success" : "failure"}">${event.success ? "Success" : "Fallback"}</span><span>${esc(event.task)}</span><span>${esc(event.error_category || "No error")}${event.http_status ? ` · HTTP ${Number(event.http_status)}` : ""}</span><span>${Number(event.latency_ms || 0)} ms</span><time>${esc(formatTime(event.created_at))}</time></div>`).join("")}</div>` : `<p class="subheading">No Gemini attempts have been recorded since diagnostics were enabled.</p>`;
    const aiPanel = `<section class="panel ai-diagnostics"><div class="section-heading"><div><div class="eyebrow">Restricted · administrator</div><h2>Gemini diagnostics</h2><p class="subheading">Metadata only. Prompts, answers, credentials, and user identifiers are not stored here.</p></div><button class="button secondary" data-action="test-ai" ${state.aiTestLoading ? "disabled" : ""}>${state.aiTestLoading ? "Testing Gemini..." : "Test AI Connection"}</button></div><div class="ai-summary-grid"><div><span>Provider</span><strong>${esc(ai.provider)}</strong></div><div><span>Configuration</span><strong>${ai.configured ? "Configured" : "Missing key"}</strong></div><div><span>Provider status</span><strong class="status-${esc(ai.provider_status)}">${esc(ai.provider_status)}</strong></div><div><span>Model</span><strong>${esc(ai.model)}</strong></div><div><span>Latest test</span><strong>${ai.latest_test ? `${ai.latest_test.success ? "Success" : "Failure"} · ${esc(formatTime(ai.latest_test.created_at))}` : "Not tested"}</strong></div><div><span>Latest evaluation</span><strong>${esc(formatTime(ai.latest_evaluation?.created_at))}</strong></div><div><span>Last successful evaluation</span><strong>${esc(formatTime(ai.latest_success?.created_at))}</strong></div><div><span>Last failure</span><strong>${latestFailure ? esc(formatTime(latestFailure.created_at)) : "None recorded"}</strong></div></div><div class="ai-counts"><span>AI feedback: <strong>${Number(ai.counts.ai_evaluations || 0)}</strong></span><span>Fallback feedback: <strong>${Number(ai.counts.fallback_evaluations || 0)}</strong></span><span>Failed attempts: <strong>${Number(ai.counts.failed_attempts || 0)}</strong></span></div>${latestFailure ? `<div class="notice ai-last-error"><strong>${esc(latestFailure.error_category || "failure")}${latestFailure.http_status ? ` · HTTP ${Number(latestFailure.http_status)}` : ""} · ${Number(latestFailure.latency_ms || 0)} ms</strong><br>${esc(latestFailure.safe_message || "No diagnostic message available.")}</div>` : ""}<h3 class="ai-events-title">Recent attempts</h3>${events}</section>`;
    return shell(`<main class="main"><section class="page-heading"><div><div class="eyebrow">Restricted · administrator</div><h1>Operational health.</h1><p class="subheading">Aggregate service metrics only. No account identifiers or practice responses are exposed.</p></div></section><section class="admin-grid">${cards}</section><section class="panel"><h2>Average category decay</h2><p class="subheading">Mean modeled loss from initial score, grouped by skill category.</p>${data.category_decay.length ? data.category_decay.map(row => `<div class="admin-category"><span>${esc(row.category)}</span><span>${(Number(row.decay || 0) * 100).toFixed(1)}% decay</span></div>`).join("") : `<p class="subheading" style="margin-top:20px">No skill activity is available yet.</p>`}</section>${aiPanel}<p class="footer-note">Operational analytics are aggregate-only. Gemini diagnostics contain sanitized status metadata, not practice content.</p></main>`);
  } catch (error) { toast(error.message, true); return dashboardView(); }
}
function settingsView() {
  const board = state.dashboard?.study_board || "";
  return shell(`<main class="main"><section class="page-heading"><div><div class="eyebrow">Workspace preferences</div><h1>Practice context.</h1><p class="subheading">Your selected syllabus is appended to scenarios and shown in evaluation context.</p></div></section><section class="panel" style="max-width:620px"><form id="settings-form"><div class="field"><label for="study-board">Study board / standard</label><input id="study-board" maxlength="120" value="${esc(board)}" placeholder="e.g. IGCSE Chemistry, NMC clinical guidance"></div><p class="field-help" style="margin-bottom:18px">Standards are stored as your context only. The app does not connect to official syllabus repositories; add exact marking criteria to each skill for transparent response checks.</p><button class="button primary" type="submit">Save preferences ${icon("arrow")}</button></form></section></main>`);
}
async function render() {
  if (!state.user) { app.innerHTML = authView(); return; }
  if (state.page === "dashboard") {
    try { state.dashboard = await api("/api/dashboard"); }
    catch (error) { if (error.message.includes("Sign in") || error.message.includes("expired")) { state.user = null; state.page = "login"; return render(); } toast(error.message, true); }
    app.innerHTML = dashboardView();
  } else if (state.page === "practice") app.innerHTML = practiceView();
  else if (state.page === "evaluation") app.innerHTML = evaluationView(state.lastResult);
  else if (state.page === "history") app.innerHTML = evaluationView(state.lastResult, true);
  else if (state.page === "settings") { state.dashboard ||= await api("/api/dashboard"); app.innerHTML = settingsView(); }
  else if (state.page === "admin" && state.user.is_admin) app.innerHTML = await adminView();
  else { state.page = "dashboard"; return render(); }
  if (state.modal) {
    app.insertAdjacentHTML("beforeend", addSkillModal());
    document.querySelector("#category").closest(".field").insertAdjacentHTML("afterend", '<div id="category-guidance" class="category-guidance" aria-live="polite" hidden></div>');
  }
  if (state.page === "practice") attachPracticeTimers();
}
function clearPracticeTimers() {
  state.timers.forEach(clearInterval);
  state.timers = [];
  if (activeRecognition) { activeRecognition.stop(); activeRecognition = null; }
}
function attachPracticeTimers() {
  clearPracticeTimers();
  state.timers.push(setInterval(() => {
    state.seconds = Math.floor((Date.now() - state.startedAt) / 1000);
    const timer = document.querySelector("#timer");
    if (timer) timer.textContent = formatTime(state.seconds);
  }, 1000));
  state.timers.push(setInterval(() => { api("/api/ping").catch(() => {}); }, 4 * 60 * 1000));
  const response = document.querySelector("#practice-response");
  const key = `readiness-draft:${state.user.id}:${state.skill.id}`;
  state.timers.push(setInterval(() => {
    const field = document.querySelector("#practice-response");
    if (!field) return;
    localStorage.setItem(key, field.value);
    const label = document.querySelector("#draft-state");
    if (label) label.textContent = `Draft saved locally · ${new Date().toLocaleTimeString()}`;
  }, 3000));
  response?.focus();
}
async function startPractice(id) {
  if (state.scenarioLoading) return;
  state.scenarioLoading = true;
  app.innerHTML = shell('<main class="main"><section class="practice-layout"><div class="eyebrow">Scenario preparation</div><h1>Generating your scenario...</h1><p class="subheading">Your saved answers and practice history stay in Masterify while we prepare this drill.</p><div class="loading">PLEASE WAIT</div></section></main>');
  try {
    const data = await api(`/api/skills/${id}/scenario`);
    clearPracticeTimers();
    state.skill = data.skill;
    state.scenario = data.scenario;
    state.aiConfigured = data.ai_configured;
    state.scenarioProvider = data.ai_provider;
    state.startedAt = Date.now();
    state.seconds = 0;
    state.submitting = false;
    state.submissionId = crypto.randomUUID();
    state.alertDismissed = false;
    state.page = "practice";
    await render();
  } catch (error) { toast(error.message, true); state.page = "dashboard"; await render(); }
  finally { state.scenarioLoading = false; }
}
async function submitPractice() {
  if (state.submitting) return;
  const response = document.querySelector("#practice-response")?.value.trim();
  if (!response) return toast("Write a response before submitting.", true);
  state.submitting = true;
  localStorage.setItem(`readiness-draft:${state.user.id}:${state.skill.id}`, response);
  const button = document.querySelector('[data-action="submit-practice"]');
  if (button) { button.disabled = true; button.textContent = "Analysing your response..."; }
  try {
    const result = await api("/api/practices", { method: "POST", body: JSON.stringify({ skill_id: state.skill.id, scenario: state.scenario.prompt, response, submission_id: state.submissionId, elapsed_seconds: Math.floor((Date.now() - state.startedAt) / 1000) }) });
    clearPracticeTimers();
    localStorage.removeItem(`readiness-draft:${state.user.id}:${state.skill.id}`);
    state.lastResult = { ...result, skill_name: state.skill.name };
    state.submitting = false;
    state.page = "evaluation";
    await render();
  } catch (error) { toast(error.message, true); state.submitting = false; if (button) { button.disabled = false; button.innerHTML = `Retry evaluation ${icon("arrow")}`; } }
}
function modalClose() { state.modal = false; render(); }

app.addEventListener("click", async event => {
  const target = event.target.closest("button,[data-action],[data-practice],[data-history],[data-delete-skill]");
  if (!target) return;
  if (target.dataset.page) { state.page = target.dataset.page; return render(); }
  if (target.dataset.practice) return startPractice(target.dataset.practice);
  if (target.dataset.history) {
    try { state.lastResult = await api(`/api/practices/${target.dataset.history}`); state.page = "history"; render(); }
    catch (error) { toast(error.message, true); }
    return;
  }
  if (target.dataset.deleteSkill) {
    if (!confirm("Remove this skill and its practice history? This cannot be undone.")) return;
    try { await api(`/api/skills/${target.dataset.deleteSkill}`, { method: "DELETE" }); toast("Skill and its history removed."); render(); }
    catch (error) { toast(error.message, true); }
    return;
  }
  const action = target.dataset.action;
  if (action === "home" || action === "back-dashboard") { clearPracticeTimers(); state.page = "dashboard"; state.modal = false; return render(); }
  if (action === "logout") { clearPracticeTimers(); await api("/api/logout", { method: "POST", body: "{}" }).catch(() => {}); state.user = null; state.page = "login"; return render(); }
  if (action === "test-ai") {
    if (state.aiTestLoading) return;
    state.aiTestLoading = true;
    target.disabled = true;
    target.textContent = "Testing Gemini...";
    try {
      const result = await api("/api/ai-test", { method: "POST", body: "{}" });
      toast(result.success ? "Gemini connection successful." : `Gemini test failed: ${result.error_category || "application error"}.`, !result.success);
    } catch (error) {
      toast("Could not complete the Gemini test. See administrator diagnostics.", true);
    } finally {
      state.aiTestLoading = false;
      state.page = "admin";
      await render();
    }
    return;
  }
  if (action === "add-skill") { state.categoryManuallySelected = false; state.modal = true; return render(); }
  if (action === "apply-suggested-category") {
    const suggestion = detectSkillCategory(document.querySelector("#skill-name")?.value || "");
    if (suggestion) {
      document.querySelector("#category").value = suggestion;
      state.categoryManuallySelected = false;
      document.querySelector("#language-field").hidden = suggestion !== "Language";
      updateCategorySuggestion();
    }
    return;
  }
  if (action === "close-modal" || (action === "backdrop" && event.target === target)) return modalClose();
  if (action === "auth-switch") { state.page = state.page === "signup" ? "login" : "signup"; return render(); }
  if (action === "toggle-password") {
    const field = document.querySelector("#password");
    const visible = field.type === "password";
    field.type = visible ? "text" : "password";
    target.innerHTML = icon(visible ? "eyeoff" : "eye");
    target.setAttribute("aria-label", visible ? "Hide password" : "Show password");
    target.title = visible ? "Hide password" : "Show password";
  }
  if (action === "dismiss-advisory") { state.alertDismissed = true; app.innerHTML = practiceView(); attachPracticeTimers(); }
  if (action === "submit-practice") submitPractice();
  if (action === "speech") startSpeech(target);
});

app.addEventListener("input", event => {
  if (event.target.matches('input[type="range"]')) event.target.nextElementSibling.value = event.target.value;
  if (event.target.id === "skill-name") updateCategorySuggestion();
});
app.addEventListener("change", event => {
  if (event.target.id === "category") {
    state.categoryManuallySelected = true;
    document.querySelector("#language-field").hidden = event.target.value !== "Language";
    updateCategorySuggestion();
  }
});
app.addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.target;
  if (form.id === "auth-form") {
    const data = Object.fromEntries(new FormData(form));
    data.remember = form.elements.remember.checked;
    try {
      await api(state.page === "signup" ? "/api/register" : "/api/login", { method: "POST", body: JSON.stringify(data) });
      const session = await api("/api/me");
      state.user = session.user;
      state.page = "dashboard";
      await render();
    } catch (error) { app.innerHTML = authView(error.message); }
  }
  if (form.id === "skill-form") {
    const data = Object.fromEntries(new FormData(form));
    data.risk_level = Number(data.risk_level); data.difficulty = Number(data.difficulty);
    data.category_override = state.categoryManuallySelected;
    try {
      const result = await api("/api/skills", { method: "POST", body: JSON.stringify(data) });
      state.modal = false;
      toast(result.category_adjusted ? `Skill category corrected to ${result.category}.` : "Skill added to your readiness profile.");
      render();
    }
    catch (error) { toast(error.message, true); }
  }
  if (form.id === "settings-form") {
    try { await api("/api/settings", { method: "POST", body: JSON.stringify(Object.fromEntries(new FormData(form))) }); toast("Preferences saved."); state.dashboard = null; render(); }
    catch (error) { toast(error.message, true); }
  }
});

function startSpeech(button) {
  if (activeRecognition) {
    activeRecognition.stop();
    activeRecognition = null;
    return;
  }
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) return toast("Speech recognition is not available in this browser. You can type your response.", true);
  const recognition = new SpeechRecognition();
  recognition.lang = state.scenario?.language_code || "en-US";
  recognition.continuous = true;
  recognition.interimResults = true;
  const field = document.querySelector("#practice-response");
  const startText = field.value;
  button.textContent = "Stop listening";
  recognition.onresult = event => {
    let transcript = "";
    for (let index = event.resultIndex; index < event.results.length; index++) transcript += event.results[index][0].transcript;
    field.value = `${startText}${startText && !startText.endsWith(" ") ? " " : ""}${transcript}`;
  };
  recognition.onerror = () => toast("Microphone input stopped. You can continue by typing.", true);
  recognition.onend = () => { activeRecognition = null; button.disabled = false; button.innerHTML = `${icon("mic")} Speak`; };
  activeRecognition = recognition;
  recognition.start();
}

async function initialize() {
  try { const result = await api("/api/me"); state.user = result.user; }
  catch { state.user = null; }
  if (!state.user) state.page = "login";
  await render();
}
initialize();