let activityChart = null;
let selectedContact = null;
let selectedRowEl = null;
let bubblesLoaded = false;
let contactsCache = [];
let activityCache = [];
let topicsCache = null;
let bubblesCache = [];
let timelineCache = null;
let timelineLoaded = false;
let timelineBucket = "month";
let timelineLimit = 10;

const TIMELINE_COLORS = ["#3987e5", "#199e70", "#c98500", "#008300", "#9085e9", "#e66767", "#d55181", "#d95926"];
const TIMELINE_MUTED = "rgba(139, 146, 168, 0.35)";

let anonymousMode = localStorage.getItem("anonymousMode") === "1";
const aliasMap = new Map();
let personCounter = 0;
let groupCounter = 0;

const GENERIC_WORDS = ["topic", "thing", "stuff", "plan", "place", "time", "idea", "work", "fun", "food", "day", "night", "chat", "week", "game"];
const GENERIC_PHRASES = ["catch up later", "sounds good", "on my way", "talk soon", "for sure", "no worries", "see you then"];

function $(sel) { return document.querySelector(sel); }
function $$(sel) { return document.querySelectorAll(sel); }

function formatNumber(n) {
  return n.toLocaleString();
}

function formatDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

function resetAliases() {
  aliasMap.clear();
  personCounter = 0;
  groupCounter = 0;
}

function anonName(key, isGroup = false) {
  if (!anonymousMode) return null;
  if (!aliasMap.has(key)) {
    if (isGroup) {
      groupCounter += 1;
      aliasMap.set(key, `Group ${groupCounter}`);
    } else {
      personCounter += 1;
      const letter = String.fromCharCode(65 + ((personCounter - 1) % 26));
      aliasMap.set(key, `Person ${letter}`);
    }
  }
  return aliasMap.get(key);
}

function maskName(key, realName, isGroup = false) {
  return anonymousMode ? anonName(key, isGroup) : realName;
}

function maskCount(n) {
  if (!anonymousMode) return formatNumber(n);
  if (n < 50) return `~${Math.max(10, Math.round(n / 5) * 5)}`;
  if (n < 500) return `~${Math.round(n / 25) * 25}`;
  if (n < 5000) return `~${(Math.round(n / 100) / 10).toFixed(1)}k`;
  return `~${Math.round(n / 500) / 2}k`;
}

function maskDate(iso) {
  if (!iso) return "";
  if (!anonymousMode) return formatDate(iso);
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { month: "short", year: "numeric" });
}

function maskText(text) {
  if (!anonymousMode) return text;
  const words = Math.max(2, Math.min(12, Math.ceil((text || "").length / 18)));
  return Array.from({ length: words }, (_, i) => "████").join(" ");
}

function maskWord(word, index) {
  if (!anonymousMode) return word;
  return GENERIC_WORDS[index % GENERIC_WORDS.length];
}

function maskPhrase(phrase, index) {
  if (!anonymousMode) return phrase;
  return GENERIC_PHRASES[index % GENERIC_PHRASES.length];
}

function showBanner(type, html) {
  const el = $("#status-banner");
  el.className = `banner ${type}`;
  el.innerHTML = html;
  el.classList.remove("hidden");
}

function updateAnonymousUI() {
  document.body.classList.toggle("anonymous-mode", anonymousMode);
  $("#anonymous-toggle").checked = anonymousMode;
  $("#anon-banner").classList.toggle("hidden", !anonymousMode);
}

async function fetchJSON(url) {
  const res = await fetch(url);
  const data = await res.json();
  if (!res.ok) throw new Error(data.message || "Request failed");
  return data;
}

function contactLabel(c) {
  const real = c.display || c.name || c.contact || c.handle;
  return maskName(contactHandle(c), real, !!c.is_group);
}

function contactHandle(c) {
  return c.handle || c.contact;
}

