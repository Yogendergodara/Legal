const $ = (id) => document.getElementById(id);

function setStatus(text, cls = "") {
  const el = $("status");
  el.textContent = text;
  el.className = "status " + cls;
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...options.headers },
    ...options,
  });
  const text = await res.text();
  let data;
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = { raw: text };
  }
  if (!res.ok) {
    const detail = data.detail || data.error || text || res.statusText;
    throw new Error(typeof detail === "object" ? JSON.stringify(detail, null, 2) : detail);
  }
  return data;
}

async function saveConfig() {
  await api("/api/config", {
    method: "POST",
    body: JSON.stringify({
      document_server_url: $("docUrl").value.trim(),
      platform_url: $("platformUrl").value.trim(),
      tenant_id: $("tenantId").value.trim(),
    }),
  });
  setStatus("Config saved", "ok");
}

async function checkHealth() {
  setStatus("Checking health…", "running");
  $("healthOut").textContent = "";
  try {
    const data = await api("/api/health");
    $("healthOut").textContent = JSON.stringify(data, null, 2);
    const docOk = data.document_mcp?.db === "ok";
    const caps = data.mcp_capabilities || [];
    const multiPid = (data.port_listener_count || 0) > 1;
    const missingCap = docOk && !caps.includes("search_request_metadata");
    if (!data.llm_configured) {
      setStatus("LLM key missing — set LLM_API_KEY in temp_java_sync/.env", "err");
    } else if (multiPid) {
      setStatus("WARNING: multiple processes on document-mcp port", "err");
    } else if (missingCap) {
      setStatus("WARNING: MCP missing search_request_metadata (stale?)", "err");
    } else {
      setStatus(docOk ? "document-mcp OK" : "document-mcp not ready (need pgvector)", docOk ? "ok" : "err");
    }
  } catch (e) {
    $("healthOut").textContent = e.message;
    setStatus("Health check failed", "err");
  }
}

function showSessionInfo(text) {
  const el = $("sessionInfo");
  el.textContent = text;
  el.classList.remove("hidden");
}

function updateContractCharCount() {
  const n = ($("contractText")?.value || "").length;
  const el = $("contractCharCount");
  if (el) el.textContent = `${n.toLocaleString()} characters`;
}

