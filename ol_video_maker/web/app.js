// OL Video Maker: sidan i appfönstret. Läget (filmer, markeringar …) finns i Python-servern;
// sidan frågar efter det med /api/state och skickar knapptryck till /api/<åtgärd>.
"use strict";

const TOKEN = new URLSearchParams(location.search).get("k") || "";
let S = null;            // senaste läget från servern
let current = 0;         // vilken sida som visas
let page = null;         // den visade sidans objekt (build/update)
let lastErrSeq = 0;      // fel från en video visas bara en gång

// ---------------------------------------------------------------------------
// Hjälpfunktioner
// ---------------------------------------------------------------------------

const $ = (id) => document.getElementById(id);

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "html") el.innerHTML = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

function esc(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
}

// 02:15,4 (svenskt decimaltecken), som format_time i core.py
function fmt(t) {
  if (t === null || t === undefined) return "–";
  t = Math.max(0, t);
  let tenths = Math.round(t * 10);
  const h = Math.floor(tenths / 36000); tenths -= h * 36000;
  const m = Math.floor(tenths / 600); tenths -= m * 600;
  const s = (tenths / 10).toFixed(1).padStart(4, "0").replace(".", ",");
  const mm = String(m).padStart(2, "0");
  return h ? `${h}:${mm}:${s}` : `${mm}:${s}`;
}

function fmtLong(sec) {
  let s = Math.round(sec);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  s = s % 60;
  if (h) return `${h} tim ${m} min`;
  if (m) return `${m} min ${s} s`;
  return `${s} s`;
}

function fmtEstimate(sec) {
  if (sec === null || sec === undefined) return "beräknar tid…";
  if (sec < 60) return "under 1 minut";
  const m = Math.round(sec / 60);
  return m < 60 ? `ca ${m} min` : `ca ${Math.floor(m / 60)} tim ${m % 60} min`;
}