function renderTags(words, container) {
  if (!words || !words.length) {
    container.innerHTML = '<p class="hint">Not enough text messages to analyze.</p>';
    return;
  }
  const max = words[0].count;
  container.innerHTML = words.map(({ word, count }, i) => {
    const size = 0.8 + (count / max) * 0.5;
    const label = maskWord(word, i);
    const countLabel = anonymousMode ? "" : `<strong>${count}</strong>`;
    return `<span class="tag" style="font-size:${size}rem">${countLabel}${escapeHtml(label)}</span>`;
  }).join("");
}

function escapeHtml(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

function renderContacts(contacts) {
  contactsCache = contacts;
  const list = $("#contacts-list");
  if (!contacts.length) {
    list.innerHTML = '<p class="loading">No messages found.</p>';
    return;
  }
  const max = contacts[0].total;
  list.innerHTML = contacts.map((c, i) => {
    const pct = Math.round((c.total / max) * 100);
    const group = c.is_group ? '<span class="badge">group</span>' : "";
    const handle = contactHandle(c);
    const label = contactLabel(c);
    const sub = !c.is_group && !anonymousMode && (c.display || c.name) !== handle
      ? `<div class="contact-sub">${escapeHtml(handle)}</div>` : "";
    return `
      <div class="contact-row" data-contact="${escapeHtml(handle)}" data-is-group="${c.is_group ? "1" : "0"}" data-index="${i}">
        <div class="contact-name">${escapeHtml(label)}${group}${sub}</div>
        <div class="contact-meta">
          ${maskCount(c.sent)} sent · ${maskCount(c.received)} received
          ${c.is_group ? "" : " · DMs only"}
          ${c.last_date ? " · last " + maskDate(c.last_date) : ""}
        </div>
        <div class="contact-count">${maskCount(c.total)}</div>
        <div class="bar-wrap" style="grid-column:1/-1"><div class="bar-fill" style="width:${pct}%"></div></div>
      </div>`;
  }).join("");

  list.querySelectorAll(".contact-row").forEach(row => {
    row.addEventListener("click", () => selectContact(row.dataset.contact, row));
  });

  if (selectedContact) {
    const row = list.querySelector(`[data-contact="${CSS.escape(selectedContact)}"]`);
    if (row) {
      selectContact(selectedContact, row);
      return;
    }
  }

  const firstDm = contacts.find(c => !c.is_group) || contacts[0];
  if (firstDm) {
    const row = list.querySelector(`[data-contact="${CSS.escape(contactHandle(firstDm))}"]`);
    selectContact(contactHandle(firstDm), row);
  }
}

async function selectContact(contact, rowEl, { deep = false } = {}) {
  selectedContact = contact;
  selectedRowEl = rowEl;
  $$(".contact-row").forEach(r => r.classList.toggle("selected", r === rowEl));
  const title = $("#detail-title");
  const content = $("#detail-content");
  title.textContent = "Loading…";
  content.innerHTML = deep
    ? '<p class="loading">Deep scanning every message… this can take a moment.</p>'
    : '<p class="loading">Loading…</p>';

  try {
    const url = `/api/contact/${encodeURIComponent(contact)}${deep ? "?deep=1" : ""}`;
    const data = await fetchJSON(url);
    const { topics, recent } = data;
    const display = maskName(contact, data.display || data.name || contact, !!data.is_group);
    title.textContent = display;

    const total = topics.messages_total;
    const decoded = topics.messages_decoded || topics.message_count || 0;
    const isDeep = !!data.deep;

    let html = `<div class="section-label">Top words${data.is_group ? " (group)" : " (all messages)"}`;
    if (isDeep && total && decoded !== total) {
      html += ` · ${maskCount(decoded)} of ${maskCount(total)} messages analyzed`;
    } else if (isDeep) {
      html += ` · ${maskCount(decoded)} messages analyzed`;
    } else {
      html += ` · quick preview (last ${maskCount(decoded)}${total ? ` of ${maskCount(total)}` : ""})`;
    }
    html += `</div>`;

    if (isDeep) {
      html += `<div class="deep-scan-row"><span class="hint">Deep scan complete — every message analyzed.</span></div>`;
    } else {
      const label = total ? `Deep index — scan all ${maskCount(total)} messages` : "Deep index — scan every message";
      html += `<div class="deep-scan-row"><button id="deep-scan-btn" class="deep-scan-btn">${escapeHtml(label)}</button></div>`;
    }

    html += '<div id="detail-words" class="tag-cloud"></div>';
    if (topics.phrases && topics.phrases.length) {
      html += '<div class="section-label">Common phrases</div>';
      html += '<div class="tag-cloud">' + topics.phrases.map((p, i) =>
        `<span class="tag">${anonymousMode ? "" : `<strong>${p.count}</strong>`}${escapeHtml(maskPhrase(p.phrase, i))}</span>`
      ).join("") + '</div>';
    }
    html += `<div class="section-label">Recent messages${data.is_group ? "" : " (DMs)"}</div><div class="messages-list">`;
    html += recent.map(m => `
      <div class="message-item ${m.is_from_me ? "me" : "them"}">
        <div class="meta">${m.is_from_me ? "You" : "Them"} · ${maskDate(m.date)}</div>
        ${escapeHtml(maskText(m.text))}
      </div>`).join("");
    html += "</div>";

    content.innerHTML = html;
    renderTags(topics.words, $("#detail-words"));

    const deepBtn = $("#deep-scan-btn");
    if (deepBtn) {
      deepBtn.addEventListener("click", () => {
        deepBtn.disabled = true;
        deepBtn.textContent = "Deep scanning…";
        selectContact(contact, selectedRowEl, { deep: true });
      });
    }
  } catch (err) {
    title.textContent = anonymousMode ? "Hidden contact" : contact;
    content.innerHTML = `<p class="hint">Error: ${escapeHtml(err.message)}</p>`;
  }
}

function renderBubbles(people) {
  bubblesCache = people;
  const container = $("#bubble-chart");
  if (!people.length) {
    container.innerHTML = '<p class="loading">No DM data found.</p>';
    return;
  }

  const width = Math.min(container.clientWidth || 900, 900);
  const height = Math.min(width, 560);

  const root = d3.hierarchy({ children: people.map(p => ({ ...p, value: p.total })) })
    .sum(d => d.value || 0)
    .sort((a, b) => (b.value || 0) - (a.value || 0));

  d3.pack().size([width, height]).padding(6)(root);

  const max = people[0].total;
  const color = d3.scaleLinear()
    .domain([0, max])
    .range(["#3d5a99", "#5b8def"]);

  container.innerHTML = "";
  const svg = d3.select(container)
    .append("svg")
    .attr("viewBox", `0 0 ${width} ${height}`)
    .attr("role", "img")
    .attr("aria-label", "Bubble chart of message counts by contact");

  const nodes = svg.selectAll("g")
    .data(root.leaves())
    .join("g")
    .attr("transform", d => `translate(${d.x},${d.y})`);

  nodes.append("circle")
    .attr("class", "bubble")
    .attr("r", d => d.r)
    .attr("fill", d => color(d.data.total))
    .on("click", (_, d) => {
      $$(".tab").forEach(t => t.classList.remove("active"));
      $$(".tab-panel").forEach(p => p.classList.remove("active"));
      $('.tab[data-tab="contacts"]').classList.add("active");
      $("#tab-contacts").classList.add("active");
      const row = $(`.contact-row[data-contact="${CSS.escape(d.data.handle)}"]`);
      selectContact(d.data.handle, row);
    });

  nodes.each(function(d) {
    const g = d3.select(this);
    const realName = d.data.display || d.data.name || d.data.handle;
    const name = maskName(d.data.handle, realName, false);
    const fontSize = Math.max(10, Math.min(16, d.r / 3.2));
    const showCount = d.r > 28;

    if (d.r > 22) {
      const words = name.split(/\s+/);
      const line1 = words.slice(0, 2).join(" ");
      const line2 = words.length > 2 ? words.slice(2).join(" ") : "";
      g.append("text")
        .attr("class", "bubble-label")
        .attr("dy", line2 ? "-0.2em" : "0.35em")
        .style("font-size", `${fontSize}px`)
        .text(line1.length > 18 ? line1.slice(0, 16) + "…" : line1);
      if (line2 && d.r > 36) {
        g.append("text")
          .attr("class", "bubble-label")
          .attr("dy", "1.1em")
          .style("font-size", `${Math.max(9, fontSize - 2)}px`)
          .text(line2.length > 18 ? line2.slice(0, 16) + "…" : line2);
      }
    }

    if (showCount) {
      g.append("text")
        .attr("class", "bubble-count")
        .attr("dy", d.r > 22 ? "2.2em" : "0.35em")
        .text(maskCount(d.data.total));
    }
  });
}

async function loadBubbles() {
  const container = $("#bubble-chart");
  container.innerHTML = '<p class="loading">Building people map…</p>';
  try {
    const data = await fetchJSON("/api/bubbles?limit=35");
    renderBubbles(data.people);
    bubblesLoaded = true;
  } catch (err) {
    container.innerHTML = `<p class="hint">${escapeHtml(err.message)}</p>`;
  }
}

function renderActivity(activity) {
  activityCache = activity;
  const ctx = $("#activity-chart").getContext("2d");
  if (activityChart) activityChart.destroy();

  activityChart = new Chart(ctx, {
    type: "bar",
    data: {
      labels: activity.map(a => a.month),
      datasets: [{
        label: "Messages",
        data: activity.map(a => a.count),
        backgroundColor: "rgba(91, 141, 239, 0.7)",
        borderRadius: 4,
      }],
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: "#8b92a8", maxRotation: 45 }, grid: { color: "#2e3344" } },
        y: {
          ticks: {
            color: "#8b92a8",
            callback: (v) => anonymousMode ? maskCount(v) : formatNumber(v),
          },
          grid: { color: "#2e3344" },
        },
      },
    },
  });
}

