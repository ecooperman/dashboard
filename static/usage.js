// Small helpers shared by the Usage page and the person page: dates in plain words, and the stat cards.
const Usage = (() => {
  const parse = (iso) => (iso ? new Date(iso) : null);

  // "today", "yesterday", "5 days ago", "3 weeks ago", "4 months ago" - or "never".
  function ago(iso) {
    const when = parse(iso);
    if (!when || Number.isNaN(when.getTime())) return "never";
    const days = Math.floor((Date.now() - when.getTime()) / 86400000);
    if (days <= 0) return "today";
    if (days === 1) return "yesterday";
    if (days < 14) return `${days} days ago`;
    if (days < 60) return `${Math.round(days / 7)} weeks ago`;
    return `${Math.round(days / 30)} months ago`;
  }

  const fullDate = (iso) => (iso ? new Date(iso).toLocaleString() : "");

  // One big number with a caption.
  const card = (value, caption) =>
    Global.el("div", { class: "stat-card" }, [Global.el("div", { class: "stat-value", text: String(value) }), Global.el("div", { class: "stat-caption", text: caption })]);

  const count = (n, one, many) => `${n} ${n === 1 ? one : many || one + "s"}`;

  return { ago, fullDate, card, count };
})();
