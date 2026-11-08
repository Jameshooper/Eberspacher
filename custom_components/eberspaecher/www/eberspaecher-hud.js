/* Eberspächer EasyStart Web - control panel.
 *
 * Runs inside Home Assistant's own page, so it uses the normal Home Assistant login and
 * can only touch the entities listed in the panel config (see panel.py). */

const TEXT = {
  en: {
    connecting: "CONNECTING…", parked: "PARKED", offline: "NOT AVAILABLE",
    start: "START HEAT", heating: "HEATING", stop: "STOP", off: "HEATER OFF",
    left: "MIN LEFT", runfor: "RUN FOR", min: "min", car: "CAR", live: "LIVE", justnow: "JUST NOW",
    ago: "AGO", h: "H", m: "MIN", ask: "ASK CAR FOR TEMPERATURE", asked: "ASKED · WAIT A MOMENT",
    schedule: "Schedule on", starts: "STARTS", ready: "READY BY", readyonce: "BE READY BY (ONE-OFF)",
    cancel: "CANCEL", lastheated: "LAST HEATED", paused: "PAUSED UNTIL", ev: "EV", fuel: "FUEL",
    evrange: "EV RANGE", fuelrange: "PETROL RANGE", odo: "ODO", v12: "12 V", none: "NONE",
    failed: "Could not do that: ", never: "NEVER", lastev: "EV · LAST",
  },
  de: {
    connecting: "VERBINDE…", parked: "GEPARKT", offline: "NICHT VERFÜGBAR",
    start: "HEIZEN", heating: "HEIZT", stop: "STOPP", off: "HEIZUNG AUS",
    left: "MIN ÜBRIG", runfor: "LAUFZEIT", min: "Min", car: "AUTO", live: "LIVE", justnow: "GERADE EBEN",
    ago: "HER", h: "STD", m: "MIN", ask: "TEMPERATUR VOM AUTO ABFRAGEN", asked: "ANGEFRAGT · KURZ WARTEN",
    schedule: "Zeitplan an", starts: "STARTET", ready: "BEREIT UM", readyonce: "EINMALIG BEREIT UM",
    cancel: "ABBRECHEN", lastheated: "ZULETZT GEHEIZT", paused: "PAUSIERT BIS", ev: "EV", fuel: "TANK",
    evrange: "EV-REICHWEITE", fuelrange: "BENZIN-REICHWEITE", odo: "KM-STAND", v12: "12 V", none: "KEINE",
    failed: "Das hat nicht geklappt: ", never: "NIE", lastev: "EV · ZULETZT",
  },
  sv: {
    connecting: "ANSLUTER…", parked: "PARKERAD", offline: "INTE TILLGÄNGLIG",
    start: "STARTA VÄRME", heating: "VÄRMER", stop: "STOPP", off: "VÄRMAREN AV",
    left: "MIN KVAR", runfor: "KÖRTID", min: "min", car: "BIL", live: "LIVE", justnow: "NYSS",
    ago: "SEDAN", h: "T", m: "MIN", ask: "FRÅGA BILEN OM TEMPERATUR", asked: "FRÅGAD · VÄNTA LITE",
    schedule: "Schema på", starts: "STARTAR", ready: "KLAR KL", readyonce: "KLAR KL (ENGÅNG)",
    cancel: "AVBRYT", lastheated: "SENAST VÄRMD", paused: "PAUSAD TILL", ev: "EL", fuel: "BRÄNSLE",
    evrange: "ELRÄCKVIDD", fuelrange: "BENSINRÄCKVIDD", odo: "MÄTARE", v12: "12 V", none: "INGA",
    failed: "Det gick inte: ", never: "ALDRIG", lastev: "EL · SENAST",
  },
  fi: {
    connecting: "YHDISTETÄÄN…", parked: "PYSÄKÖITY", offline: "EI SAATAVILLA",
    start: "KÄYNNISTÄ LÄMMITYS", heating: "LÄMMITTÄÄ", stop: "PYSÄYTÄ", off: "LÄMMITIN POIS",
    left: "MIN JÄLJELLÄ", runfor: "KESTO", min: "min", car: "AUTO", live: "REAALIAIKA", justnow: "JUURI NYT",
    ago: "SITTEN", h: "T", m: "MIN", ask: "KYSY AUTON LÄMPÖTILAA", asked: "KYSYTTY · ODOTA HETKI",
    schedule: "Aikataulu päällä", starts: "KÄYNNISTYY", ready: "VALMIS KLO", readyonce: "VALMIS KLO (KERRAN)",
    cancel: "PERUUTA", lastheated: "VIIMEKSI LÄMMITETTY", paused: "TAUOLLA ASTI", ev: "SÄHKÖ", fuel: "POLTTOAINE",
    evrange: "SÄHKÖKANTAMA", fuelrange: "BENSIINIKANTAMA", odo: "MATKAMITTARI", v12: "12 V", none: "EI MITÄÄN",
    failed: "Se ei onnistunut: ", never: "EI KOSKAAN", lastev: "SÄHKÖ · VIIMEKSI",
  },
};