function renderTopics(data) {
  topicsCache = data;
  const overall = $("#topics-overall");
  const grid = $("#topics-by-contact");
  renderTags(data.overall.words, overall);

  grid.innerHTML = data.by_contact.map(c => {
    const label = maskName(c.contact, c.display || c.name, false);
    return `
      <div class="card topic-card">
        <h3>${escapeHtml(label)} <span class="badge">${maskCount(c.total)} DMs</span></h3>
        <div class="tag-cloud contact-tags" data-contact="${escapeHtml(c.contact)}"></div>
      </div>`;
  }).join("");

  grid.querySelectorAll(".contact-tags").forEach((el, i) => {
    renderTags(data.by_contact[i].topics.words, el);
  });
}

async function loadTopics() {
  const overall = $("#topics-overall");
  const grid = $("#topics-by-contact");
  overall.innerHTML = '<p class="loading">Analyzing messages…</p>';
  grid.innerHTML = "";

  try {
    const data = await fetchJSON("/api/topics?top=12");
    renderTopics(data);
  } catch (err) {
    overall.innerHTML = `<p class="hint">${escapeHtml(err.message)}</p>`;
  }
}

function niceCeil(n) {
  if (n <= 5) return 5;
  const magnitude = Math.pow(10, Math.floor(Math.log10(n)));
  const residual = n / magnitude;
  let niceResidual;
  if (residual <= 1) niceResidual = 1;
  else if (residual <= 2) niceResidual = 2;
  else if (residual <= 5) niceResidual = 5;
  else niceResidual = 10;
  return niceResidual * magnitude;
}