function esc(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function escAttr(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;");
}

function primaryFindings(findings) {
  const byKey = new Map();
  for (const f of findings) {
    const key = `${f.contract_section_id || ""}:${f.dimension_label || ""}`;
    const existing = byKey.get(key);
    const source = f.metadata?.source || "";
    if (existing) {
      if (source === "playbook_compare" && existing.metadata?.source !== "playbook_compare") {
        byKey.set(key, f);
      }
      continue;
    }
    byKey.set(key, f);
  }
  return [...byKey.values()].sort((a, b) => {
    const sec = (a.contract_section_id || "").localeCompare(b.contract_section_id || "");
    if (sec !== 0) return sec;
    return severityRank(b.severity) - severityRank(a.severity);
  });
}

function severityRank(sev) {
  const order = { critical: 3, important: 2, info: 1 };
  return order[String(sev || "").toLowerCase()] || 0;
}

function renderFindings(findings) {
  if (!findings?.length) {
    $("findingsTable").innerHTML = "<p>No findings.</p>";
    return;
  }
  const primary = primaryFindings(findings);
  const rows = primary
    .map(
      (f) => `<tr>
      <td>${esc(f.contract_section_id || "—")}</td>
      <td><span class="badge ${esc(f.status)}">${esc(f.status)}</span></td>
      <td>${esc(f.dimension_label || "—")}</td>
      <td>${esc(f.metadata?.policy_title || "—")}</td>
      <td class="quote-cell">${esc(f.contract_quote || "—")}</td>
      <td class="quote-cell">${esc(f.policy_quote || "—")}</td>
      <td>${esc((f.rationale || "").slice(0, 160))}</td>
    </tr>`
    )
    .join("");
  $("findingsTable").innerHTML = `<table>
    <thead><tr>
      <th>§</th><th>Status</th><th>Dimension</th><th>Playbook</th>
      <th>Contract text</th><th>Policy text</th><th>Rationale</th>
    </tr></thead>
    <tbody>${rows}</tbody></table>`;
}

function violationCardHtml(f) {
  const policyTitle = f.metadata?.policy_title || "Policy playbook";
  const section = f.contract_section_id ? `§${f.contract_section_id}` : "—";
  return `<article class="violation-card">
    <div class="vc-header">
      <span class="vc-title">${esc(section)} — ${esc(f.dimension_label || "Violation")}</span>
      <span class="badge ${esc(f.status)}">${esc(f.status)}</span>
      <span class="badge">${esc(f.severity || "—")}</span>
    </div>
    <div class="vc-playbook">Violated playbook: <strong>${esc(policyTitle)}</strong></div>
    <div class="quote-compare">
      <div class="quote-box contract">
        <div class="quote-label contract">Contract (violates policy)</div>
        <div class="quote-text">${esc(f.contract_quote || "— no quote —")}</div>
      </div>
      <div class="quote-box policy">
        <div class="quote-label policy">Policy / playbook (required standard)</div>
        <div class="quote-text">${esc(f.policy_quote || "— no quote —")}</div>
      </div>
    </div>
    <div class="violation-rationale"><strong>Why:</strong> ${esc(f.rationale || "")}</div>
  </article>`;
}

function renderViolations(findings) {
  const panel = $("violationsPanel");
  if (!findings?.length) {
    panel.innerHTML = "<p>No findings.</p>";
    return;
  }
  const violations = primaryFindings(findings).filter(
    (f) =>
      f.status === "NON_COMPLIANT" &&
      (f.contract_quote || f.policy_quote) &&
      (f.metadata?.source || "") !== "section_first_final"
  );
  if (!violations.length) {
    panel.innerHTML =
      "<p class='violations-intro'>No non-compliant findings with contract + policy quotes. Check <strong>All findings</strong> or <strong>Summary</strong>.</p>";
    return;
  }
  panel.innerHTML =
    `<p class="violations-intro">${violations.length} violation(s) — contract language vs playbook standard (side by side).</p>` +
    violations.map(violationCardHtml).join("");
}

function parseReviewOutput(data) {
  const findings =
    data.findings ??
    data.artifacts?.report?.findings ??
    data.report?.findings ??
    [];
  const count = data.finding_count ?? findings.length;
  const md =
    data.summary_markdown ??
    data.output ??
    data.artifacts?.report?.summary_markdown ??
    "(no summary)";
  const artifact =
    data.artifact ??
    data.artifacts?.audit ??
    data.artifacts?.report?.metadata?.artifact ??
    {};
  return { findings, count, md, artifact };
}

function renderReview(data) {
  const { findings, count, md, artifact } = parseReviewOutput(data);
  $("summaryMd").textContent = md;
  renderViolations(findings);
  renderFindings(findings);
  $("artifactJson").textContent = JSON.stringify(artifact || {}, null, 2);
  $("rawJson").textContent = JSON.stringify(data, null, 2);
  const violations = primaryFindings(findings).filter(
    (f) => f.status === "NON_COMPLIANT" && (f.metadata?.source || "") !== "section_first_final"
  ).length;
  setStatus(
    `Review done — ${count} finding(s), ${violations} violation(s) with quotes`,
    "ok"
  );
}

function disableButtons(on) {
  document.querySelectorAll("button").forEach((b) => (b.disabled = on));
}

function showPreflight(data) {
  const panel = $("preflightPanel");
  const pf = data.preflight;
  if (!pf) {
    panel.classList.add("hidden");
    return;
  }
  const tagged = (data.policies || [])
    .map((p) => `${p.title || p.policy_ref}: [${(p.categories || []).join(", ") || "?"}]`)
    .join(" | ");
  panel.textContent =
    `${pf.policies_synced ?? "?"} policies indexed (auto-tagged)` +
    (tagged ? ` — ${tagged}` : "");
  panel.classList.remove("hidden");
}

function applyPolicySyncResponse(data) {
  if (data.tenant_id) {
    $("sessionTenantId").value = data.tenant_id;
    $("sessionTenantId").dataset.locked = "1";
    showSessionInfo(`tenant: ${data.tenant_id} | ${data.policies?.length ?? 0} policies indexed`);
  }
  showPreflight(data);
  $("rawJson").textContent = JSON.stringify(data, null, 2);
  setStatus(
    `Policies indexed — tenant ${data.tenant_id} | ${data.policies?.length ?? 0} playbook(s)`,
    "ok"
  );
}

// --- Policy blocks (raw text only; categories auto-tagged at ingest) ---

const SAMPLE_POLICIES = [
  {
    title: "Standard Confidentiality Playbook",
    review_guidance: "Receiving party must use at least reasonable care. Term should be at least 2 years for vendor NDAs.",
    text: "The receiving party shall protect Confidential Information using no less than reasonable care and industry-standard safeguards. NDA term shall be no less than two (2) years from the Effective Date.",
  },
  {
    title: "Liability Cap Playbook",
    review_guidance: "Vendor liability cap should not be below $500k for enterprise deals.",
    text: "Total aggregate liability shall not be less than five hundred thousand dollars ($500,000) for vendor NDAs.",
  },
  {
    title: "Indemnification Standard",
    review_guidance: "Indemnification must be mutual for both parties.",
    text: "Each party shall indemnify, defend, and hold harmless the other party from third-party claims arising from that party's gross negligence, willful misconduct, or material breach.",
  },
];

function policyCardHtml(policy, index) {
  const canRemove = index > 0;
  return `<div class="policy-card" data-index="${index}">
    <div class="policy-card-head">
      <input class="policy-title-input" type="text" value="${escAttr(policy.title || "")}" placeholder="Policy title" />
      ${canRemove ? '<button type="button" class="danger btn-remove-policy">Remove</button>' : ""}
    </div>
    <label class="compact-label">Review guidance (optional)
      <input class="policy-guidance-input" type="text" value="${escAttr(policy.review_guidance || "")}" placeholder="What the reviewer should check for" />
    </label>
    <label class="compact-label">Policy raw text
      <textarea class="policy-text-input" rows="5" placeholder="Paste full playbook / policy standard text…">${esc(policy.text || "")}</textarea>
    </label>
  </div>`;
}

function renderPolicyBlocks(policies) {
  $("policyBlocks").innerHTML = policies.map(policyCardHtml).join("");
  bindPolicyButtons();
}

function bindPolicyButtons() {
  $("policyBlocks").querySelectorAll(".btn-remove-policy").forEach((btn) => {
    btn.onclick = () => {
      btn.closest(".policy-card")?.remove();
    };
  });
}

function addPolicyBlock() {
  const container = $("policyBlocks");
  const index = container.querySelectorAll(".policy-card").length;
  container.insertAdjacentHTML(
    "beforeend",
    policyCardHtml({ title: "", review_guidance: "", text: "" }, index)
  );
  bindPolicyButtons();
}

function collectPoliciesPayload() {
  return [...$("policyBlocks").querySelectorAll(".policy-card")].map((card) => ({
    title: card.querySelector(".policy-title-input")?.value.trim() || "Policy",
    review_guidance: card.querySelector(".policy-guidance-input")?.value.trim() || "",
    policy_type: $("contractType").value.trim() || "nda",
    text: card.querySelector(".policy-text-input")?.value || "",
  }));
}

async function runSyncPolicies() {
  const policies = collectPoliciesPayload().filter((p) => p.text.trim());
  if (!policies.length) {
    setStatus("Add at least one policy with raw text", "err");
    return;
  }

  setStatus("Indexing policies (auto-tagging categories)…", "running");
  disableButtons(true);
  try {
    const data = await api("/api/sync-policies", {
      method: "POST",
      body: JSON.stringify({
        policies,
        use_shared_tenant: $("useSharedTenant").checked,
        replace_tenant_policies: $("replaceTenantPolicies").checked,
      }),
    });
    applyPolicySyncResponse(data);
  } catch (e) {
    setStatus("Policy sync failed: " + e.message, "err");
    $("rawJson").textContent = e.message;
  } finally {
    disableButtons(false);
  }
}

function loadSamplePolicies() {
  renderPolicyBlocks(SAMPLE_POLICIES);
  setStatus("Sample policies loaded — categories auto-tagged on index", "ok");
}

async function loadSampleContract() {
  setStatus("Loading fixture contract text…", "running");
  try {
    const data = await api("/api/fixture-contract");
    $("contractText").value = data.contract_text || "";
    $("contractTitle").value = "Mutual Non-Disclosure Agreement";
    $("contractType").value = "nda";
    updateContractCharCount();
    setStatus("Sample NDA contract text loaded", "ok");
  } catch (e) {
    setStatus("Failed to load sample contract: " + e.message, "err");
  }
}

async function runReviewText(usePlatform) {
  const contractText = ($("contractText").value || "").trim();
  if (!contractText) {
    setStatus("Paste full contract raw text before review", "err");
    return;
  }

  setStatus(usePlatform ? "Review via platform…" : "Running review…", "running");
  disableButtons(true);
  try {
    const data = await api("/api/review-text", {
      method: "POST",
      body: JSON.stringify({
        query: $("reviewQuery").value.trim() || "Review this contract against our policies",
        contract_text: contractText,
        contract_title: $("contractTitle").value.trim() || "Contract",
        contract_type: $("contractType").value.trim() || "nda",
        use_platform: usePlatform,
        tenant_id: $("sessionTenantId").value.trim() || null,
      }),
    });
    renderReview(data);
  } catch (e) {
    setStatus("Review failed: " + e.message, "err");
    $("rawJson").textContent = e.message;
  } finally {
    disableButtons(false);
  }
}

async function runSync() {
  setStatus("Indexing fixture policies…", "running");
  disableButtons(true);
  try {
    const data = await api("/api/sync", { method: "POST", body: "{}" });
    applyPolicySyncResponse(data);
  } catch (e) {
    setStatus("Fixture sync failed: " + e.message, "err");
    $("rawJson").textContent = e.message;
  } finally {
    disableButtons(false);
  }
}

async function runReview(usePlatform) {
  setStatus(usePlatform ? "Review fixture via platform…" : "Review fixture (direct)…", "running");
  disableButtons(true);
  try {
    const data = await api("/api/review", {
      method: "POST",
      body: JSON.stringify({
        contract_title: "Mutual NDA (Dev UI)",
        contract_type: "nda",
        use_platform: usePlatform,
      }),
    });
    renderReview(data);
  } catch (e) {
    setStatus("Review failed: " + e.message, "err");
    $("rawJson").textContent = e.message;
  } finally {
    disableButtons(false);
  }
}

async function runTombstone() {
  setStatus("Tombstone smoke…", "running");
  disableButtons(true);
  try {
    const data = await api("/api/tombstone", { method: "POST", body: "{}" });
    $("rawJson").textContent = JSON.stringify(data, null, 2);
    setStatus(
      data.deleted_policy_in_hits ? "FAIL — deleted policy still in search" : "Tombstone OK",
      data.deleted_policy_in_hits ? "err" : "ok"
    );
  } catch (e) {
    setStatus("Tombstone failed: " + e.message, "err");
  } finally {
    disableButtons(false);
  }
}

async function runFullE2e() {
  setStatus("Full E2E running (may take several minutes)…", "running");
  disableButtons(true);
  try {
    const data = await api("/api/full-e2e", { method: "POST", body: "{}" });
    $("rawJson").textContent = JSON.stringify(data, null, 2);
    const allOk = (data.steps || []).every((s) => s.ok);
    setStatus(allOk ? "Full E2E passed" : "E2E had failures — see Raw JSON", allOk ? "ok" : "err");
    try {
      const review = await api("/api/outputs/review_result.json");
      renderReview(review);
    } catch {
      /* optional */
    }
  } catch (e) {
    setStatus("E2E failed: " + e.message, "err");
    $("rawJson").textContent = e.message;
  } finally {
    disableButtons(false);
  }
}

document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".tab-pane").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    $("tab-" + btn.dataset.tab).classList.add("active");
  });
});