const HL = ["34.3,112.3 100.5,120.0 137.1,154.8 131.2,167.8 76.8,169.0 42.5,157.2 30.7,134.7",
  "472.7,112.3 395.9,124.1 379.4,158.4 401.8,167.8 437.3,167.2 473.9,152.5 481.6,133.0"];
const BP = ["28.4,213.9 89.8,223.4 87.5,237.5 29.5,234.0", "483.4,213.9 421.9,224.5 424.3,238.7 482.2,235.2"];
const PJ = [[56.1, 135.9], [88.6, 140.0], [428.4, 138.9], [459.7, 133.5]];
const FL = [[72.1, 137.7], [444.4, 135.9]];
const svg = (cls, inner) =>
  `<svg class="lights ${cls}" viewBox="0 0 520 342" preserveAspectRatio="none">${inner}</svg>`;
const DAY = svg("", HL.map(p => `<polygon points="${p}" fill="#000" opacity=".38" filter="url(#b3)"/>`).join("")) +
  svg("glow", BP.map(p => `<polygon points="${p}" fill="#7fdcff" opacity=".55" filter="url(#b12)"/><polygon points="${p}" fill="#d8f7ff" opacity=".75" filter="url(#b3)"/>`).join("") +
    `<line x1="33.1" y1="224.5" x2="86.3" y2="231" stroke="#fff" stroke-width="5" stroke-linecap="round" filter="url(#b1)"/>` +
    `<line x1="478.6" y1="224.5" x2="426.6" y2="232.2" stroke="#fff" stroke-width="5" stroke-linecap="round" filter="url(#b1)"/>`);
const NIGHT = svg("glow glow-night",
  ["72.1", "444.4"].map(x => `<ellipse cx="${x}" cy="342" rx="120" ry="110" fill="#6fa8d6" opacity=".35" filter="url(#b25)"/>`).join("") +
  HL.map(p => `<polygon points="${p}" fill="#9fe6ff" opacity=".35" filter="url(#b3)"/>`).join("") +
  PJ.map(([x, y]) => `<circle cx="${x}" cy="${y}" r="34" fill="#6fd3ff" opacity=".5" filter="url(#b25)"/><circle cx="${x}" cy="${y}" r="15" fill="#c8f3ff" opacity=".9" filter="url(#b6)"/><circle cx="${x}" cy="${y}" r="7" fill="#fff" filter="url(#b1)"/>`).join("") +
  FL.map(([x, y]) => `<ellipse cx="${x}" cy="${y}" rx="46" ry="1.3" fill="#fff" filter="url(#b1)"/><ellipse cx="${x}" cy="${y}" rx="60" ry="4" fill="#7fdcff" opacity=".6" filter="url(#b3)"/>`).join("") +
  BP.map(p => `<polygon points="${p}" fill="#d8f7ff" opacity=".4" filter="url(#b3)"/>`).join(""));

