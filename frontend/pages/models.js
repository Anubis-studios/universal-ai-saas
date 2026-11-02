import { $, el } from "../components/ui.js";

export function initModels(models) {
  const table = $("#models-table");
  const byCategory = models.reduce((acc, m) => {
    (acc[m.category] ||= []).push(m);
    return acc;
  }, {});
  for (const [category, list] of Object.entries(byCategory)) {
    table.append(el("h3", {}, `${category[0].toUpperCase()}${category.slice(1)} (${list.length})`));
    for (const m of list) {
      table.append(el("div", { class: "model-row" }, [
        el("div", {}, [el("div", { class: "name" }, m.name), el("div", { class: "muted" }, m.description)]),
        el("div", { class: "muted" }, `${m.provider} · ${m.model_id}`),
        el("div", {}, m.capabilities.join(", ")),
        el("div", {}, [
          el("span", { class: `pill ${m.available ? "ok" : "off"}` }, m.available ? "available" : "no key"),
        ]),
      ]));
    }
  }
}