const MONTHS = ["jan", "feb", "mar", "apr", "maj", "jun", "jul", "aug", "sep", "okt", "nov", "dec"];
function clock(t, seconds = true) {
  const d = new Date(t * 1000), p = (n) => String(n).padStart(2, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}` + (seconds ? `:${p(d.getSeconds())}` : "");
}
function day(t) { const d = new Date(t * 1000); return `${d.getDate()} ${MONTHS[d.getMonth()]}`; }

function setRange(input, v) {
  input.value = v;
  paintRange(input);
}
function paintRange(input) {
  const min = +input.min, max = +input.max;
  const p = max > min ? ((+input.value - min) / (max - min)) * 100 : 0;
  input.style.setProperty("--p", p + "%");
}

// ---------------------------------------------------------------------------
// Prata med servern
// ---------------------------------------------------------------------------

async function api(name, body) {
  let res;
  try {
    const r = await fetch("/api/" + name, {
      method: "POST", headers: {"Content-Type": "application/json", "X-OLVM-Token": TOKEN},
      body: JSON.stringify(body || {}),
    });
    res = await r.json();
  } catch (e) {
    return {};
  }
  if (res.state) setState(res.state);
  if (res.error) showModal(res.error.title, `<p>${esc(res.error.text)}</p>`, [{text: "OK", primary: true}]);
  if (res.confirm) {
    const c = res.confirm;
    let body = `<p>${esc(c.text)}</p>`;
    if (c.list) body += "<ul>" + c.list.map((x) => `<li>${esc(x)}</li>`).join("") + "</ul>";
    if (c.question) body += `<p>${esc(c.question)}</p>`;
    showModal(c.title, body, [{text: "Avbryt"}, {text: c.yes || "Ja", primary: true,
      run: () => api(c.action, c.body)}]);
  }
  return res;
}

function url(path, params) {
  const q = new URLSearchParams({...params, k: TOKEN});
  return `${path}?${q}`;
}

let polling = false;
async function poll() {
  if (polling) return;
  polling = true;
  try {
    const r = await fetch(url("/api/state", {}));
    if (r.ok) setState(await r.json());
  } catch (e) { /* servern är stängd */ }
  polling = false;
}
function schedulePoll() {
  const busy = S && (S.running || S.loading.headcam || S.loading.livelox || S.loading.fit ||
                     S.benchRunning);
  setTimeout(async () => { await poll(); schedulePoll(); }, busy ? 300 : 1200);
}

function setState(s) {
  const first = S === null;
  S = s;
  // Första start: villkoren måste godkännas innan något annat visas.
  if (!S.termsAccepted) { if (!termsShown) showTerms(); return; }
  if (termsShown) { leaveTerms(); show(0); return; }
  const r = S.render;
  if (r && r.ok === false && r.error && r.seq !== lastErrSeq) {
    lastErrSeq = r.seq;
    showModal("Något gick fel", `<p>${esc(r.error)}</p>`, [{text: "OK", primary: true}]);
  }
  if (first) show(0); else refresh();
}

// ---------------------------------------------------------------------------
// Villkoren (visas före steg 1 första gången, skiss A från UX-designern)
// ---------------------------------------------------------------------------

let termsShown = false;

// Villkorstexten använder bara **fet** och *kursiv*.
function inline(s) {
  return esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/\*(.+?)\*/g, "<i>$1</i>");
}

function showTerms() {
  const t = S.terms;
  termsShown = true;
  document.body.classList.add("terms-mode");
  $("terms-link").classList.add("cur");
  hideModal();
  $("kicker").textContent = t.title;
  $("optional").hidden = true;
  $("title").textContent = "Läs och godkänn villkoren";
  $("lead").innerHTML = esc(t.intro) + " Du behöver bara göra detta första gången.";
  // Sidomenyn: stegen syns men är låsta.
  const ul = $("steps");
  ul.innerHTML = "";
  NAMES.forEach((name, j) => {
    if (PAGES[j].sub) return;
    ul.append(h("li", {class: "todo locked dim"}, h("span", {class: "ctrl"}, PAGES[j].number), name));
  });
  const list = h("ol", {class: "terms"});
  t.points.forEach((p, i) => {
    const li = h("li", {}, h("span", {class: "tn"}, `${i + 1}.`), h("b", {html: inline(p.title)}));
    for (const b of p.blocks) {
      if (b.type === "ul") li.append(h("ul", {}, b.content.map((x) => h("li", {html: inline(x)}))));
      else li.append(h("p", {html: inline(b.content)}));
    }
    list.append(li);
  });
  // Rullar listan? Visa märket tills man har rullat till botten.
  const card = h("div", {class: "termcard"}, list);
  const fade = h("div", {class: "fade"}, h("span", {}, `Rulla ner för att läsa alla ${t.points.length} punkter ↓`));
  const checkFade = () => {
    fade.hidden = card.scrollTop + card.clientHeight >= card.scrollHeight - 4;
  };
  card.addEventListener("scroll", checkFade);
  window.addEventListener("resize", () => { if (termsShown) checkFade(); });
  const box = h("input", {type: "checkbox", id: "terms-ok"});
  const accept = h("button", {class: "btnp", disabled: true, onclick: async () => {
    if (!box.checked) return;
    accept.disabled = true;
    await api("accept_terms", {version: t.version});
    if (!S.termsAccepted) accept.disabled = !box.checked;
  }}, t.accept + " ›");
  box.addEventListener("change", () => {
    accept.disabled = !box.checked;
    agree.classList.toggle("on", box.checked);
  });
  const agree = h("label", {class: "agree", for: "terms-ok"}, box, h("span", {class: "cb"}), t.checkbox);
  const decline = h("button", {class: "btng", onclick: quitApp}, t.decline);
  const root = $("page");
  root.innerHTML = "";
  root.append(
    h("div", {class: "termwrap"}, card, fade),
    h("div", {class: "tfoot"},
      h("div", {class: "act"}, h("span", {class: "anum"}, "1"), agree),
      h("div", {class: "trow"}, decline, h("span", {class: "spacer"}),
        h("div", {class: "act"}, h("span", {class: "anum"}, "2"), accept))));
  checkFade();
}

function leaveTerms() {
  termsShown = false;
  document.body.classList.remove("terms-mode");
  $("terms-link").classList.remove("cur");
}

// "Avsluta": stäng programmet utan att spara något.
async function quitApp() {
  await api("decline_terms");
  window.close();
  // Gick fönstret inte att stänga (stängs annars av programmet strax efter).
  $("page").innerHTML = "";
  $("title").textContent = "Programmet är avslutat";
  $("lead").textContent = "Du kan stänga fönstret.";
}

// ---------------------------------------------------------------------------
// Ruta ovanpå (fel, frågor, hjälp)
// ---------------------------------------------------------------------------

function showModal(title, html, buttons) {
  $("modal").querySelector(".dialog").classList.remove("llguide");
  $("m-title").textContent = title;
  $("m-body").innerHTML = html;
  const row = $("m-btns");
  row.innerHTML = "";
  for (const b of buttons) {
    row.append(h("button", {class: "big" + (b.primary ? "" : " second"), onclick: () => {
      hideModal();
      if (b.run) b.run();
    }}, b.text));
  }
  $("modal").hidden = false;
  const last = row.lastChild;
  if (last) last.focus();
}
function hideModal() { $("modal").hidden = true; $("modal").querySelector(".dialog").classList.remove("llguide"); }

// Livelox-instruktionen: alla nio steg i en lista man rullar i (skiss A, ux-design/livelox).
// Skärmbilderna kommer från Livelox; namnen i deltagarlistan är utbytta mot påhittade.
const LIVELOX_GUIDE = [
  {group: "I Livelox", text: "<b>Välj löparen</b> som ska visas. Kryssa i dig själv i listan <b>Participants</b>.",
   more: "Vill du visa flera? Kryssa i dem också.",
   pic: `<img src="/static/livelox/deltagare.png" width="99" height="160" alt="Listan Participants i Livelox">`},
  {text: "<b>Välj uppspelningshastighet 1x.</b> Dra reglaget längst ner tills det står 1x.",
   more: "Annars går kartan inte i takt med headcam-filmen.",
   pic: `<img src="/static/livelox/hastighet.png" width="118" alt="Reglaget för hastighet">`},
  {text: "<b>Välj Follow selected participant.</b> Klicka på knappen uppe till vänster på kartan och välj det nedersta alternativet.",
   more: "Då följer kartan med dig hela loppet.",
   pic: `<img src="/static/livelox/folj.png" width="241" alt="Menyn för att följa löpare"><span class="ring" style="left:0.7%;top:76%;width:98.6%;height:20.5%"></span>`},
  {group: "Spela in med Windows",
   text: `<b>Öppna skärminspelningen.</b> Tryck <kbd>⊞ Windows</kbd><span class="plus">+</span><kbd>Shift</kbd><span class="plus">+</span><kbd>S</kbd>`,
   more: "Klippverktyget öppnas. Det ingår i Windows 11. Har du Windows 10? Läs i guiden hur du gör med Xbox Game Bar."},
  {text: "<b>Välj Video</b> (kameran) och dra en <b>smal, hög rektangel</b> över kartan. Den ska täcka hela sträckan från start till kontroll 1.",
   more: "Rekommenderad storlek: <span class=\"pill\">640 × 1080 pixlar</span>, lika stort som kartan i videon på YouTube. " +
         "Får den inte plats? Tryck <kbd>F11</kbd> eller välj <span class=\"pill\">540 × 900</span>. " +
         "Zooma in så att kartan syns tydligt, men så att din längsta sträcka får plats.",
   pic: `<img src="/static/livelox/rektangel.svg" width="300" alt="En smal, hög rektangel över kartan">`},
  {text: "<b>Starta videoinspelningen.</b> Klicka på <b>Starta</b> i Klippverktyget."},
  {text: "<b>Starta uppspelningen i Livelox.</b> Klicka på play-knappen."},
  {group: "Medan den spelar in", text: "<b>Vänta tills hela banan är klar.</b> Det tar lika lång tid som ditt lopp.",
   more: "Ut och träna så länge! 🏃"},
  {text: "<b>Stoppa inspelningen</b> när du har gått i mål på kartan. Windows sparar filmen i mappen " +
         "<b>Skärminspelningar</b> under <b>Videoklipp</b>.",
   more: "Välj sedan den filen i steg 1 här i programmet."},
];
const FIT_STEPS = [
  "Gå till connect.garmin.com i webbläsaren och logga in.",
  "Öppna aktiviteten för ditt lopp.",
  "Klicka på kugghjulet uppe till höger och välj Exportera original.",
  "Du får en zip-fil. Välj den direkt här i programmet (eller packa upp .fit-filen först).",
];
function stepsModal(title, intro, steps, foot) {
  showModal(title, `<p>${esc(intro)}</p><ol>${steps.map((s) => `<li>${esc(s)}</li>`).join("")}</ol>` +
            `<p class="foot">${esc(foot)}</p>`,
            [{text: "Öppna hela guiden", run: () => api("open_help")}, {text: "Stäng", primary: true}]);
}
function liveloxHelp() {
  showModal("Så spelar du in Livelox", "", []);
  $("modal").querySelector(".dialog").classList.add("llguide");
  const list = h("div", {class: "llscroll", tabindex: 0});
  LIVELOX_GUIDE.forEach((x, i) => {
    if (x.group) list.append(h("div", {class: "llgrp"}, x.group));
    list.append(h("div", {class: "llstep"},
      h("span", {class: "llnum"}, i + 1),
      h("div", {class: "lltext"}, h("div", {html: x.text}), x.more ? h("p", {html: x.more}) : null),
      x.pic ? h("div", {class: "llpic", html: x.pic}) : null));
  });
  const body = $("m-body");
  body.innerHTML = "";
  body.append(h("p", {class: "llintro"},
    "Du spelar själv in det du ser på skärmen. Programmet hämtar aldrig något från Livelox."), list);
  const row = $("m-btns");
  row.innerHTML = "";
  row.append(
    h("span", {class: "llsrc"}, "Skärmbilder: Livelox (namnen är påhittade)"),
    h("button", {class: "big second", onclick: () => api("open_help")}, "Öppna hela guiden"),
    h("button", {class: "big", onclick: hideModal}, "Stäng"));
  list.focus();
}
function fitHelp() {
  stepsModal("Hämta pulsfilen från Garmin Connect",
    "Filen innehåller pulsen från ditt lopp. Den läses bara på din dator.", FIT_STEPS,
    "Utan internet: anslut klockan till datorn med USB-kabeln. Filerna ligger i mappen " +
    "GARMIN\\Activity på klockan. Den senaste är ditt senaste pass.");
}

// ---------------------------------------------------------------------------
// Bilder ur filmerna: bara den senaste beställningen räknas, så reglaget känns snabbt
// ---------------------------------------------------------------------------

class FrameLoader {
  constructor(img, src, onError) {
    this.img = img; this.src = src; this.onError = onError;
    this.want = null; this.busy = false; this.objUrl = null; this.box = null;
  }
  request(t, w, h) {
    this.want = {t, w: Math.round(w), h: Math.round(h)};
    if (!this.busy) this._next();
  }
  async _next() {
    const want = this.want;
    if (!want) return;
    this.want = null;
    this.busy = true;
    try {
      const r = await fetch(url("/frame", {src: this.src, t: want.t.toFixed(3), w: want.w, h: want.h}));
      if (r.ok) {
        const blob = await r.blob();
        if (!this.want) {  // en nyare beställning hinner annars visa sig först
          const old = this.objUrl;
          this.objUrl = URL.createObjectURL(blob);
          this.img.src = this.objUrl;
          this.img.hidden = false;
          if (old) URL.revokeObjectURL(old);
        }
      } else if (r.status === 500 && this.onError) {
        this.onError(await r.text());
      }
    } catch (e) { /* fönstret stängs */ }
    this.busy = false;
    if (this.want) this._next();
  }
  showLast() { if (this.objUrl) this.img.src = this.objUrl; }
}

function px(el) {
  const r = el.getBoundingClientRect(), d = window.devicePixelRatio || 1;
  return [Math.max(32, Math.min(1920, r.width * d)), Math.max(18, Math.min(1080, r.height * d))];
}

// ---------------------------------------------------------------------------
// Film med reglage, stegknappar och stor huvudknapp ("Starten är här")
// ---------------------------------------------------------------------------

class Viewer {
  constructor(o) {
    this.o = o; this.t = 0; this.info = null; this.playTimer = null;
    const flip = o.flip ? h("label", {class: "flipbox", title: "Kameran satt upp och ner? Bilden vänds även i videon."},
      this.flipInput = h("input", {type: "checkbox", onchange: (e) => this._flip(e.target.checked)}), "Vänd bilden") : null;
    this.name = h("small");
    this.img = h("img", {alt: "", hidden: true});
    this.msg = h("div", {class: "msg"}, "Ingen film vald");
    this.vid = h("div", {class: "vid", tabindex: 0, onkeydown: (e) => this._key(e),
                         onclick: () => this.vid.focus()}, this.img, this.msg);
    this.range = h("input", {type: "range", min: 0, max: 1, step: "any", value: 0,
                             oninput: () => { paintRange(this.range); this.seek(+this.range.value, true); }});
    this.time = h("span", {class: "time"}, "00:00,0");
    this.markBtn = h("button", {class: "big", onclick: () => this.info && o.onMark(this.t)});
    this.status = h("div", {class: "status"});
    const nudges = [["« 10 s", -10], ["‹ 1 s", -1], ["‹ bild", "-f"], ["bild ›", "+f"], ["1 s ›", 1], ["10 s »", 10]];
    this.el = h("div", {class: "card"},
      h("h2", {}, o.step ? h("span", {class: "num"}, o.step) : null, o.title, this.name, flip),
      this.vid, this.range,
      h("div", {class: "timerow"}, this.time,
        h("button", {class: "ghost", onclick: () => this.play()}, "▶ Spela 3 s")),
      h("div", {class: "nudge"}, nudges.map(([t, d]) => h("button", {class: "ghost", onclick: () => this.step(d)}, t))),
      this.markBtn, this.status);
    this.loader = new FrameLoader(this.img, o.src, (m) => this._error(m));
    new ResizeObserver(() => this._resized()).observe(this.vid);
  }
  setInfo(info, t) {
    this.info = info;
    const n = info?.files?.length || 0;
    this.name.textContent = !info ? "" : (n > 1 ? `${n} filer` : info.names);
    this.name.title = n > 1 ? info.files.join("\n") : "";
    if (!info) { this.msg.textContent = "Ingen film vald"; this.img.hidden = true; return; }
    this.range.max = Math.max(0.1, info.duration);
    this.seek(t || 0);
  }
  setMarked(t) {
    const o = this.o, b = this.markBtn;
    if (t === null || t === undefined) {
      b.className = "big";
      b.innerHTML = `${o.icon}<span>${esc(o.markText)}</span>`;
      this.status.textContent = o.unmarked || "Inte markerad än";
    } else {
      b.className = "big marked";
      b.textContent = "✓ " + o.markedText.replace("{}", fmt(t));
      this.status.textContent = "Ändra genom att trycka igen";
    }
  }
  seek(t, fromRange) {
    if (!this.info) return;
    this._stopPlay();
    const frame = 1 / (this.info.fps || 30);
    this.t = Math.max(0, Math.min(t, this.info.duration - frame));
    if (!fromRange) setRange(this.range, this.t);
    this.time.textContent = fmt(this.t);
    this.msg.textContent = "";
    this.msg.className = "msg";
    this._fetch();
  }
  _fetch() { const [w, hh] = px(this.vid); this.loader.request(this.t, w, hh); }
  _resized() {
    clearTimeout(this._rz);
    this._rz = setTimeout(() => { if (this.info && !this.playTimer) this._fetch(); }, 200);
  }
  step(d) {
    if (!this.info) return;
    if (d === "-f" || d === "+f") d = (d === "-f" ? -1 : 1) / (this.info.fps || 30);
    this.seek(this.t + d);
    this.vid.focus();
  }
  play() {
    if (!this.info) return;
    this._stopPlay();
    const [w, hh] = px(this.vid), start = this.t;
    const secs = Math.min(3, Math.max(0.1, this.info.duration - start));
    this.img.hidden = false;
    this.img.src = url("/play", {src: this.o.src, t: start.toFixed(3), s: secs, w: Math.round(w), h: Math.round(hh)});
    this.playTimer = setTimeout(() => {
      this.playTimer = null;
      this.seek(start + secs);
    }, secs * 1000 + 400);
    this.vid.focus();
  }
  _stopPlay() {
    if (this.playTimer) { clearTimeout(this.playTimer); this.playTimer = null; this.loader.showLast(); }
  }
  _key(e) {
    const map = {ArrowLeft: e.shiftKey ? -1 : "-f", ArrowRight: e.shiftKey ? 1 : "+f"};
    if (e.key in map) { this.step(map[e.key]); e.preventDefault(); }
    else if (e.key === " ") { this.play(); e.preventDefault(); }
  }
  _error(m) { this.img.hidden = true; this.msg.textContent = m; this.msg.className = "msg err"; }
  async _flip(on) {
    await api("set_flip", {on});
    this.seek(this.t);
  }
}

// ---------------------------------------------------------------------------
// Sidorna
// ---------------------------------------------------------------------------

const TRI = '<span class="tri"></span>';
const RING = '<span class="ring2"></span>';
const HEART_SVG = (size, color = "#e8344e") =>
  `<svg width="${size}" height="${size}" viewBox="0 0 24 24"><path fill="${color}" d="M12 21s-7.5-4.6-9.6-9.2C.9 8.4 2.9 4.5 6.6 4.5c2.2 0 3.6 1.2 5.4 3.2 1.8-2 3.2-3.2 5.4-3.2 3.7 0 5.7 3.9 4.2 7.3C19.5 16.4 12 21 12 21z"/></svg>`;

// --- 1. Välj filmer ---
const FilesPage = {
  number: 1, title: "Välj dina filmer", nextStep: 4, tip: "Klart! Tryck på Nästa.",
  lead: "Välj filmen från din headcam och filmen du spelade in från Livelox. Pulsfilen från klockan är frivillig.",
  ready() {
    if (!S.hc) return "Välj din headcam-film för att gå vidare.";
    if (!S.ll) return "Välj din Livelox-inspelning för att gå vidare.";
    return null;
  },
  build(root) {
    this.cards = {};
    const row = h("div", {class: "row"});
    for (const [which, src, step, title, icon, btn, hint, link] of [
      ["headcam", "hc", 1, "Din headcam-film", "🎥", "Välj headcam-film…",
       "Tips: Har kameran delat upp loppet i flera filer (GS01…, GS02…)? Klicka på den första, håll ner Shift och klicka på den sista.", false],
      ["livelox", "ll", 2, "Din Livelox-inspelning", "🗺", "Välj Livelox-inspelning…",
       "Filmen du spelade in från skärmen när du tittade på loppet i Livelox.", true]]) {
      const c = {};
      c.img = h("img", {alt: "", hidden: true});
      c.thumb = h("div", {class: "vid thumb"}, c.img);
      c.empty = h("div", {class: "empty"}, "Ingen film vald än",
        link ? h("a", {class: "link", onclick: liveloxHelp}, "Hur spelar jag in Livelox?") : null);
      c.status = h("div", {class: "okline"});
      c.btn = h("button", {class: "big", onclick: () => api(which === "headcam" ? "pick_headcam" : "pick_livelox")}, btn);
      c.btnText = btn;
      c.hint = h("p", {}, hint);
      c.link = link ? h("a", {class: "link", onclick: liveloxHelp}, "Hur spelar jag in Livelox?") : null;
      c.loader = new FrameLoader(c.img, src);
      c.src = src;
      c.el = h("div", {class: "card pick"},
        h("h2", {}, h("span", {class: "num"}, step), `${icon} ${title}`),
        c.empty, c.thumb, c.status, h("div", {}, c.btn), c.hint, c.link);
      this.cards[which] = c;
      row.append(c.el);
    }
    // 3. Puls från klockan (frivilligt)
    this.p = {};
    const p = this.p;
    p.desc = h("p", {}, "Visa din puls i videon. Välj filen (.fit) från din Garmin-klocka. Hoppa över om du inte vill.");
    p.status = h("div", {class: "okline"});
    p.facts = h("div", {class: "facts"});
    p.remove = h("button", {class: "ghost lg", onclick: () => api("remove_fit")}, "Ta bort");
    p.pick = h("button", {class: "big", onclick: () => api("pick_fit")}, "Välj pulsfil…");
    const pulse = h("div", {class: "card pulsecard"},
      h("span", {class: "num"}, 3),
      h("div", {class: "heartbox", html: HEART_SVG(34)}),
      h("div", {class: "pulsemid"},
        h("h2", {}, "Puls från klockan ", h("span", {class: "tag"}, "Frivilligt")),
        p.desc, p.status, p.facts,
        h("a", {class: "link", onclick: fitHelp}, "Hur hämtar jag .fit-filen från Garmin?")),
      h("div", {class: "btnrow"}, p.remove, p.pick));
    root.append(row, pulse);
    this.shown = {};
  },
  update() {
    const next = !S.hc ? "headcam" : (!S.ll ? "livelox" : null);
    for (const [which, c] of Object.entries(this.cards)) {
      const info = which === "headcam" ? S.hc : S.ll;
      const loading = S.loading[which], err = S.errors[which];
      c.empty.hidden = !!info; c.thumb.hidden = !info; c.status.title = "";
      if (loading) { c.status.className = "okline wait"; c.status.textContent = "Läser filen…"; }
      else if (err) { c.status.className = "okline err"; c.status.textContent = "⚠ " + err; }
      else if (info) {
        c.status.className = "okline";
        const files = info.files || [];
        if (files.length > 1) {
          const stem = (f) => f.replace(/\.[^.]+$/, "");
          c.status.innerHTML = `✓ ${files.length} filer sätts ihop till en film` +
            `<small>${esc(stem(files[0]))} → ${esc(stem(files[files.length - 1]))} · Längd: ${esc(info.length)}</small>`;
          c.status.title = files.join("\n");
        } else {
          c.status.innerHTML = `✓ ${esc(info.names)}<small>Längd: ${esc(info.length)}</small>`;
        }
      } else c.status.textContent = "";
      c.status.hidden = !c.status.textContent;
      // En huvudknapp i taget: den film som ska väljas härnäst är lila.
      if (info) { c.btn.className = "ghost lg"; c.btn.textContent = "Byt film…"; }
      else { c.btn.className = which === next ? "big" : "big second"; c.btn.textContent = c.btnText; }
      if (c.link) c.link.hidden = !info;
      c.hint.hidden = !!info;
      // Förhandsbild en bit in i filmen (början är ofta svart).
      const key = info ? info.names + "|" + S.flip : null;
      if (info && this.shown[which] !== key) {
        this.shown[which] = key;
        const t = which === "headcam" ? Math.min(10, info.duration / 2) : info.duration * 0.3;
        requestAnimationFrame(() => { const [w, hh] = px(c.thumb); c.loader.request(t, w, hh); });
      }
    }
    const p = this.p, hr = S.hr, err = S.errors.fit;
    p.desc.hidden = !!(hr || err || S.loading.fit);
    if (S.loading.fit) { p.status.className = "okline wait"; p.status.textContent = "Läser filen…"; }
    else if (err) { p.status.className = "okline err"; p.status.textContent = "⚠ " + err; }
    else if (hr) { p.status.className = "okline"; p.status.textContent = "✓ " + hr.name; }
    else p.status.textContent = "";
    p.status.hidden = !p.status.textContent;
    p.facts.hidden = !hr;
    if (hr) {
      p.facts.innerHTML = `<span>Start <b>${day(hr.start)} ${clock(hr.start, false)}</b></span>` +
        `<span>Längd <b>${hr.minutes} min</b></span><span>Puls <b>${hr.min}–${hr.max}</b> slag/min</span>`;
      p.pick.className = "ghost lg"; p.pick.textContent = "Byt fil…";
    } else {
      p.pick.className = S.hc && S.ll ? "big" : "big second"; p.pick.textContent = "Välj pulsfil…";
    }
    p.remove.hidden = !hr;
  },
};

// --- 2. Synka starten ---
const SyncPage = {
  number: 2, title: "Hitta starten i båda filmerna", nextStep: 3,
  lead: "Dra reglaget tills du ser starten. Tryck sedan på <b>Starten är här</b> under båda bilderna.",
  tip: "Klart! Tips: Klicka på bilden och använd piltangenterna: ← → en bildruta, Shift + pil en sekund, mellanslag spelar 3 s.",
  ready() {
    if (S.hcSync === null && S.llSync === null) return "Tryck på <b>Starten är här</b> under båda bilderna.";
    if (S.hcSync === null) return "Nästan klart! Tryck på <b>Starten är här</b> under headcam-bilden.";
    if (S.llSync === null) return "Nästan klart! Tryck på <b>Starten är här</b> under Livelox-bilden.";
    return null;
  },
  build(root) {
    this.hc = new Viewer({src: "hc", step: 1, title: "🎥 Headcam", flip: true, icon: TRI, markText: "Starten är här",
      markedText: "Starten markerad vid {}", onMark: (t) => api("mark", {what: "hc", t})});
    this.ll = new Viewer({src: "ll", step: 2, title: "🗺 Livelox", icon: TRI, markText: "Starten är här",
      markedText: "Starten markerad vid {}", onMark: (t) => api("mark", {what: "ll", t})});
    root.append(h("div", {class: "row grow"}, this.hc.el, this.ll.el));
    this.shown = [null, null];
  },
  update() {
    if (this.shown[0] !== S.hc?.names) { this.shown[0] = S.hc?.names; this.hc.setInfo(S.hc, S.hcSync || 0); }
    if (this.shown[1] !== S.ll?.names) { this.shown[1] = S.ll?.names; this.ll.setInfo(S.ll, S.llSync || 0); }
    this.hc.flipInput.checked = S.flip;
    this.hc.setMarked(S.hcSync);
    this.ll.setMarked(S.llSync);
  },
};

// --- 2b. Synka pulsen (bara med pulsfil) ---
const PulsePage = {
  number: 2, kicker: "Steg 2 av 5 · Puls", optional: true, title: "Hitta starten i pulsen", nextStep: 3,
  sub: true, tip: "Klart! Tryck på Nästa.",
  lead: "Klockan vet inte var filmen börjar. Dra den lila linjen till där loppet startar. Där brukar pulsen <b>stiga brant</b>.",
  active: () => !!S.hr,
  ready() { return S.hr && S.fitStart === null ? "Dra linjen och tryck på <b>Starten är här</b>." : null; },
  ML: 46, MR: 18, MT: 38, MB: 30,
  build(root) {
    this.range = h("small");
    this.svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    this.graph = h("div", {class: "graph", tabindex: 0, onkeydown: (e) => {
      const d = {ArrowLeft: -1, ArrowRight: 1}[e.key];
      if (d) { this.move(d * (e.shiftKey ? 10 : 1)); e.preventDefault(); }
    }}, this.svg);
    this.graph.addEventListener("pointerdown", (e) => { this.graph.setPointerCapture(e.pointerId); this.graph.focus(); this._drag(e); });
    this.graph.addEventListener("pointermove", (e) => { if (e.buttons) this._drag(e); });
    this.time = h("span", {class: "time"});
    this.bpm = h("span", {class: "pill"});
    this.markBtn = h("button", {class: "big", onclick: () => api("mark", {what: "fit", t: this.t})});
    this.status = h("div", {class: "status"});
    const nudges = [["« 1 min", -60], ["« 10 s", -10], ["‹ 1 s", -1], ["1 s ›", 1], ["10 s »", 10], ["1 min »", 60]];
    const left = h("div", {class: "card"},
      h("h2", {}, h("span", {class: "num"}, 1), h("span", {html: HEART_SVG(16, "#2a2a22")}), "Din puls från klockan", this.range),
      this.graph,
      h("div", {class: "timerow"}, this.time, this.bpm),
      h("div", {class: "nudge"}, nudges.map(([t, d]) => h("button", {class: "ghost", onclick: () => this.move(d)}, t))),
      h("div", {class: "btnrow"}, h("span", {class: "num", style: "margin-top:14px"}, 2), this.markBtn),
      this.status);
    this.hcImg = h("img", {alt: ""});
    this.hcTxt = h("p");
    const panel = h("div", {class: "panel"},
      h("div", {class: "box"}, h("h3", {}, "🎥 Starten i din headcam"), h("div", {class: "hcimg"}, this.hcImg), this.hcTxt),
      h("div", {class: "box"}, h("h3", {}, "Inte säker?"),
        h("p", {}, "Det gör inget om det skiljer några sekunder. Du kan kolla i provbiten i steg 4.")));
    root.append(h("div", {class: "row grow"}, left, panel));
    this.hcLoader = new FrameLoader(this.hcImg, "hc");
    this.data = null; this.t = 0;
    new ResizeObserver(() => this.draw()).observe(this.graph);
    this._shownHc = null;
  },
  async update() {
    const hr = S.hr;
    if (!hr) return;
    if (!this.data || this.data.id !== hr.id) {
      const r = await fetch(url("/api/pulse", {}));
      this.data = await r.json();
      this.t = S.fitStart ?? this.data.suggestion;
      this.range.textContent = `${day(hr.start)} · ${clock(hr.start, false)}–${clock(hr.end, false)}`;
      this.draw();
    }
    const key = `${S.hcSync}|${S.flip}`;
    if (this._shownHc !== key && S.hc) {
      this._shownHc = key;
      this.hcTxt.innerHTML = `Du markerade starten vid <b>${fmt(S.hcSync || 0)}</b> i filmen. Pulsen ska börja där.`;
      requestAnimationFrame(() => { const [w, hh] = px(this.hcImg.parentNode); this.hcLoader.request(S.hcSync || 0, w, hh); });
    }
    this._labels();
  },
  at(t) {  // pulsen vid tiden t (linjärt), null i glapp
    const d = this.data, f = (t - d.start) / d.step, i = Math.floor(f);
    const a = d.values[i], b = d.values[Math.min(i + 1, d.values.length - 1)];
    if (a === null || a === undefined) return null;
    if (b === null || b === undefined) return a;
    return a + (f - i) * (b - a);
  },
  plot() {
    const r = this.graph.getBoundingClientRect();
    return [this.ML, this.MT, Math.max(10, r.width - this.ML - this.MR), Math.max(10, r.height - this.MT - this.MB), r.width, r.height];
  },
  x(t) { const [x0, , pw] = this.plot(), hr = S.hr; return x0 + (t - hr.start) / Math.max(1, hr.duration) * pw; },
  y(v) { const [, y0, , ph] = this.plot(); return y0 + (this.hi - v) / Math.max(1, this.hi - this.lo) * ph; },
  draw() {
    const hr = S && S.hr, d = this.data, svg = this.svg;
    if (!hr || !d) return;
    const [x0, y0, pw, ph, W, H] = this.plot();
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    let lo = hr.min, hi = hr.max;
    const grid = hi - lo < 60 ? 20 : 40;
    this.lo = lo - 8; this.hi = hi + 8;
    let out = "";
    for (let v = (Math.floor(this.lo / grid) + 1) * grid; v < this.hi; v += grid) {
      const y = this.y(v);
      out += `<line x1="${x0}" x2="${x0 + pw}" y1="${y}" y2="${y}" stroke="#3a3a3a"/>` +
             `<text x="${x0 - 8}" y="${y + 4}" text-anchor="end">${v}</text>`;
    }
    const step = hr.duration <= 1800 ? 300 : (hr.duration <= 5400 ? 600 : 1200);
    for (let t = (Math.floor(hr.start / step) + 1) * step; t < hr.end; t += step)
      out += `<text x="${this.x(t)}" y="${y0 + ph + 20}" text-anchor="middle">${clock(t, false)}</text>`;
    // Kurvan; glapp (klockan pausad) bryter linjen.
    let path = "", pen = false;
    d.values.forEach((v, i) => {
      if (v === null) { pen = false; return; }
      const t = d.start + i * d.step;
      path += `${pen ? "L" : "M"}${this.x(t).toFixed(1)} ${this.y(v).toFixed(1)}`;
      pen = true;
    });
    out += `<path d="${path}" fill="none" stroke="#ff5a6a" stroke-width="2" stroke-linejoin="round"/>`;
    const sx = this.x(d.suggestion), sv = this.at(d.suggestion) ?? this.lo;
    const sy = Math.min(y0 + ph - 10, this.y(sv) + 30);
    const right = sx > x0 + pw - 220;
    out += `<text x="${sx + (right ? -8 : 8)}" y="${sy}" text-anchor="${right ? "end" : "start"}" style="fill:#f2c94c;font-size:13px">↑ Förslag: pulsen stiger här</text>`;
    out += `<g id="cursor"></g>`;
    svg.innerHTML = out;
    this._cursor();
  },
  _cursor() {
    const g = this.svg.querySelector("#cursor");
    if (!g) return;
    const [x0, y0, pw, ph] = this.plot(), x = this.x(this.t), tw = 84;
    const lx = x + 6 + tw <= x0 + pw ? Math.max(x + 6, x0) : x - 6 - tw;
    g.innerHTML = `<line x1="${x}" x2="${x}" y1="${y0 - 26}" y2="${y0 + ph + 4}" stroke="#b0127f" stroke-width="3"/>` +
      `<circle cx="${x}" cy="${y0 + ph + 4}" r="8" fill="#fff" stroke="#b0127f" stroke-width="3"/>` +
      `<rect x="${lx}" y="8" width="${tw}" height="24" rx="4" fill="#b0127f"/>` +
      `<text x="${lx + tw / 2}" y="25" text-anchor="middle" style="fill:#fff;font-weight:700;font-size:13px">${clock(this.t)}</text>`;
  },
  _drag(e) {
    if (!S.hr) return;
    const r = this.graph.getBoundingClientRect(), [x0, , pw] = this.plot();
    const f = Math.min(1, Math.max(0, (e.clientX - r.left - x0) / pw));
    this.t = Math.round(S.hr.start + f * S.hr.duration);
    this._cursor(); this._labels();
  },
  move(dt) {
    if (!S.hr) return;
    this.t = Math.min(S.hr.end, Math.max(S.hr.start, this.t + dt));
    this._cursor(); this._labels();
    this.graph.focus();
  },
  _labels() {
    if (!this.data) return;
    this.time.textContent = clock(this.t);
    const v = this.at(this.t);
    this.bpm.innerHTML = "♥ " + (v ? `${Math.round(v)} slag/min` : "–");
    if (S.fitStart === null) {
      this.markBtn.className = "big"; this.markBtn.innerHTML = TRI + "<span>Starten är här</span>";
      this.status.textContent = "Inte markerad än";
    } else {
      this.markBtn.className = "big marked"; this.markBtn.textContent = `✓ Starten markerad kl. ${clock(S.fitStart)}`;
      this.status.textContent = "Ändra genom att trycka igen";
    }
  },
};

// --- 3. Välj slutet ---
const EndPage = {
  number: 3, title: "Var ska videon sluta?", optional: true, nextStep: 2,
  lead: "Dra reglaget till målgången och tryck på <b>Målgång är här</b>. Vill du ha med allt kan du hoppa över steget.",
  tip: "Steget är frivilligt. Du kan trycka på Nästa direkt.",
  ready: () => null,
  build(root) {
    this.v = new Viewer({src: "hc", step: 1, title: "🎥 Hitta målgången", icon: RING, markText: "Målgång är här",
      markedText: "Målgång markerad vid {}", unmarked: "Inte markerad än. Videon tar med hela filmen.",
      onMark: (t) => api("mark", {what: "end", t})});
    this.facts = h("p");
    this.skipT = h("h3"); this.skipP = h("p");
    this.all = h("button", {class: "ghost", style: "margin-top:10px", onclick: () => api("mark", {what: "end", t: null})}, "Ta med hela filmen");
    root.append(h("div", {class: "row grow"}, this.v.el, h("div", {class: "panel"},
      h("div", {class: "box"}, h("h3", {}, "Så blir videon"), this.facts),
      h("div", {class: "box"}, this.skipT, this.skipP, this.all))));
    this.shown = null;
  },
  update() {
    const key = `${S.hc?.names}|${S.flip}`;
    if (this.shown !== key) {
      this.shown = key;
      this.v.setInfo(S.hc, S.endTime ?? Math.max(0, S.hc.duration - 30));
    }
    this.v.setMarked(S.endTime);
    this.facts.innerHTML = `Börjar <b>${S.leadIn} s före starten</b><br>` +
      `Slutar <b>${S.endTime === null ? "vid filmens slut" : "vid " + fmt(S.endTime)}</b><br>` +
      `Längd ungefär <b>${fmtLong(S.videoLength)}</b>`;
    const none = S.endTime === null;
    this.skipT.textContent = none ? "Vill du inte välja slut?" : "Vill du ha med allt?";
    this.skipP.textContent = none ? "Tryck bara på Nästa. Då kommer hela filmen med." : "Då tar videon med hela filmen till slutet.";
    this.all.hidden = none;
  },
};

// Förloppet när en video skapas (provvideo i steg 4, riktig video i steg 5).
function renderPanel(kind) {
  const bar = h("i"), prog = h("div", {class: "progress"}, bar);
  const text = h("span"), cancel = h("button", {class: "ghost", onclick: () => api("cancel_render")}, "Avbryt");
  const row = h("div", {class: "rstatus"}, text, cancel);
  const open = h("button", {class: "ghost lg", style: "margin-top:8px", onclick: () => api("open_output")}, "▶ Öppna provvideon");
  const el = h("div", {}, prog, row, open);
  el.hidden = true;
  return {el, update() {
    const r = S.render, mine = r.kind === kind;
    el.hidden = !mine;
    if (!mine) return;
    prog.hidden = !S.running && !r.ok;
    bar.style.width = `${Math.round(r.progress * 100)}%`;
    text.textContent = S.running && r.text !== "Avbryter…" && r.progress > 0
      ? `${r.text.replace(/….*/, "…")} ${Math.round(r.progress * 100)} %` : r.text;
    row.className = "rstatus" + (r.ok === true ? " ok" : (r.ok === false ? " bad" : ""));
    cancel.hidden = !S.running;
    // Provvideo: bara "Klart!" och en knapp (ett långt filnamn bryts annars mitt i ordet).
    if (kind === "preview" && r.ok === true) text.textContent = "✓ Klart! Provvideon öppnas.";
    open.hidden = !(kind === "preview" && r.ok === true && !S.running);
  }};
}

// --- 4. Titta på provbit ---
const PreviewPage = {
  number: 4, title: "Följer kartan med?", nextStep: 3,
  lead: "Dra reglaget och kolla några ställen. Pricken på kartan ska vara där du är i filmen.",
  tip: "Ser det bra ut? Tryck på Nästa. Är kartan helt fel, gå tillbaka till steg 2.",
  ready: () => null,
  build(root) {
    this.hcImg = h("img", {alt: ""}); this.llImg = h("img", {alt: ""});
    this.credit = h("div", {class: "credit"}, "Karta: Livelox");
    this.llMsg = h("div", {class: "llmsg"});
    this.band = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    this.frame = h("div", {class: "frame"}, this.hcImg, this.llImg, this.band, this.llMsg, this.credit);
    this.stage = h("div", {class: "stage"}, this.frame);
    this.range = h("input", {type: "range", min: 0, max: 1, step: "any", oninput: () => { paintRange(this.range); this._schedule(); }});
    this.time = h("span", {class: "time"});
    this.sync = h("span", {class: "small", style: "margin:0"});
    const nudge = (label, dt) => h("button", {class: "ghost", onclick: () => api("nudge", {dt}).then(() => this.refresh())}, label);
    const left = h("div", {class: "card"},
      h("h2", {}, h("span", {class: "num"}, 1), "Dra reglaget och kolla några ställen"),
      this.stage, this.range,
      h("div", {class: "timerow"}, this.time,
        h("div", {class: "adj"}, "Kartan ligger efter", nudge("0,2 s", 0.2), nudge("1 s", 1),
          h("span", {style: "margin-left:8px"}, "före"), nudge("0,2 s", -0.2), nudge("1 s", -1))),
      h("div", {class: "timerow"}, h("span"), this.sync));
    this.where = "start";
    this.seg = h("div", {class: "seg"}, [["start", "Starten"], ["end", "Slutet"], ["here", "Här"]].map(([k, t]) =>
      h("button", {"data-k": k, onclick: () => { this.where = k; this._seg(); }}, t)));
    this.prevBtn = h("button", {class: "big", onclick: () => api("preview", {kind: this.where, t: +this.range.value})},
      h("span", {}, "▶ Gör provvideo"));
    this.panel = renderPanel("preview");
    this.lw = h("input", {type: "range", step: 20, oninput: () => { paintRange(this.lw); this._width(); }});
    this.lwTxt = h("div", {class: "small"});
    const panel = h("div", {class: "panel"},
      h("div", {class: "box"}, h("h3", {}, h("span", {class: "num"}, 2), "Titta på en provbit"),
        h("p", {}, `${S.previewSeconds} sekunder med ljud och rörelse.`), this.seg, this.prevBtn,
        h("div", {class: "small"}, "Öppnas i din vanliga videospelare."), this.panel.el),
      h("div", {class: "box"}, h("h3", {class: "muted"}, "Kartans bredd"), this.lw,
        h("div", {class: "ends"}, h("span", {}, "Större headcam"), h("span", {}, "Större karta")),
        this.lwTxt, h("a", {class: "textlink", onclick: () => { setRange(this.lw, 640); this._width(); }}, "Återställ")));
    root.append(h("div", {class: "row grow"}, left, panel));
    this.hcL = new FrameLoader(this.hcImg, "hc", (m) => this._err(m));
    this.llL = new FrameLoader(this.llImg, "ll");
    this.lwMoving = 0;
    new ResizeObserver(() => { this.layout(); this._schedule(200); }).observe(this.stage);
    this._seg();
  },
  _seg() { for (const b of this.seg.children) b.className = b.dataset.k === this.where ? "on" : ""; },
  _err(m) { if (!this._errShown) { this._errShown = true; showModal("Något saknas", `<p>${esc(m)}</p>`, [{text: "OK", primary: true}]); } },
  _width() {
    this.lwMoving = Date.now();
    this.localWidth = +this.lw.value;
    this.layout(); this._schedule(150);
    clearTimeout(this._wt);
    this._wt = setTimeout(() => api("set_width", {width: this.localWidth}), 150);
  },
  width() { return Date.now() - this.lwMoving < 1500 ? this.localWidth : S.liveloxWidth; },
  span() { const start = Math.max(0, S.hcSync - S.leadIn); return [start, S.endTime ?? S.hc.duration]; },
  update() {
    this.lw.min = S.lwMin; this.lw.max = S.lwMax;
    if (Date.now() - this.lwMoving > 1500) setRange(this.lw, S.liveloxWidth);
    const [start, end] = this.span();
    this.range.min = start; this.range.max = Math.max(start + 0.1, end - 0.5);
    const key = `${S.hcSync}|${S.endTime}`;
    if (this._key !== key) {
      this._key = key;
      setRange(this.range, Math.min(end - 0.5, S.hcSync + 30));
      this.refresh();
    }
    const busy = S.running;
    this.prevBtn.disabled = busy;
    this.panel.update();
    this.layout();
    const pk = `${S.flip}|${S.pulse}|${S.llSync}`;
    if (this._pk !== pk) { this._pk = pk; this.refresh(); }
  },
  layout() {
    if (!S || !S.hc) return;
    const r = this.stage.getBoundingClientRect();
    let W = r.width, H = r.height;
    if (W * 9 > H * 16) W = H * 16 / 9; else H = W * 9 / 16;
    const x0 = (r.width - W) / 2, y0 = 0;
    const lw = W * this.width() / S.outW, hw = W - lw;
    const hb = S.pulse ? H - H * 180 / 1080 : H;   // pulsbandet ligger under headcam, som i videon
    Object.assign(this.frame.style, {left: x0 + "px", top: y0 + "px", width: W + "px", height: H + "px"});
    // Headcam mitt i höjd men ovanför pulsbandet; kartan i sin kolumn till höger.
    let ih = Math.min(hb, hw * 9 / 16);
    Object.assign(this.hcImg.style, {left: 0, top: Math.min((H - ih) / 2, hb - ih) + "px", width: hw + "px", height: ih + "px", objectFit: "contain"});
    Object.assign(this.llImg.style, {left: hw + "px", top: 0, width: lw + "px", height: H + "px", objectFit: "contain"});
    Object.assign(this.llMsg.style, {left: hw + "px", top: 0, width: lw + "px", height: H + "px"});
    Object.assign(this.credit.style, {left: hw + lw / 2 + "px", top: H - 20 + "px"});
    this.geom = {W, H, hw, lw, hb};
    const fw = S.outW - this.width();
    this.lwTxt.textContent = `Karta ${this.width()} px bred. Headcam-bilden blir ${fw} × ${Math.min(1080, Math.floor(fw * 9 / 16))} i Full HD.`;
    this.drawBand();
  },
  async drawBand() {
    const g = this.geom, svg = this.band;
    if (!S.pulse || !g) { svg.innerHTML = ""; return; }
    if (!this.pdata || this.pdata.id !== S.hr.id) this.pdata = await (await fetch(url("/api/pulse", {}))).json();
    const d = this.pdata, k = g.W / 1920, off = S.fitStart - S.hcSync;
    const at = (t) => PulsePage.at.call({data: d}, t);
    const [g0, g1] = this.span(), tNow = +this.range.value;
    const gx = 180 * k, gw = g.hw - 214 * k, gy0 = (1080 - 180 + 30) * k, gh0 = 136 * k;
    svg.setAttribute("width", g.W); svg.setAttribute("height", g.H);
    if (gw < 20 || g1 <= g0) { svg.innerHTML = ""; return; }
    let lo = Infinity, hi = -Infinity;
    for (let t = g0; t <= g1; t += Math.max(1, (g1 - g0) / 400)) { const v = at(t + off); if (v !== null) { lo = Math.min(lo, v); hi = Math.max(hi, v); } }
    if (!isFinite(lo)) { svg.innerHTML = ""; return; }
    lo -= 4; hi += 4;
    if (hi - lo < 40) { const m = (lo + hi) / 2; lo = m - 20; hi = m + 20; }
    const pad = 8 * k, gy = gy0 + pad, gh = gh0 - 2 * pad, n = Math.max(2, Math.floor(gw / 3));
    let red = [], grey = [];
    for (let i = 0; i <= n; i++) {
      const t = g0 + (g1 - g0) * i / n, v = at(t + off);
      if (v === null) continue;
      (t <= tNow ? red : grey).push([gx + gw * i / n, gy + (hi - v) / (hi - lo) * gh]);
    }
    const line = (pts) => pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join("");
    let out = "";
    if (grey.length > 1) out += `<path d="${line(grey)}" fill="none" stroke="#6e6e6e" stroke-width="2"/>`;
    if (red.length > 1) {
      const b = gy + gh + pad;
      out += `<path d="M${red[0][0]} ${b}${line(red).replace("M", "L")}L${red[red.length - 1][0]} ${b}Z" fill="#340c12"/>`;
      out += `<path d="${line(red)}" fill="none" stroke="#ff5a6a" stroke-width="2"/>`;
    }
    const v = at(tNow + off);
    if (v !== null && tNow >= g0 && tNow <= g1) {
      const hx = gx + gw * (tNow - g0) / (g1 - g0), hy = gy + (hi - v) / (hi - lo) * gh, s = Math.max(10, 40 * k);
      out += `<g transform="translate(${hx - s / 2} ${hy - s / 2}) scale(${s / 24})"><path fill="#e8344e" stroke="#fff" stroke-width="1.5" d="M12 21s-7.5-4.6-9.6-9.2C.9 8.4 2.9 4.5 6.6 4.5c2.2 0 3.6 1.2 5.4 3.2 1.8-2 3.2-3.2 5.4-3.2 3.7 0 5.7 3.9 4.2 7.3C19.5 16.4 12 21 12 21z"/></g>`;
    }
    out += `<text x="${30 * k}" y="${gy0 + 52 * k}" style="fill:#ff5a6a;font-weight:700;font-size:${Math.max(10, 60 * k)}px">${v === null ? "–" : Math.round(v)}</text>`;
    out += `<text x="${32 * k}" y="${gy0 + 94 * k}" style="fill:#fff;font-size:${Math.max(8, 22 * k)}px">slag/min</text>`;
    out += `<text x="${32 * k}" y="${gy0 + 122 * k}" style="fill:#8a8a8a;font-size:${Math.max(7, 18 * k)}px">max ${Math.round(hi - 4)}</text>`;
    svg.innerHTML = out;
  },
  _schedule(ms = 60) { clearTimeout(this._job); this._job = setTimeout(() => this.refresh(), ms); },
  refresh() {
    if (!S.hc || !S.ll || S.hcSync === null || S.llSync === null || !this.geom) return;
    const t = +this.range.value, [start] = this.span(), g = this.geom, d = window.devicePixelRatio || 1;
    this.time.textContent = `${fmt(t - start)} i videon`;
    this.sync.textContent = `Starten i Livelox: ${fmt(S.llSync)}`;
    this.drawBand();
    this.hcL.request(t, g.hw * d, g.hb * d);
    const lt = t - S.hcSync + S.llSync;
    if (lt < 0) {
      this.llImg.hidden = true;
      this.llMsg.textContent = "(Livelox-inspelningen har inte börjat)";
      return;
    }
    this.llMsg.textContent = "";
    this.llL.request(lt, g.lw * d, g.H * d);
  },
};

// --- 5. Skapa videon ---
const CreatePage = {
  number: 5, title: "Skapa videon", nextStep: 0, tip: "Allt är klart. Tryck på <b>Skapa videon</b>.",
  lead: "Välj kvalitet och tryck på den stora knappen. Ett långt lopp tar en stund.",
  ready: () => null,
  build(root) {
    this.sum = h("div", {class: "card summary"});
    this.choices = [1, 2].map((q) => {
      const sub = h("span");
      const el = h("button", {class: "choice", onclick: () => api("set_quality", {q})},
        h("div", {}, h("b", {}, q === 1 ? "Full HD" : "4K"), sub), h("span", {class: "radio"}));
      return {q, el, sub};
    });
    this.path = h("input", {type: "text", spellcheck: "false", onchange: () => api("set_output", {path: this.path.value})});
    this.makeBtn = h("button", {class: "big", onclick: () => api("make", {path: this.path.value})}, "🎬 Skapa videon");
    this.panel = renderPanel("make");
    this.done = h("div", {class: "donebtns"},
      h("button", {class: "big", onclick: () => api("open_output")}, "▶ Titta på videon"),
      h("button", {class: "big second", onclick: () => api("open_output", {folder: true})}, "Öppna mappen"));
    root.append(this.sum,
      h("div", {class: "label"}, h("span", {class: "num"}, 1), "Välj kvalitet"),
      h("div", {class: "choices"}, this.choices.map((c) => c.el)),
      h("div", {class: "label"}, h("span", {class: "num"}, 2), "Kontrollera var videon sparas"),
      h("div", {class: "saverow"}, this.path, h("button", {class: "ghost lg", onclick: () => api("pick_output")}, "Ändra…")),
      h("div", {class: "makerow"}, h("span", {class: "num"}, 3), this.makeBtn),
      this.panel.el, this.done);
  },
  show() { api("bench"); },
  update() {
    const vals = [["Start i headcam", fmt(S.hcSync)], ["Start i Livelox", fmt(S.llSync)],
      ["Slut", S.endTime === null ? "Hela filmen" : fmt(S.endTime)], ["Längd", "ca " + fmtLong(S.videoLength)]];
    if (S.flip) vals.push(["Headcam", "Vänd 180°"]);
    if (S.pulse) vals.push(["Puls", "♥ Med"]);
    this.sum.innerHTML = vals.map(([l, v]) => `<div>${l}<b>${esc(v)}</b></div>`).join("");
    for (const c of this.choices) {
      c.el.className = "choice" + (S.quality === c.q ? " on" : "");
      c.el.disabled = S.running;
      const est = S.estimates[c.q];
      const tail = est !== null ? ` · tar ${fmtEstimate(est)}` : (S.benchRunning ? " · beräknar tid…" : "");
      c.sub.textContent = (c.q === 1 ? "Bra för YouTube · 1920 × 1080" : "Skarpast, större fil · 3840 × 2160") + tail;
    }
    if (document.activeElement !== this.path) this.path.value = S.output;
    this.makeBtn.disabled = S.running;
    const finished = S.render.kind === "make" && S.render.ok === true && !S.running;
    // När videon är klar är "Titta på videon" huvudknappen och Skapa videon tonas ner.
    this.makeBtn.className = finished ? "big second" : "big";
    this.done.hidden = !finished;
    this.panel.update();
  },
  hint() { return S.render.kind === "make" && S.render.ok === true ? "Videon är klar! Titta på den, eller ladda upp den på YouTube." : null; },
};

const PAGES = [FilesPage, SyncPage, PulsePage, EndPage, PreviewPage, CreatePage];
const NAMES = ["Välj filmer", "Synka starten", "♥ Synka pulsen", "Välj målgång", "Titta på provbit", "Skapa videon"];
const isActive = (i) => !PAGES[i].active || PAGES[i].active();

// ---------------------------------------------------------------------------
// Navigering
// ---------------------------------------------------------------------------

function stepFrom(i, dir) {
  while (i >= 0 && i < PAGES.length && !isActive(i)) i += dir;
  return i;
}

function show(i) {
  if (S.running) return;
  const dir = i < current ? -1 : 1;
  i = stepFrom(Math.max(0, Math.min(i, PAGES.length - 1)), dir);
  if (i < 0 || i >= PAGES.length) return;
  current = i;
  page = PAGES[i];
  const root = $("page");
  root.innerHTML = "";
  $("kicker").textContent = page.kicker || `Steg ${page.number} av 5`;
  $("optional").hidden = !page.optional;
  $("title").textContent = page.title;
  $("lead").innerHTML = page.lead;
  page.build(root);
  if (page.show) page.show();
  refresh();
}

function canJump(i) {
  if (!isActive(i) || S.running) return false;
  for (let j = 0; j < i; j++) if (isActive(j) && PAGES[j].ready()) return false;
  return true;
}

function refresh() {
  if (!page) return;
  $("warn").hidden = !S.toolsError;
  $("warn").textContent = S.toolsError ? "⚠ " + S.toolsError : "";
  page.update();
  const reason = page.ready();
  const last = current === PAGES.length - 1;
  $("back").disabled = current === 0 || S.running;
  $("next").hidden = last; $("nextnum").hidden = last || !page.nextStep;
  $("nextnum").textContent = page.nextStep;
  $("next").disabled = !!reason || S.running;
  $("hint").innerHTML = reason || (page.hint && page.hint()) || page.tip;
  // Sidomenyn: stegen ser ut som kontrollringar.
  const ul = $("steps");
  ul.innerHTML = "";
  PAGES.forEach((p, j) => {
    if (!isActive(j)) return;
    if (p.sub && current < 1) return;  // "Synka pulsen" syns först från steg 2
    const state = j === current ? "active" : (j < current ? "done" : "todo");
    const ring = p.sub ? null : h("span", {class: "ctrl"}, state === "done" ? "✓" : p.number);
    const name = p.sub && state === "done" ? "✓ Synka pulsen" : NAMES[j];
    ul.append(h("li", {class: `${state}${p.sub ? " sub" : ""}${canJump(j) ? "" : " locked"}`,
      onclick: () => canJump(j) && show(j)}, ring, name));
  });
}

$("back").onclick = () => show(stepFrom(current - 1, -1));
$("next").onclick = () => { if (!page.ready()) show(stepFrom(current + 1, 1)); };
$("help").onclick = (e) => { e.preventDefault(); api("open_help"); };
$("terms-link").onclick = (e) => { e.preventDefault(); api("open_help", {page: "villkor"}); };
$("modal").addEventListener("click", (e) => { if (e.target.id === "modal") hideModal(); });
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("modal").hidden) hideModal();
  if (!$("modal").hidden && $("modal").querySelector(".llguide")) {
    // Piltangenterna rullar i Livelox-instruktionen, även när en knapp har fokus.
    const list = $("modal").querySelector(".llscroll"), d = {ArrowDown: 60, ArrowUp: -60}[e.key];
    if (d && document.activeElement !== list) { list.scrollBy({top: d}); e.preventDefault(); }
  }
  if (e.key === "Enter" && !termsShown && $("modal").hidden && !["INPUT", "BUTTON", "A"].includes(document.activeElement.tagName)) {
    if (current < PAGES.length - 1) $("next").click();
  }
});
// Varna om fönstret stängs medan en video skapas.
window.addEventListener("beforeunload", (e) => { if (S && S.running) { e.preventDefault(); e.returnValue = ""; } });
// Fönstret stängs: säg till programmet direkt (annars väntar det på att sidan slutar höras av).
window.addEventListener("pagehide", () => navigator.sendBeacon(url("/api/bye", {}), ""));
document.addEventListener("contextmenu", (e) => { if (e.target.tagName !== "INPUT") e.preventDefault(); });

poll().then(schedulePoll);
