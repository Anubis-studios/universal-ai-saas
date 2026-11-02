import { api } from "../lib/api.js";
import { $, el, setStatus } from "../components/ui.js";

let activeSkill = null;

export function initSkills(skills) {
  const list = $("#skill-list");
  for (const skill of skills) {
    list.append(el("button", { class: "skill-card", type: "button", onclick: () => openSkill(skill) }, [
      el("span", { class: "cat" }, skill.category),
      el("h3", {}, skill.title || skill.name),
      el("p", {}, skill.description),
    ]));
  }

  $("#skill-cancel").addEventListener("click", () => {
    $("#skill-form").hidden = true;
    $("#skill-output").hidden = true;
    activeSkill = null;
  });

  $("#skill-form").addEventListener("submit", async (e) => {
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
      out.textContent = typeof res.result === "string" ? res.result : JSON.stringify(res.result, null, 2);
    } catch (err) {
      out.textContent = `Error: ${err.message}`;
    }
  });
}

function openSkill(skill) {
  activeSkill = skill;
  $("#skill-form").hidden = false;
  $("#skill-output").hidden = true;
  $("#skill-title").textContent = skill.title || skill.name;
  const fields = $("#skill-fields");
  fields.innerHTML = "";
  const specs = skill.fields || (skill.params || []).map((name) => ({
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