const STYLE = `
  :host { display: block; min-height: 100%; color: #eaf3ff; --cyan: #39d5ff; --amber: #ffb347; --red: #ff4660; --muted: #7fa8d8; --line: rgba(79,195,255,.28);
    background: radial-gradient(ellipse at 50% -10%, #23407a 0%, #0f1d3d 40%, #060b1a 100%) fixed;
    font-family: -apple-system, "SF Pro Display", "Segoe UI", Roboto, sans-serif; }
  * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
  .page { padding: max(16px, env(safe-area-inset-top)) 14px max(24px, env(safe-area-inset-bottom)); }
  .wrap { max-width: 520px; margin: 0 auto; display: flex; flex-direction: column; gap: 14px; }
  .panel { background: linear-gradient(160deg, rgba(31,58,112,.75) 0%, rgba(15,29,63,.9) 55%, rgba(7,13,31,.95) 100%);
    border: 1px solid var(--line); border-radius: 22px; padding: 18px; box-shadow: 0 0 28px rgba(57,140,255,.2), inset 0 1px 0 rgba(255,255,255,.07); }
  h1 { margin: 0; font-weight: 300; font-size: 20px; letter-spacing: 6px; }
  .sub { margin-top: 6px; font-size: 11px; letter-spacing: 3px; color: #8fc8ff; display: flex; align-items: center; gap: 8px; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--cyan); box-shadow: 0 0 8px var(--cyan); }
  .label { font-size: 9px; letter-spacing: 2px; color: var(--muted); }
  .frame { position: relative; margin: 14px -8px 0; border-radius: 14px; overflow: hidden; box-shadow: 0 0 22px rgba(57,213,255,.18); }
  .frame::after { content: ''; position: absolute; inset: 0; border-radius: 14px; border: 1px solid rgba(57,213,255,.35); pointer-events: none; z-index: 5; }
  .frame img { width: 100%; display: block; transition: filter .8s; }
  .frame.night img { filter: brightness(.68) saturate(1.1); }
  .lights { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; z-index: 2; }
  .glow { mix-blend-mode: screen; }
  .frame.night .glow-night { animation: breathe 3s ease-in-out infinite; }
  .heat { position: absolute; left: 0; right: 0; bottom: 0; height: 65%; z-index: 3; pointer-events: none; mix-blend-mode: screen;
    background: radial-gradient(ellipse 75% 100% at 50% 100%, rgba(255,150,50,.6), rgba(255,95,25,.3) 40%, transparent 75%); animation: heat 3.2s ease-in-out infinite; display: none; }
  .frame.heating .heat { display: block; }
  .temp { position: absolute; top: 10px; right: 10px; z-index: 5; display: flex; flex-direction: column; align-items: flex-end; padding: 6px 10px 5px;
    border-radius: 10px; background: rgba(6,12,30,.62); border: 1px solid rgba(57,213,255,.4); box-shadow: 0 0 14px rgba(57,213,255,.25);
    backdrop-filter: blur(4px); -webkit-backdrop-filter: blur(4px); line-height: 1.05; }
  .temp .label { font-size: 9px; letter-spacing: .2em; color: var(--cyan); }
  .temp b { font-size: 24px; font-weight: 600; color: #e8f6ff; text-shadow: 0 0 10px rgba(57,213,255,.6); }
  .temp b small { font-size: 12px; margin-left: 2px; color: var(--muted); }
  .temp .age { font-size: 9px; letter-spacing: .12em; color: var(--muted); margin-top: 2px; }
  .temp.cold { border-color: rgba(120,190,255,.7); } .temp.cold b { color: #bfe3ff; }
  .temp.warm { border-color: rgba(255,140,60,.75); box-shadow: 0 0 16px rgba(255,120,40,.45); }
  .temp.warm .label { color: #ffb347; } .temp.stale b { opacity: .55; }
  .frame.warn-amber { animation: warnA 2.4s ease-in-out infinite; }
  .frame.warn-red { animation: warnR 1.6s ease-in-out infinite; }
  @keyframes breathe { 0%,100% { opacity: .9 } 50% { opacity: 1 } }
  @keyframes heat { 0%,100% { opacity: .55; transform: translateY(8%) } 50% { opacity: 1; transform: translateY(0) } }
  @keyframes warnA { 0%,100% { box-shadow: 0 0 10px rgba(255,179,71,.25) } 50% { box-shadow: 0 0 30px rgba(255,179,71,.75) } }
  @keyframes warnR { 0%,100% { box-shadow: 0 0 10px rgba(255,60,80,.3) } 50% { box-shadow: 0 0 32px rgba(255,60,80,.85) } }
  .rings { display: flex; justify-content: space-around; padding: 16px 0 6px; }
  .ring { width: 108px; height: 108px; border-radius: 50%; display: grid; place-items: center; }
  .ring > div { width: 88px; height: 88px; border-radius: 50%; background: #0a1430; display: grid; place-items: center; text-align: center; }
  .ring b { font-size: 26px; font-weight: 500; } .ring small { font-size: 12px; }
  .row { display: flex; gap: 8px; margin-top: 8px; }
  .cell { flex: 1; padding: 10px 12px; border-radius: 12px; background: rgba(10,20,48,.6); }
  .cell .v { font-size: 22px; margin-top: 2px; } .u { font-size: 12px; color: var(--muted); }
  .mini { flex: 1; display: flex; justify-content: space-between; align-items: baseline; padding: 7px 12px; border-radius: 10px;
    border: 1px solid rgba(79,195,255,.18); background: rgba(10,20,48,.45); font-size: 14px; }
  .btns { display: flex; gap: 10px; }
  button.big { flex: 1; height: 84px; border-radius: 16px; color: #eaf3ff; font: inherit; font-size: 13px; letter-spacing: 3px; display: flex; flex-direction: column;
    align-items: center; justify-content: center; gap: 6px; cursor: pointer; background: linear-gradient(160deg, #1a3266, #0c1836 60%, #070d1f);
    border: 1px solid rgba(79,195,255,.4); box-shadow: 0 0 14px rgba(57,213,255,.15); transition: all .3s; }
  button.big:active { transform: scale(.97); }
  button.big svg { width: 28px; height: 28px; }
  #start svg { fill: var(--cyan); filter: drop-shadow(0 0 5px var(--cyan)); }
  #start.on { border-color: rgba(255,160,70,.9); box-shadow: 0 0 18px rgba(255,140,50,.55), inset 0 0 14px rgba(255,140,50,.18); }
  #start.on svg { fill: #ffa046; filter: drop-shadow(0 0 6px #ff8c32); }
  #stop { border-color: rgba(255,92,138,.45); opacity: .55; } #stop.on { opacity: 1; }
  #stop svg { fill: #ff5c8a; filter: drop-shadow(0 0 5px rgba(255,92,138,.8)); }
  button.small { width: 100%; margin-top: 12px; padding: 11px; border-radius: 12px; color: #eaf3ff; font: inherit; font-size: 11px; letter-spacing: 2px; cursor: pointer;
    background: rgba(10,20,48,.6); border: 1px solid rgba(79,195,255,.35); }
  button.small:active { background: rgba(57,213,255,.25); } button.small[disabled] { opacity: .5; }
  .status { margin-top: 12px; font-size: 12px; letter-spacing: 2px; color: #8fc8ff; text-align: center; min-height: 16px; }
  .setting { margin-top: 16px; }
  .setting .top { display: flex; justify-content: space-between; align-items: baseline; }
  .setting .val { font-size: 18px; }
  .note { margin-top: 6px; font-size: 12px; color: var(--muted); line-height: 1.4; }
  input[type=range] { width: 100%; margin-top: 10px; accent-color: var(--cyan); height: 28px; }
  input[type=datetime-local] { display: block; width: 100%; max-width: 100%; min-width: 0; -webkit-appearance: none; appearance: none; text-align: left; margin-top: 8px;
    padding: 10px 12px; border-radius: 10px; font: inherit; font-size: 15px; color: #eaf3ff; background: rgba(10,22,52,.85); border: 1px solid rgba(57,213,255,.4); color-scheme: dark; }
  .toggle { display: flex; justify-content: space-between; align-items: center; margin-top: 14px; font-size: 14px; }
  .sw { width: 52px; height: 30px; border-radius: 15px; background: rgba(57,213,255,.18); border: 1px solid rgba(57,213,255,.4); position: relative; cursor: pointer; transition: background .3s; flex: none; }
  .sw::after { content: ''; position: absolute; top: 3px; left: 3px; width: 22px; height: 22px; border-radius: 50%; background: #5a7099; transition: all .3s; }
  .sw.on { background: var(--cyan); box-shadow: 0 0 12px rgba(57,213,255,.6); } .sw.on::after { left: 25px; background: #fff; }
  .toast { position: fixed; left: 50%; bottom: 24px; transform: translateX(-50%); background: #3a0f1c; border: 1px solid var(--red); color: #ffd6de;
    padding: 10px 16px; border-radius: 12px; font-size: 13px; display: none; max-width: 90%; z-index: 9; }
  .hidden { display: none !important; }
  @media (prefers-reduced-motion: reduce) { * { animation: none !important; } }
`;

