// The Usage page's chart: how many people used the apps on each of the last 30 days (a person counts once per app per day). One series,
// so no legend; the tooltip and the table below the chart carry the per-app numbers. Drawn at the width it is given and again when that
// changes. Colours are the theme's tokens, so dark mode follows.
const Trend = (() => {
  const NS = "http://www.w3.org/2000/svg";
  const svg = (tag, attrs = {}, children = []) => {
    const node = document.createElementNS(NS, tag);
    for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, String(value));
    node.append(...children);
    return node;
  };

  const shortDate = (day) => new Date(`${day}T00:00:00Z`).toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" });
  const longDate = (day) => new Date(`${day}T00:00:00Z`).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" });
  const people = (n) => `${n} ${n === 1 ? "person" : "people"}`;

  // A step for the axis that gives a few round whole numbers: 1, 2, 5, 10, 20, 50...
  function stepFor(max) {
    for (const step of [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000]) if (max / step <= 4) return step;
    return 1000;
  }

  // The chart for `trend` ({days, apps: [{name, counts}], total, since}); returns {node, redraw}.
  function chart(trend) {
    const host = Global.el("div", { class: "trend" });
    const plot = Global.el("div", { class: "trend-plot" });
    const tip = Global.el("div", { class: "trend-tip hidden", role: "status" });
    host.append(plot, tip);

    function showTip(index, x, top) {
      const rows = trend.apps.filter((app) => app.counts[index] > 0);
      tip.replaceChildren(
        Global.el("div", { class: "trend-tip-value", text: people(trend.total[index]) }),
        Global.el("div", { class: "trend-tip-day", text: longDate(trend.days[index]) }),
        ...rows.map((app) => Global.el("div", { class: "trend-tip-row", text: `${app.name}: ${app.counts[index]}` }))
      );
      tip.classList.remove("hidden");
      const half = tip.offsetWidth / 2;
      tip.style.left = `${Math.max(0, Math.min(host.clientWidth - tip.offsetWidth, x - half))}px`;
      tip.style.top = `${Math.max(0, top - tip.offsetHeight - 6)}px`;
    }

    function redraw() {
      const width = Math.max(260, host.clientWidth || 640);
      const height = 190;
      const left = 28;
      const bottom = 22;
      const top = 8;
      const right = 4;
      const days = trend.days.length;
      const step = stepFor(Math.max(1, ...trend.total));
      const ceiling = Math.max(step, Math.ceil(Math.max(1, ...trend.total) / step) * step);
      const slot = (width - left - right) / days;
      const barWidth = Math.max(2, Math.min(24, slot - 2));
      const y = (value) => top + (height - top - bottom) * (1 - value / ceiling);
      const nodes = [];
      for (let tick = 0; tick <= ceiling; tick += step) {
        nodes.push(svg("line", { x1: left, x2: width - right, y1: y(tick), y2: y(tick), class: "trend-grid" }));
        nodes.push(svg("text", { x: left - 6, y: y(tick) + 4, class: "trend-axis", "text-anchor": "end" }, [document.createTextNode(String(tick))]));
      }
      for (const index of [0, Math.floor((days - 1) / 2), days - 1]) {
        const cx = left + slot * index + slot / 2;
        const anchor = index === 0 ? "start" : index === days - 1 ? "end" : "middle";
        nodes.push(svg("text", { x: anchor === "start" ? left : anchor === "end" ? width - right : cx, y: height - 6, class: "trend-axis", "text-anchor": anchor }, [document.createTextNode(shortDate(trend.days[index]))]));
      }
      trend.total.forEach((value, index) => {
        const cx = left + slot * index + slot / 2;
        const x = cx - barWidth / 2;
        const barTop = y(value);
        const base = y(0);
        const group = svg("g", { class: "trend-day", tabindex: 0, role: "img", "aria-label": `${longDate(trend.days[index])}: ${people(value)}` });
        if (value > 0) {
          const r = Math.min(4, barWidth / 2, base - barTop);
          // A bar that grows from the baseline: square at the bottom, rounded at the top.
          group.append(svg("path", {
            class: "trend-bar",
            d: `M${x},${base} V${barTop + r} Q${x},${barTop} ${x + r},${barTop} H${x + barWidth - r} Q${x + barWidth},${barTop} ${x + barWidth},${barTop + r} V${base} Z`,
          }));
        }
        group.append(svg("rect", { x: left + slot * index, y: top, width: slot, height: height - top - bottom, class: "trend-hit" })); // the hit area is the whole day
        const show = () => showTip(index, cx, Math.min(barTop, y(0) - 4));
        group.addEventListener("pointerenter", show);
        group.addEventListener("focus", show);
        group.addEventListener("pointerleave", () => tip.classList.add("hidden"));
        group.addEventListener("blur", () => tip.classList.add("hidden"));
        nodes.push(group);
      });
      plot.replaceChildren(svg("svg", { width, height, viewBox: `0 0 ${width} ${height}`, role: "group", "aria-label": "People active per day, last 30 days" }, nodes));
    }

    let last = 0;
    new ResizeObserver(() => {
      if (host.clientWidth && host.clientWidth !== last) {
        last = host.clientWidth;
        redraw();
      }
    }).observe(host);
    return { node: host, redraw };
  }

  // The same numbers as a table (reachable without hovering), newest day first.
  function table(trend) {
    const head = Global.el("tr", {}, [Global.el("th", { text: "Day" }), Global.el("th", { text: "Together" }), ...trend.apps.map((app) => Global.el("th", { text: app.name }))]);
    const body = trend.days
      .map((day, index) => ({ day, index }))
      .reverse()
      .map(({ day, index }) => Global.el("tr", {}, [Global.el("td", { text: longDate(day) }), Global.el("td", { text: String(trend.total[index]) }), ...trend.apps.map((app) => Global.el("td", { text: String(app.counts[index]) }))]));
    return Global.el("details", { class: "trend-table" }, [
      Global.el("summary", { text: "Show as a table" }),
      Global.el("div", { class: "trend-table-scroll" }, [Global.el("table", { class: "trend-grid-table" }, [Global.el("thead", {}, [head]), Global.el("tbody", {}, body)])]),
    ]);
  }

  // Everything for the section: the chart, a note about where the history starts, the table. Nothing recorded yet is said, not drawn empty.
  function section(trend) {
    const empty = !trend.total.some((n) => n > 0);
    if (empty) {
      return [Global.el("p", { class: "field-hint", text: "Nothing has been recorded yet. Each app starts counting the first time someone uses it after its next update, so the first days will look quiet." })];
    }
    const drawn = chart(trend);
    const note = Global.el("p", { class: "field-hint", text: `A person counts once per app per day. Recorded since ${shortDate(trend.since)}; before that nothing was kept.` });
    return [drawn.node, note, table(trend)];
  }

  return { section };
})();