function periodLabel(period, bucket) {
  if (bucket === "year") return period;
  const [y, m] = period.split("-");
  const d = new Date(Number(y), Number(m) - 1, 1);
  return d.toLocaleDateString(undefined, { month: "short", year: "2-digit" });
}

function renderTimeline(data) {
  timelineCache = data;
  const container = $("#timeline-chart");
  const legend = $("#timeline-legend");
  const contacts = data.contacts || [];

  if (!contacts.length) {
    container.innerHTML = '<p class="loading">No timeline data found.</p>';
    legend.innerHTML = "";
    return;
  }

  const periodSet = new Set();
  contacts.forEach(c => c.series.forEach(s => periodSet.add(s.period)));
  const periods = Array.from(periodSet).sort();

  if (!periods.length) {
    container.innerHTML = '<p class="loading">No dated messages found.</p>';
    legend.innerHTML = "";
    return;
  }

  const width = Math.max(container.clientWidth || 900, 320);
  const height = 360;
  const margin = { top: 16, right: 16, bottom: 32, left: 44 };
  const plotW = width - margin.left - margin.right;
  const plotH = height - margin.top - margin.bottom;

  const maxCount = Math.max(1, ...contacts.flatMap(c => c.series.map(s => s.count)));
  const yMax = niceCeil(maxCount);

  const xStep = periods.length > 1 ? plotW / (periods.length - 1) : 0;
  const xAt = i => margin.left + (periods.length > 1 ? i * xStep : plotW / 2);
  const yAt = v => margin.top + plotH - (v / yMax) * plotH;

  const seriesByContact = contacts.map((c, i) => {
    const byPeriod = new Map(c.series.map(s => [s.period, s.count]));
    const points = periods.map(p => byPeriod.get(p) || 0);
    const color = i < 8 ? TIMELINE_COLORS[i] : TIMELINE_MUTED;
    return { contact: c, points, color, highlighted: i < 8 };
  });

  const svgNS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(svgNS, "svg");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "Message count per period by contact");
  svg.style.height = `${height}px`;

  const ticks = 4;
  for (let t = 0; t <= ticks; t++) {
    const v = (yMax / ticks) * t;
    const y = yAt(v);
    const line = document.createElementNS(svgNS, "line");
    line.setAttribute("x1", margin.left);
    line.setAttribute("x2", width - margin.right);
    line.setAttribute("y1", y);
    line.setAttribute("y2", y);
    line.setAttribute("class", "timeline-grid");
    svg.appendChild(line);

    const label = document.createElementNS(svgNS, "text");
    label.setAttribute("x", margin.left - 8);
    label.setAttribute("y", y);
    label.setAttribute("class", "timeline-axis-label");
    label.setAttribute("text-anchor", "end");
    label.setAttribute("dominant-baseline", "middle");
    label.textContent = anonymousMode ? maskCount(Math.round(v)) : formatNumber(Math.round(v));
    svg.appendChild(label);
  }

  const maxLabels = Math.min(periods.length, Math.max(4, Math.floor(plotW / 70)));
  const labelStep = Math.max(1, Math.ceil(periods.length / maxLabels));
  periods.forEach((p, i) => {
    if (i % labelStep !== 0 && i !== periods.length - 1) return;
    const label = document.createElementNS(svgNS, "text");
    label.setAttribute("x", xAt(i));
    label.setAttribute("y", height - margin.bottom + 18);
    label.setAttribute("class", "timeline-axis-label");
    label.setAttribute("text-anchor", "middle");
    label.textContent = periodLabel(p, data.bucket);
    svg.appendChild(label);
  });

  // Draw muted (overflow) series first so highlighted lines stay on top.
  const ordered = [...seriesByContact].sort((a, b) => (a.highlighted === b.highlighted ? 0 : a.highlighted ? 1 : -1));
  ordered.forEach(s => {
    const d = s.points.map((v, i) => `${i === 0 ? "M" : "L"}${xAt(i)},${yAt(v)}`).join(" ");
    const path = document.createElementNS(svgNS, "path");
    path.setAttribute("d", d);
    path.setAttribute("class", "timeline-line" + (s.highlighted ? "" : " muted"));
    path.setAttribute("stroke", s.color);
    svg.appendChild(path);
  });

  const hit = document.createElementNS(svgNS, "rect");
  hit.setAttribute("x", margin.left);
  hit.setAttribute("y", margin.top);
  hit.setAttribute("width", Math.max(plotW, 1));
  hit.setAttribute("height", Math.max(plotH, 1));
  hit.setAttribute("fill", "transparent");
  svg.appendChild(hit);

  container.innerHTML = "";
  container.appendChild(svg);

  const overlay = document.createElement("div");
  overlay.className = "timeline-overlay";
  const crosshair = document.createElement("div");
  crosshair.className = "timeline-crosshair hidden";
  const tooltip = document.createElement("div");
  tooltip.className = "timeline-tooltip hidden";
  overlay.appendChild(crosshair);
  overlay.appendChild(tooltip);
  container.appendChild(overlay);

  function showTooltip(evt) {
    const rect = svg.getBoundingClientRect();
    const scale = width / rect.width;
    const mouseX = (evt.clientX - rect.left) * scale;
    let idx = xStep > 0 ? Math.round((mouseX - margin.left) / xStep) : 0;
    idx = Math.max(0, Math.min(periods.length - 1, idx));

    const px = (xAt(idx) / width) * rect.width;
    crosshair.style.left = `${px}px`;
    crosshair.classList.remove("hidden");

    const rows = seriesByContact
      .map(s => ({
        name: maskName(s.contact.id, s.contact.name, !!s.contact.is_group),
        color: s.color,
        value: s.points[idx],
      }))
      .filter(r => r.value > 0)
      .sort((a, b) => b.value - a.value);

    const shown = rows.slice(0, 8);
    const extra = rows.length - shown.length;

    tooltip.innerHTML = "";
    const periodEl = document.createElement("div");
    periodEl.className = "timeline-tooltip-period";
    periodEl.textContent = periodLabel(periods[idx], data.bucket);
    tooltip.appendChild(periodEl);

    if (!rows.length) {
      const empty = document.createElement("div");
      empty.className = "timeline-tooltip-empty";
      empty.textContent = "No messages";
      tooltip.appendChild(empty);
    } else {
      shown.forEach(r => {
        const row = document.createElement("div");
        row.className = "timeline-tooltip-row";
        const key = document.createElement("span");
        key.className = "timeline-tooltip-key";
        key.style.background = r.color;
        const name = document.createElement("span");
        name.className = "timeline-tooltip-name";
        name.textContent = r.name;
        const value = document.createElement("span");
        value.className = "timeline-tooltip-value";
        value.textContent = maskCount(r.value);
        row.append(key, name, value);
        tooltip.appendChild(row);
      });
      if (extra > 0) {
        const more = document.createElement("div");
        more.className = "timeline-tooltip-more";
        more.textContent = `+${extra} more`;
        tooltip.appendChild(more);
      }
    }
    tooltip.classList.remove("hidden");

    const tooltipWidth = 200;
    let left = px + 12;
    if (left + tooltipWidth > rect.width) left = px - tooltipWidth - 12;
    tooltip.style.left = `${Math.max(0, left)}px`;
    tooltip.style.top = `${margin.top}px`;
  }

  function hideTooltip() {
    crosshair.classList.add("hidden");
    tooltip.classList.add("hidden");
  }

  svg.addEventListener("pointermove", showTooltip);
  svg.addEventListener("pointerleave", hideTooltip);

  const highlighted = seriesByContact.filter(s => s.highlighted);
  const mutedCount = seriesByContact.length - highlighted.length;
  legend.innerHTML = "";
  highlighted.forEach(s => {
    const item = document.createElement("div");
    item.className = "timeline-legend-item";
    const key = document.createElement("span");
    key.className = "timeline-legend-key";
    key.style.background = s.color;
    const name = document.createTextNode(" " + maskName(s.contact.id, s.contact.name, !!s.contact.is_group) + " ");
    const total = document.createElement("span");
    total.className = "timeline-legend-total";
    total.textContent = maskCount(s.contact.total);
    item.append(key, name, total);
    legend.appendChild(item);
  });
  if (mutedCount > 0) {
    const item = document.createElement("div");
    item.className = "timeline-legend-item muted";
    const key = document.createElement("span");
    key.className = "timeline-legend-key";
    key.style.background = TIMELINE_MUTED;
    const label = document.createTextNode(` +${mutedCount} more`);
    item.append(key, label);
    legend.appendChild(item);
  }
}