const HTML = `
<div class="page"><div class="wrap">
  <div class="panel">
    <h1>OPTIMA</h1>
    <div class="sub"><span class="dot" id="dot"></span><span id="sub"></span></div>
    <div class="frame" id="frame">
      <img src="/eberspaecher_static/car.webp" alt="">
      <svg width="0" height="0" style="position:absolute"><defs>
        <filter id="b1" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="1"/></filter>
        <filter id="b3" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="3"/></filter>
        <filter id="b6" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="6"/></filter>
        <filter id="b12" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="12"/></filter>
        <filter id="b25" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="25"/></filter>
      </defs></svg>
      <div id="lights"></div><div class="heat"></div>
      <div class="temp" id="temp"><span class="label" id="tempLabel"></span><b><span id="tempv">–</span><small>°C</small></b><span class="age" id="tempage"></span></div>
    </div>
    <div id="car" class="hidden">
      <div class="rings">
        <div class="ring" id="evRing"><div><div><b id="ev">–</b><small>%</small></div><div class="label" id="evLabel" style="color:var(--cyan)"></div></div></div>
        <div class="ring" id="fuelRing"><div><div><b id="fuel">–</b><small>%</small></div><div class="label" id="fuelLabel" style="color:var(--amber)"></div></div></div>
      </div>
      <div class="row">
        <div class="cell" id="evrCell" style="border:1px solid rgba(57,213,255,.35)"><div class="label" id="evrLabel" style="color:var(--cyan)"></div><div class="v"><span id="evr">–</span> <span class="u">km</span></div></div>
        <div class="cell" id="frCell" style="border:1px solid rgba(255,179,71,.35)"><div class="label" id="frLabel" style="color:var(--amber)"></div><div class="v"><span id="fr">–</span> <span class="u">km</span></div></div>
      </div>
      <div class="row">
        <div class="mini" id="odoCell"><span class="label" id="odoLabel"></span><span><span id="odo">–</span> <span class="u">km</span></span></div>
        <div class="mini" id="v12Cell"><span class="label" id="v12Label"></span><span><span id="v12">–</span> <span class="u">V</span></span></div>
      </div>
    </div>
  </div>

  <div class="panel">
    <div class="btns">
      <button class="big" id="start"><svg viewBox="0 0 24 24"><path d="M17.66 11.2c-.23-.3-.51-.56-.77-.82-.67-.6-1.43-1.03-2.07-1.66C13.33 7.26 13 4.85 13.95 3c-.95.23-1.78.75-2.49 1.32-2.59 2.08-3.61 5.75-2.39 8.9.04.1.08.2.08.33 0 .22-.15.42-.35.5-.23.1-.47.04-.66-.12a.6.6 0 0 1-.14-.17c-1.13-1.43-1.31-3.48-.55-5.12C5.78 10 4.87 12.3 5 14.47c.06.5.12 1 .29 1.5.14.6.41 1.2.71 1.73 1.08 1.73 2.95 2.97 4.96 3.22 2.14.27 4.43-.12 6.07-1.6 1.83-1.66 2.47-4.32 1.53-6.6l-.13-.26c-.21-.46-.77-1.26-.77-1.26z"/></svg><span id="startLabel"></span></button>
      <button class="big" id="stop"><svg viewBox="0 0 24 24"><path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm0 18a8 8 0 1 1 0-16 8 8 0 0 1 0 16zM9 9h6v6H9z"/></svg><span id="stopLabel"></span></button>
    </div>
    <div class="status" id="hstatus"></div>
    <div class="setting" id="runSetting"><div class="top"><span class="label" id="runLabel"></span><span class="val"><span id="minsVal">–</span> <span class="u" id="minUnit"></span></span></div>
      <input type="range" id="mins"></div>
    <button class="small" id="ask"></button>
  </div>

  <div class="panel" id="plan">
    <div class="toggle" style="margin-top:0"><span id="schedLabel"></span><div class="sw" id="sched"></div></div>
    <div class="note" id="schedNote"></div>
    <div class="setting"><div class="label" id="onceLabel"></div><input type="datetime-local" id="once"></div>
    <div class="note" id="onceNote"></div>
    <button class="small hidden" id="cancelOnce"></button>
    <div class="note" id="heatedNote" style="margin-top:14px"></div>
  </div>
</div></div>
<div class="toast" id="toast"></div>
`;

