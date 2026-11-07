const state = {
  csrf: "",
  providers: [],
  profiles: [],
  selectedProviderId: "",
};

const colors = ["#2563eb", "#0f766e", "#c2410c", "#7c3aed", "#b45309", "#0369a1", "#be123c", "#4d7c0f", "#475569"];
const byId = (id) => document.getElementById(id);

function escapeText(value) {
  const node = document.createElement("span");
  node.textContent = String(value ?? "");
  return node.textContent;
}

function toast(message, kind = "info") {
  const element = byId("toast");
  element.textContent = message;
  element.dataset.kind = kind;
  element.classList.add("visible");
  window.setTimeout(() => element.classList.remove("visible"), 3200);
}

function selectedProvider() {
  return state.providers.find((provider) => provider.provider_id === state.selectedProviderId);
}

function providerColor(providerId) {
  const index = Math.max(0, state.providers.findIndex((provider) => provider.provider_id === providerId));
  return colors[index % colors.length];
}

function reviewedCapabilities(provider) {
  return Object.entries(provider.capability_status || {})
    .filter(([, status]) => status === "reviewed")
    .map(([capability]) => capability);
}

function providerSearchText(provider) {
  return [
    provider.provider_id,
    provider.label,
    provider.group_label,
    ...(provider.search_terms || []),
    ...provider.models.map((model) => model.model),
    ...reviewedCapabilities(provider),
  ].join(" ").toLowerCase();
}

function renderProviders(query = "") {
  const grid = byId("provider-grid");
  grid.replaceChildren();
  const normalized = query.trim().toLowerCase();
  const capability = byId("capability-filter").value;
  const visible = state.providers.filter((provider) => {
    const queryMatches = !normalized || providerSearchText(provider).includes(normalized);
    const capabilityMatches = !capability || provider.capability_status?.[capability] === "reviewed";
    return queryMatches && capabilityMatches;
  });
  if (!visible.length) {
    const empty = document.createElement("p");
    empty.className = "provider-search-empty";
    empty.textContent = "没有匹配的审核预设";
    grid.append(empty);
    return;
  }
  const grouped = new Map();
  for (const provider of visible) {
    const group = grouped.get(provider.group_label) || [];
    group.push(provider);
    grouped.set(provider.group_label, group);
  }
  for (const [groupLabel, providers] of grouped) {
    const section = document.createElement("section");
    section.className = "provider-group";
    const heading = document.createElement("h3");
    heading.textContent = `${groupLabel} · ${providers.length}`;
    const cards = document.createElement("div");
    cards.className = "provider-group-grid";
    section.append(heading, cards);
    grid.append(section);
    for (const provider of providers) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `provider-card${provider.provider_id === state.selectedProviderId ? " selected" : ""}`;
    button.setAttribute("aria-pressed", String(provider.provider_id === state.selectedProviderId));
    button.addEventListener("click", () => selectProvider(provider.provider_id));

    const swatch = document.createElement("span");
    swatch.className = "provider-swatch";
    swatch.style.backgroundColor = providerColor(provider.provider_id);
    const text = document.createElement("span");
    text.className = "provider-card-text";
    const label = document.createElement("strong");
    label.textContent = provider.label;
    const meta = document.createElement("span");
    const capabilities = reviewedCapabilities(provider);
    const setup = provider.setup_mode === "api_key_only" ? "只填 Key" : "需额外字段";
    meta.textContent = `${provider.models.length} 个预设 · ${capabilities.join(" / ") || "待审核"} · ${setup}`;
    text.append(label, meta);
    const marker = document.createElement("span");
    marker.className = "select-marker";
    marker.textContent = provider.provider_id === state.selectedProviderId ? "已选" : "选择";
    button.append(swatch, text, marker);
    cards.append(button);
    }
  }
}

function renderProviderFields(provider) {
  const container = byId("provider-fields");
  container.replaceChildren();
  const fields = [...(provider.required_fields || []), ...(provider.optional_fields || [])];
  for (const field of fields) {
    const label = document.createElement("label");
    label.htmlFor = `provider-field-${field.field_id}`;
    label.textContent = field.label;
    const input = document.createElement("input");
    input.id = `provider-field-${field.field_id}`;
    input.dataset.providerField = field.field_id;
    input.type = field.input_type === "url" ? "url" : "text";
    input.placeholder = field.placeholder || "";
    input.required = (provider.required_fields || []).some((item) => item.field_id === field.field_id);
    input.disabled = provider.onboarding_status !== "ready";
    container.append(label, input);
    if (field.help) {
      const help = document.createElement("small");
      help.textContent = field.help;
      container.append(help);
    }
  }
}

