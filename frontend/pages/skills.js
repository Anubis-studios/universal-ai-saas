import { api } from "../lib/api.js";
import { rememberResult } from "../lib/persist.js";
import { $, el } from "../components/ui.js";

const PAGE = 24;
let activeSkill = null;
let state = { q: "", category: "", offset: 0, total: 0 };

export function initSkills(firstPage) {
  const list = $("#skill-list");
  const more = $("#skill-more");
  const count = $("#skill-count");
  const search = $("#skill-search");
  const category = $("#skill-category");

  if (!search.dataset.wired) {
    search.dataset.wired = "1";
    for (const [name, n] of Object.entries(firstPage.categories || {})) {
      category.append(el("option", { value: name }, `${name} (${n})`));
    }
    $("#skill-total").textContent = (firstPage.total || 0).toLocaleString();

    let timer;
    search.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(() => reload(), 220);
    });
    category.addEventListener("change", () => reload());
    more.addEventListener("click", () => load(state.offset, true));
    $("#skill-cancel").addEventListener("click", () => {
      $("#skill-form").hidden = true;
      $("#skill-output").hidden = true;
      activeSkill = null;
    });
    $("#skill-form").addEventListener("submit", submitSkill);
  }

  function render(page, append) {
    if (!append) list.innerHTML = "";
    for (const skill of page.skills) {
      list.append(el("button", { class: "skill-card", type: "button", onclick: () => openSkill(skill) }, [
        el("span", { class: "cat" }, skill.tier === "builtin" ? `${skill.category} · built-in` : skill.category),
        el("h3", {}, skill.title || skill.name),
        el("p", {}, skill.description || "No description."),
      ]));
    }
    if (!page.skills.length && !append) {
      list.append(el("p", { class: "hint" }, "No skills match that search."));
    }
    state.total = page.total;
    state.offset = (append ? state.offset : 0) + page.skills.length;
    count.textContent = `${state.offset.toLocaleString()} of ${page.total.toLocaleString()} · ${page.mode}`;
    more.hidden = state.offset >= page.total;
    more.textContent = `Load more (${Math.min(PAGE, page.total - state.offset)} left)`;
  }

  async function load(offset, append) {
    try {
      const page = await api.skills({ q: state.q, category: state.category, limit: PAGE, offset });
      render(page, append);
    } catch (err) {
      count.textContent = `Search failed: ${err.message}`;
    }
  }

  function reload() {
    state.q = search.value.trim();
    state.category = category.value;
    load(0, false);
  }

  async function submitSkill(e) {
    e.preventDefault();
    if (!activeSkill) return;
    const params = {};
    for (const input of $("#skill-fields").querySelectorAll("[name]")) {
      params[input.name] = input.type === "number" ? Number(input.value) : input.value;
    }
    const out = $("#skill-output");
    out.hidden = false;
    out.textContent = "Running…";
    try {
      const res = await api.runSkill(activeSkill.name, params);
      rememberResult(res, { kind: "skill", title: activeSkill.name, prompt: JSON.stringify(params) });
      const value = res.result ?? res;
      out.textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
    } catch (err) {
      out.textContent = `Error: ${err.message}`;
    }
  }

  render({ ...firstPage, mode: firstPage.mode || "browse" }, false);
}

function openSkill(skill) {
  activeSkill = skill;
  $("#skill-form").hidden = false;
  $("#skill-output").hidden = true;
  $("#skill-title").textContent = `${skill.title || skill.name}${skill.risk && skill.risk !== "safe" ? ` · ${skill.risk}` : ""}`;
  const fields = $("#skill-fields");
  fields.innerHTML = "";
  const specs = skill.fields && skill.fields.length
    ? skill.fields
    : (skill.params || ["input"]).map((name) => ({
        name, label: name.replace(/[_-]/g, " "), type: "text", required: true,
      }));
  for (const field of specs) {
    const control = field.type === "textarea"
      ? el("textarea", { name: field.name, rows: 5 })
      : el("input", { name: field.name, type: field.type || "text" });
    if (field.placeholder) control.placeholder = field.placeholder;
    if (field.default != null) control.value = field.default;
    control.required = Boolean(field.required);
    fields.append(el("label", {}, [field.label + (field.required ? " *" : ""), control]));
  }
  $("#skill-form").scrollIntoView({ behavior: "smooth", block: "nearest" });
}