class EberspaecherHud extends HTMLElement {
  constructor() {
    super();
    this._root = this.attachShadow({ mode: "open" });
    this._built = false;
    this._busyUntil = 0;
    this._askedUntil = 0;
    this._lastNight = null;
  }

  connectedCallback() {
    this._tick = setInterval(() => this._render(), 30000);  // keeps "x min ago" current
    this._brand();
  }
  disconnectedCallback() { clearInterval(this._tick); this._unbrand(); }

  // Safari reads the home-screen icon and name when "Add to Home Screen" is used, so while
  // this page is open they are the Optima ones instead of Home Assistant's.
  _brand() {
    this._saved = [];
    const set = (selector, make, attr, value) => {
      let el = document.head.querySelector(selector);
      const created = !el;
      if (created) { el = make(); document.head.appendChild(el); }
      this._saved.push({ el, created, attr, old: el.getAttribute(attr) });
      el.setAttribute(attr, value);
    };
    const tag = (name, attrs) => () => { const e = document.createElement(name); Object.entries(attrs).forEach(([k, v]) => e.setAttribute(k, v)); return e; };
    set('link[rel="apple-touch-icon"]', tag("link", { rel: "apple-touch-icon" }), "href", "/eberspaecher_static/apple-touch-icon.png");
    set('meta[name="apple-mobile-web-app-title"]', tag("meta", { name: "apple-mobile-web-app-title" }), "content", "Optima");
    this._title = document.title;
    document.title = "Optima";
  }