async function loadTimeline() {
  const container = $("#timeline-chart");
  const legend = $("#timeline-legend");
  container.innerHTML = '<p class="loading">Loading timeline…</p>';
  legend.innerHTML = "";
  try {
    const data = await fetchJSON(`/api/timeline?limit=${timelineLimit}&bucket=${timelineBucket}`);
    timelineLoaded = true;
    renderTimeline(data);
  } catch (err) {
    container.innerHTML = `<p class="hint">${escapeHtml(err.message)}</p>`;
  }
}

function setupTimelineControls() {
  $$("#timeline-bucket-toggle .toggle-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      if (btn.classList.contains("active")) return;
      $$("#timeline-bucket-toggle .toggle-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      timelineBucket = btn.dataset.value;
      loadTimeline();
    });
  });
  $$("#timeline-limit-toggle .toggle-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      if (btn.classList.contains("active")) return;
      $$("#timeline-limit-toggle .toggle-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      timelineLimit = Number(btn.dataset.value);
      loadTimeline();
    });
  });
}

function refreshAnonymousViews() {
  if (contactsCache.length) renderContacts(contactsCache);
  if (activityCache.length) renderActivity(activityCache);
  if (topicsCache) renderTopics(topicsCache);
  if (bubblesCache.length) renderBubbles(bubblesCache);
  if (timelineCache) renderTimeline(timelineCache);
}