$("btnSaveConfig").onclick = saveConfig;
$("btnHealth").onclick = checkHealth;
$("btnSync").onclick = runSync;
$("btnReview").onclick = () => runReview(false);
$("btnReviewPlatform").onclick = () => runReview(true);
$("btnTombstone").onclick = runTombstone;
$("btnFullE2e").onclick = runFullE2e;
$("btnAddPolicy").onclick = addPolicyBlock;
$("btnSyncPolicies").onclick = runSyncPolicies;
$("btnLoadSamplePolicies").onclick = loadSamplePolicies;
$("btnReviewText").onclick = () => runReviewText(false);
$("btnReviewTextPlatform").onclick = () => runReviewText(true);
$("btnLoadSampleContract").onclick = loadSampleContract;
$("contractText").oninput = updateContractCharCount;

function updateActiveTenantPreview() {
  const shared = $("useSharedTenant").checked;
  const field = $("sessionTenantId");
  if (!field) return;
  if (shared) {
    field.value = $("tenantId").value.trim() || "e2e-demo";
    field.placeholder = "uses config tenant";
  } else if (!field.dataset.locked) {
    field.value = "";
    field.placeholder = "auto dev-ui-… assigned on sync";
  }
  $("replacePoliciesRow").classList.toggle("hidden", !shared);
  if (!shared) {
    $("replaceTenantPolicies").checked = false;
  }
}

function toggleSharedTenantUi() {
  updateActiveTenantPreview();
}

$("useSharedTenant").onchange = toggleSharedTenantUi;
$("tenantId").oninput = updateActiveTenantPreview;

document.addEventListener("DOMContentLoaded", () => {
  loadSamplePolicies();
  updateActiveTenantPreview();
  updateContractCharCount();
});

(async function init() {
  try {
    const cfg = await api("/api/config");
    $("docUrl").value = cfg.document_server_url;
    $("platformUrl").value = cfg.platform_url;
    $("tenantId").value = cfg.tenant_id;
    updateActiveTenantPreview();
    if (!cfg.llm_configured) {
      setStatus("LLM_API_KEY not set — add to temp_java_sync/.env", "err");
    }
  } catch {
    setStatus("Dev UI loaded", "");
  }
})();