  _unbrand() {
    (this._saved || []).forEach(({ el, created, attr, old }) => {
      if (created) el.remove(); else if (old === null) el.removeAttribute(attr); else el.setAttribute(attr, old);
    });
    this._saved = [];
    if (this._title !== undefined) document.title = this._title;
  }

  set panel(p) { this._cfg = (p && p.config) || {}; this._render(); }
  set hass(h) { this._hass = h; this._render(); }
  set narrow(_) { /* the page is the same either way */ }
  set route(_) { }

  get _t() { return TEXT[(this._hass && this._hass.language || "en").slice(0, 2)] || TEXT.en; }
  _id(name) { return (this._cfg.entities || {})[name]; }
  _car(name) { return (this._cfg.car || {})[name]; }
  _s(id) { return id && this._hass ? this._hass.states[id] : undefined; }
  _own(name) { return this._s(this._id(name)); }
  _num(s) { const v = s ? parseFloat(s.state) : NaN; return isNaN(v) ? null : v; }
  $(id) { return this._root.getElementById(id); }

  _build() {
    this._root.innerHTML = `<style>${STYLE}</style>${HTML}`;
    this._built = true;
    const call = (domain, service, data) => this._call(domain, service, data);
    this.$("start").onclick = () => call("switch", "turn_on", { entity_id: this._id("heating") });
    this.$("stop").onclick = () => call("switch", "turn_off", { entity_id: this._id("heating") });
    this.$("mins").oninput = e => { this._busyUntil = Date.now() + 4000; this.$("minsVal").textContent = e.target.value; };
    this.$("mins").onchange = e => call("number", "set_value", { entity_id: this._id("run_time"), value: Number(e.target.value) });
    this.$("ask").onclick = () => {
      this._askedUntil = Date.now() + 20000; this._render();
      call("button", "press", { entity_id: this._id("request_temperature") });
    };
    this.$("sched").onclick = e => {
      const on = !e.currentTarget.classList.contains("on");
      e.currentTarget.classList.toggle("on", on);
      this._busyUntil = Date.now() + 4000;
      call("switch", on ? "turn_on" : "turn_off", { entity_id: this._id("schedule") });
    };
    this.$("once").onchange = e => {
      if (!e.target.value) return;
      this._busyUntil = Date.now() + 4000;
      call("datetime", "set_value", { entity_id: this._id("ready_once"), datetime: e.target.value.replace("T", " ") + ":00" });
    };
    this.$("cancelOnce").onclick = () => call("button", "press", { entity_id: this._id("cancel_ready_once") });
  }