function setupAnonymousToggle() {
  updateAnonymousUI();
  $("#anonymous-toggle").addEventListener("change", (e) => {
    anonymousMode = e.target.checked;
    localStorage.setItem("anonymousMode", anonymousMode ? "1" : "0");
    resetAliases();
    updateAnonymousUI();
    refreshAnonymousViews();
  });
}

function setupTabs() {
  $$(".tab").forEach(tab => {
    tab.addEventListener("click", () => {
      $$(".tab").forEach(t => t.classList.remove("active"));
      $$(".tab-panel").forEach(p => p.classList.remove("active"));
      tab.classList.add("active");
      $(`#tab-${tab.dataset.tab}`).classList.add("active");
      if (tab.dataset.tab === "topics" && !topicsCache) {
        loadTopics();
      }
      if (tab.dataset.tab === "bubbles" && !bubblesLoaded) {
        loadBubbles();
      }
      if (tab.dataset.tab === "timeline" && !timelineLoaded) {
        loadTimeline();
      }
    });
  });
}

function renderSummaryStats(s) {
  $("#summary-stats").innerHTML = `
    <div class="stat-card"><div class="value">${maskCount(s.messages)}</div><div class="label">Messages</div></div>
    <div class="stat-card"><div class="value">${maskCount(s.handles)}</div><div class="label">Contacts</div></div>
    <div class="stat-card"><div class="value">${maskCount(s.chats)}</div><div class="label">Chats</div></div>`;
}

async function init() {
  setupAnonymousToggle();
  setupTabs();
  setupTimelineControls();

  try {
    const status = await fetchJSON("/api/status");
    if (!status.ok) throw new Error(status.message);

    renderSummaryStats(status.summary);

    const [contactsData, activityData] = await Promise.all([
      fetchJSON("/api/contacts?limit=50"),
      fetchJSON("/api/activity"),
    ]);

    renderContacts(contactsData.contacts);
    renderActivity(activityData.activity);
  } catch (err) {
    const isPerm = err.message.includes("Full Disk Access");
    showBanner("error", isPerm
      ? `<strong>Permission required.</strong> Grant <strong>Full Disk Access</strong> to Terminal (or Cursor) in System Settings → Privacy & Security → Full Disk Access, then restart this app.<br><br><code>${escapeHtml(err.message)}</code>`
      : escapeHtml(err.message));
    $("#contacts-list").innerHTML = "";
  }
}

init();