function selectProvider(providerId) {
  state.selectedProviderId = providerId;
  const provider = selectedProvider();
  renderProviders(byId("provider-search").value);
  byId("selected-swatch").style.backgroundColor = providerColor(providerId);
  byId("selected-label").textContent = provider.label;
  byId("selected-destination").textContent = provider.destination;
  byId("advanced-base-url").textContent = provider.base_url;
  byId("advanced-protocol").textContent = provider.protocols.join(" / ");
  byId("advanced-group").textContent = provider.group_label;
  byId("advanced-source").textContent = provider.evidence_sources.map((source) => `${source.project} ${source.version || source.commit?.slice(0, 8) || ""}`).join("；");
  const ready = provider.onboarding_status === "ready";
  byId("setup-mode-badge").textContent = provider.setup_mode === "api_key_only" ? "Key-only" : "需额外字段";
  byId("api-key").disabled = !ready;
  byId("save-profile").disabled = !ready;
  byId("provider-blocked").hidden = ready;
  byId("provider-blocked").textContent = ready ? "" : `${provider.note || "该模板尚不可生成可执行 Profile。"} 当前状态：${provider.onboarding_status}`;
  renderProviderFields(provider);
  const modelSelect = byId("model-preset");
  modelSelect.replaceChildren();
  modelSelect.disabled = !ready || !provider.models.length;
  for (const model of provider.models) {
    const option = document.createElement("option");
    option.value = model.preset_id;
    option.textContent = `${model.model} · ${model.capabilities.join(" / ")}`;
    option.selected = model.preset_id === provider.recommended_preset_id;
    modelSelect.append(option);
  }
  if (!provider.models.length) {
    const option = document.createElement("option");
    option.textContent = "暂无可执行审核预设";
    option.value = "";
    modelSelect.append(option);
  }
  updateCapabilities();
}

function updateCapabilities() {
  const provider = selectedProvider();
  if (!provider) return;
  const model = provider.models.find((item) => item.preset_id === byId("model-preset").value) || provider.models[0];
  byId("advanced-capabilities").textContent = model ? model.capabilities.join(" / ") : "待审核";
}

function renderProfiles() {
  const body = byId("profiles-body");
  body.replaceChildren();
  byId("profile-count").textContent = `${state.profiles.length} 项`;
  byId("profiles-empty").hidden = state.profiles.length > 0;
  for (const profile of state.profiles) {
    const row = document.createElement("tr");
    const values = [
      profile.profile_id,
      profile.provider_id,
      profile.model,
      profile.capabilities.join(" / "),
      profile.auth_ready ? "已存入系统凭据库" : "凭据待补",
    ];
    values.forEach((value, index) => {
      const cell = document.createElement("td");
      cell.textContent = escapeText(value);
      if (index === 4) cell.className = profile.auth_ready ? "credential-ready" : "credential-missing";
      row.append(cell);
    });
    body.append(row);
  }
}

async function load() {
  const response = await fetch("/api/v1/bootstrap", { cache: "no-store" });
  if (!response.ok) throw new Error("无法读取配置台状态");
  const payload = await response.json();
  state.csrf = payload.csrf_token;
  state.providers = payload.catalog.providers;
  state.profiles = payload.profiles;
  if (state.providers.length) selectProvider(state.providers[0].provider_id);
  renderProfiles();
}

async function saveProfile(event) {
  event.preventDefault();
  const provider = selectedProvider();
  if (!provider) return toast("请先选择供应商", "error");
  if (provider.onboarding_status !== "ready") return toast("该供应商仍需受审核的本机配置模板", "error");
  const apiKey = byId("api-key").value;
  if (!apiKey.trim()) return toast("请填写 API Key", "error");
  const submit = byId("save-profile");
  submit.disabled = true;
  submit.textContent = "保存中";
  try {
    const response = await fetch("/api/v1/profiles", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-MPG-CSRF": state.csrf },
      body: JSON.stringify({
        provider_id: provider.provider_id,
        preset_id: byId("model-preset").value,
        profile_id: byId("profile-id").value.trim() || undefined,
        api_key: apiKey,
        provider_fields: Object.fromEntries(
          [...document.querySelectorAll("[data-provider-field]")]
            .map((input) => [input.dataset.providerField, input.value.trim()])
            .filter(([, value]) => value),
        ),
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "保存失败");
    byId("api-key").value = "";
    const refreshed = await fetch("/api/v1/bootstrap", { cache: "no-store" }).then((item) => item.json());
    state.profiles = refreshed.profiles;
    renderProfiles();
    toast("配置已保存，尚未执行任何模型调用", "success");
  } catch (error) {
    toast(error.message || "保存失败", "error");
  } finally {
    submit.disabled = false;
    submit.textContent = "安全保存";
  }
}

async function runMockSmoke() {
  const button = byId("mock-smoke");
  button.disabled = true;
  button.textContent = "自检中";
  try {
    const response = await fetch("/api/v1/mock-smoke", { method: "POST", headers: { "X-MPG-CSRF": state.csrf } });
    const payload = await response.json();
    if (!response.ok || !payload.ok) throw new Error("本地自检未通过");
    toast("本地回环自检通过，没有访问外部 Provider", "success");
  } catch (error) {
    toast(error.message || "本地自检未通过", "error");
  } finally {
    button.disabled = false;
    button.textContent = "运行本地自检";
  }
}

byId("provider-search").addEventListener("input", (event) => renderProviders(event.target.value));
byId("capability-filter").addEventListener("change", () => renderProviders(byId("provider-search").value));
byId("model-preset").addEventListener("change", updateCapabilities);
byId("setup-form").addEventListener("submit", saveProfile);
byId("mock-smoke").addEventListener("click", runMockSmoke);
byId("toggle-key").addEventListener("click", () => {
  const input = byId("api-key");
  input.type = input.type === "password" ? "text" : "password";
  byId("toggle-key").textContent = input.type === "password" ? "显示" : "隐藏";
});

load().catch((error) => toast(error.message, "error"));