  async _call(domain, service, data) {
    try {
      await this._hass.callService(domain, service, data);
    } catch (err) {
      const t = this.$("toast");
      t.textContent = this._t.failed + ((err && err.message) || err);
      t.style.display = "block";
      clearTimeout(t._h);
      t._h = setTimeout(() => (t.style.display = "none"), 6000);
    }
  }

  _ring(el, v, color) {
    const n = Math.max(0, Math.min(100, v || 0));
    el.style.background = `conic-gradient(${color} ${n * 3.6}deg, rgba(120,160,220,.12) 0)`;
    el.style.boxShadow = `0 0 20px ${color}55`;
  }

  _local(iso) {
    // "YYYY-MM-DDTHH:MM" in Home Assistant's time zone, for the date and time box.
    const parts = new Intl.DateTimeFormat("sv-SE", {
      timeZone: this._hass.config.time_zone, year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", hourCycle: "h23",
    }).format(new Date(iso));
    return parts.replace(" ", "T");
  }

  _ago(since) {
    const t = this._t;
    const mins = Math.max(0, Math.round((Date.now() - new Date(since)) / 60000));
    const text = mins < 60 ? `${mins} ${t.m}` : `${Math.floor(mins / 60)} ${t.h} ${mins % 60} ${t.m}`;
    return { mins, text: `${text} ${t.ago}` };
  }

  _render() {
    if (!this._hass || !this._cfg) return;
    if (!this._built) this._build();
    const t = this._t;
    const sun = this._s("sun.sun");
    const night = !!sun && sun.state === "below_horizon";
    if (night !== this._lastNight) { this.$("lights").innerHTML = night ? NIGHT : DAY; this._lastNight = night; }

    const heatSwitch = this._own("heating");
    const available = !!heatSwitch && heatSwitch.state !== "unavailable";
    const heating = !!heatSwitch && heatSwitch.state === "on";
    const f = this.$("frame");
    f.classList.toggle("night", night);
    f.classList.toggle("heating", heating);

    // Optional car data, shown only for the sensors chosen in the options.
    const ev = this._num(this._s(this._car("ev"))), fuel = this._num(this._s(this._car("fuel")));
    const anyCar = ["ev", "fuel", "ev_range", "fuel_range", "odometer", "battery_12v"].some(n => this._car(n));
    this.$("car").classList.toggle("hidden", !anyCar);
    const show = (id, on) => this.$(id).classList.toggle("hidden", !on);
    show("evRing", !!this._car("ev")); show("fuelRing", !!this._car("fuel"));
    show("evrCell", !!this._car("ev_range")); show("frCell", !!this._car("fuel_range"));
    show("odoCell", !!this._car("odometer")); show("v12Cell", !!this._car("battery_12v"));
    f.classList.toggle("warn-red", ev !== null && ev < 10);
    f.classList.toggle("warn-amber", !(ev !== null && ev < 10) && fuel !== null && fuel < 15);
    this.$("evLabel").textContent = t.ev; this.$("fuelLabel").textContent = t.fuel;
    this.$("evrLabel").textContent = t.evrange; this.$("frLabel").textContent = t.fuelrange;
    this.$("odoLabel").textContent = t.odo; this.$("v12Label").textContent = t.v12;
    const val = (name, digits = 0) => { const v = this._num(this._s(this._car(name))); return v === null ? "–" : v.toFixed(digits); };
    this.$("ev").textContent = ev === null ? "–" : Math.round(ev); this._ring(this.$("evRing"), ev, "#39d5ff");
    this.$("fuel").textContent = fuel === null ? "–" : Math.round(fuel); this._ring(this.$("fuelRing"), fuel, "#ffb347");
    this.$("evr").textContent = val("ev_range"); this.$("fr").textContent = val("fuel_range");
    this.$("odo").textContent = val("odometer"); this.$("v12").textContent = val("battery_12v", 1);

    // Heater.
    const remaining = this._num(this._own("remaining"));
    const dot = this.$("dot");
    dot.style.background = dot.style.boxShadow = "";
    if (heating) { dot.style.background = "#ffa046"; dot.style.boxShadow = "0 0 8px #ff8c32"; }
    this.$("sub").textContent = !available ? t.offline : heating ? t.heating : t.parked;
    this.$("start").classList.toggle("on", heating);
    this.$("stop").classList.toggle("on", heating);
    this.$("startLabel").textContent = heating ? t.heating : t.start;
    this.$("stopLabel").textContent = t.stop;
    let status = heating ? t.heating : t.off;
    if (heating && remaining) status += ` · ${remaining} ${t.left}`;
    const hs = heatSwitch && heatSwitch.attributes.heater_state;
    if (heating && hs && hs !== "ON") status += ` · ${String(hs).replace(/_/g, " ")}`;

    // The heater's own last temperature reading.
    const tempState = this._own("temperature");
    const tv = this._num(tempState);
    this.$("tempLabel").textContent = t.car;
    this.$("tempv").textContent = tv === null ? "–" : tv.toFixed(0);
    let age = null;
    if (tv !== null && tempState.last_changed) age = this._ago(tempState.last_changed);
    const asking = Date.now() < this._askedUntil;
    this.$("tempage").textContent = asking ? t.asked : age ? age.text : "";
    const te = this.$("temp");
    te.classList.toggle("warm", heating);
    te.classList.toggle("cold", !heating && tv !== null && tv < 5);
    te.classList.toggle("stale", !!age && age.mins > 90);
    if (tv !== null) status += ` · ${t.car} ${tv.toFixed(0)} °C`;
    this.$("hstatus").textContent = status;

    this.$("ask").textContent = t.ask;
    this.$("ask").disabled = !this._id("request_temperature") || asking;
    this.$("runLabel").textContent = t.runfor; this.$("minUnit").textContent = t.min;
    const run = this._own("run_time");
    show("runSetting", !!run);

    // Plan: schedule and the one-off ready-by time.
    const sched = this._own("schedule"), once = this._own("ready_once");
    show("plan", !!sched || !!once);
    this.$("schedLabel").textContent = t.schedule;
    show("sched", !!sched);
    const a = (sched && sched.attributes) || {};
    const times = (a.start_times || []).join(" · ") || t.none;
    this.$("schedNote").textContent = sched ? `${t.ready} ${a.ready_by || "–"} · ${t.starts} ${times}` : "";
    this.$("onceLabel").textContent = t.readyonce;
    show("once", !!once);
    const oa = (once && once.attributes) || {};
    const hasOnce = !!once && !["unknown", "unavailable"].includes(once.state);
    this.$("onceNote").textContent = hasOnce
      ? `${t.starts} ${(oa.start_times || []).join(" · ") || t.none}` : (oa.last_result || "");
    show("cancelOnce", hasOnce && !!this._id("cancel_ready_once"));
    this.$("cancelOnce").textContent = t.cancel;
    const heated = this._own("last_heated");
    const pause = this._own("pause_until");
    const bits = [];
    if (heated) bits.push(`${t.lastheated}: ${heated.state && !["unknown", "unavailable"].includes(heated.state) ? this._ago(heated.state).text : t.never}`);
    if (pause && !["unknown", "unavailable"].includes(pause.state)) bits.push(`${t.paused} ${pause.state}`);
    this.$("heatedNote").textContent = bits.join(" · ");

    // Do not move a control while it is being used.
    if (Date.now() > this._busyUntil) {
      if (run) {
        const r = this.$("mins");
        r.min = run.attributes.min; r.max = run.attributes.max; r.step = run.attributes.step || 1;
        r.value = parseFloat(run.state); this.$("minsVal").textContent = Math.round(parseFloat(run.state));
      }
      this.$("sched").classList.toggle("on", !!sched && sched.state === "on");
      this.$("once").value = hasOnce ? this._local(once.state) : "";
    }
  }
}

if (!customElements.get("eberspaecher-hud")) customElements.define("eberspaecher-hud", EberspaecherHud);
