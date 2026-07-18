// ══════════════════════════════════════════════════════════════════════════
// ABYSS TAB — Abyssal Deadspace tier / weather / enemy guide
//
// Pure client-side static reference, modeled on the Exploration tab. NOT a fit
// tool (fits → caldarijones). Interaction model: pick a Tier + Weather to set the
// combat "conditions" (bonus / penalty / damage type to exploit), then pick the
// faction "room" you warped into to see its enemies as tactical cards. Weather
// does NOT select the faction in-game (spawns are semi-random per room), so the
// bestiary is faction-first and the weather panel is advisory.
// ══════════════════════════════════════════════════════════════════════════

// ── Damage types ───────────────────────────────────────────────────────────
const ABY_DMG = {
  em:    {label:"EM",        short:"EM",  color:"#4a9eff", missile:"Mjolnir"},
  therm: {label:"Thermal",   short:"Th",  color:"#e05555", missile:"Inferno"},
  kin:   {label:"Kinetic",   short:"Kin", color:"#9aa7b5", missile:"Scourge"},
  exp:   {label:"Explosive", short:"Exp", color:"#c8a040", missile:"Nova"},
};
// EVE missiles come in four flavours, one per damage type — so the best missile
// against an enemy is simply the one matching its weakness.
function abyMissileTag(type){
  const d = ABY_DMG[type]; if(!d) return "";
  return `<span class="aby-missile" data-tip="${abyEsc(d.missile)} missiles deal ${abyEsc(d.label)} damage">🚀 ${abyEsc(d.missile)}</span>`;
}

// ── EWAR module chips ────────────────────────────────────────────────────────
const ABY_EWAR = {
  web:   {label:"Stasis Web",      tip:"Stasis Webifier — slows you, kills your kite"},
  scram: {label:"Warp Scram",      tip:"Warp Scrambler — stops you warping out / shuts off your MWD"},
  neut:  {label:"Energy Neut",     tip:"Energy Neutralizer — drains your capacitor"},
  damp:  {label:"Sensor Damp",     tip:"Sensor Dampener — cuts your lock range / scan resolution"},
  paint: {label:"Target Paint",    tip:"Target Painter — bloats your signature, so you take more damage"},
  td:    {label:"Tracking Disrupt",tip:"Tracking / Guidance Disruptor — wrecks your weapon application"},
  rr:    {label:"Remote Reps",     tip:"Remote repair — heals the rest of the room"},
};

// ── Priority ribbons ─────────────────────────────────────────────────────────
const ABY_PRIO = {
  high:   {label:"KILL FIRST", cls:"aby-prio-high"},
  tackle: {label:"CLEAR EARLY", cls:"aby-prio-tackle"},
  normal: {label:"", cls:""},
};

// ── Ship-class glyphs (SVG) — reliable card visual, faction-colored ──────────
const ABY_CLASS_GLYPH = {
  "Frigate":       "M12 2 L16 20 L12 16 L8 20 Z",
  "Destroyer":     "M12 2 L15 12 L18 20 L12 17 L6 20 L9 12 Z",
  "Cruiser":       "M12 2 L20 18 L12 14 L4 18 Z",
  "Battlecruiser": "M12 2 L21 20 L12 15 L3 20 Z",
  "Battleship":    "M12 1 L22 21 L12 16 L2 21 Z",
  "Drone":         "M12 4 L20 12 L12 20 L4 12 Z",
  "Logistics":     "M12 3 L14 10 L21 12 L14 14 L12 21 L10 14 L3 12 L10 10 Z",
};

// Ship classes ordered small→large; used for tier gating.
const ABY_CLASS_ORDER = ["Frigate","Destroyer","Drone","Logistics","Cruiser","Battlecruiser","Battleship"];
// Minimum tier at which a hull class typically appears in numbers. Approximate —
// this is a guide, not a spawn table.
const ABY_CLASS_MINTIER = {"Battleship":3, "Battlecruiser":2};

// ── Tiers (T0–T6) ─────────────────────────────────────────────────────────────
const ABY_TIERS = [
  {n:0, name:"Tranquil",    color:"#4caf76", reward:"~2–8M"},
  {n:1, name:"Calm",        color:"#66bb6a", reward:"~5–15M"},
  {n:2, name:"Agitated",    color:"#c8d84a", reward:"~10–25M"},
  {n:3, name:"Fierce",      color:"#f0c040", reward:"~20–40M"},
  {n:4, name:"Raging",      color:"#e0863a", reward:"~25–45M"},
  {n:5, name:"Chaotic",     color:"#e05555", reward:"~60–80M"},
  {n:6, name:"Cataclysmic", color:"#a04fc8", reward:"~100–120M"},
];

// ── Weathers ──────────────────────────────────────────────────────────────────
// bonus is always ±50%; penalty scales -30/-50% (T0–T3) or -50/-70% (T4–T6).
const ABY_WEATHER = [
  {key:"dark",       name:"Dark",       color:"#8a7fd0",
   bonus:"+50% Maximum Velocity", penalty:"− Turret Optimal & Falloff", exploit:null,
   note:"No resistance hole — needs raw DPS. Missiles ignore the range penalty. Sansha rooms are hardest here."},
  {key:"electrical", name:"Electrical", color:"#4fc3f7",
   bonus:"−50% Capacitor Recharge Time", penalty:"− EM Resistance", exploit:"em",
   note:"Widely considered the easiest weather — big cap boon."},
  {key:"exotic",     name:"Exotic",     color:"#66bb6a",
   bonus:"+50% Scan Resolution", penalty:"− Kinetic Resistance", exploit:"kin",
   note:"Faster locks; kinetic damage is opened up."},
  {key:"firestorm",  name:"Firestorm",  color:"#ff7043",
   bonus:"+50% Armor HP", penalty:"− Thermal Resistance", exploit:"therm",
   note:"Generally the hardest — the armor buff also fattens the many armor-tanked rats. Favors armor ships."},
  {key:"gamma",      name:"Gamma",      color:"#c8a040",
   bonus:"+50% Shield HP", penalty:"− Explosive Resistance", exploit:"exp",
   note:"Favors shield ships; explosive damage is opened up."},
];

// ── Factions & enemies ────────────────────────────────────────────────────────
const ABY_FACTIONS = [
  {
    key:"triglavian", name:"Triglavian Collective", glyph:"◈", color:"#e0863a",
    prob:[25,23,21,20,20,20,20],
    spot:"Rust-red, angular hulls named Damavik, Kikimora, Vedmak, Drekavac, Leshak. Their beams glow brighter the longer they keep firing on you.",
    deals:["therm","exp"], weak:"exp",
    strategy:"Deal Explosive (Thermal second). Disintegrators have no falloff — kite past ~22 km to switch them off and reset their ramping damage; orbit close on slow Leshaks. Kill remote-rep support first.",
    note:"EWAR is set by the ship's prefix: Tangling=web · Anchoring=scram · Starving=neut · Renewing=remote-rep · Blinding=damp · Ghosting=tracking-disrupt · Harrowing/Shining=painter.",
    enemies:[
      {name:"Damavik", cls:"Frigate", deals:["therm","exp"], weak:"exp", ewar:["web","scram","neut","td"], priority:"tackle",
       tip:"Fast EWAR frigate. Clear Tangling (web) & Anchoring (scram) variants first so you keep range control."},
      {name:"Kikimora", cls:"Destroyer", deals:["therm","exp"], weak:"exp", ewar:[], priority:"normal",
       tip:"Spawns in numbers at range. Watch the stacked ramping DPS — kite out to reset it."},
      {name:"Vedmak", cls:"Cruiser", deals:["therm","exp"], weak:"exp", ewar:["neut"], priority:"normal",
       tip:"Main sustained DPS. Keep it beyond ~22 km to reset its ramp, or alpha it before it spools up."},
      {name:"Drekavac", cls:"Battlecruiser", deals:["therm","exp"], weak:"exp", ewar:[], priority:"normal",
       tip:"A heavier Vedmak that ramps hard. Spawns pre-damaged — focus it down quickly."},
      {name:"Leshak", cls:"Battleship", deals:["therm","exp"], weak:"exp", ewar:["neut"], priority:"normal",
       tip:"Enormous ramped damage but poor tracking. Orbit close/fast, or kite past ~22 km to reset. Thin HP buffer once you apply."},
      {name:"Rodiva", cls:"Logistics", deals:[], weak:"exp", ewar:["rr"], priority:"high",
       tip:"Remote-repair logi. KILL FIRST — its reps spool up to T2-logi strength and will out-heal your DPS."},
    ],
  },
  {
    key:"roguedrones", name:"Rogue Drones", glyph:"⬡", color:"#4caf76",
    prob:[38,32,28,25,25,26,26],
    spot:"Insectoid drone hulls — 'Tessella' frigates, the '…grip' battlecruisers, and the huge Abyssal Overmind. Often a Deviant Automata Suppressor structure floating nearby.",
    deals:["therm","kin"], weak:"em",
    strategy:"Deal EM (Thermal second). Kill the remote-rep weaver drones first. You can lure pirate drones into a Deviant Automata Suppressor structure to help kill them.",
    note:"The 'Grip' battlecruisers are named for the damage they deal: Sparkgrip=EM · Strikegrip=Kinetic · Embergrip=Thermal · Blastgrip=Explosive.",
    enemies:[
      {name:"Tessella (drone frigate)", cls:"Frigate", deals:["therm","kin"], weak:"em", ewar:[], priority:"normal",
       tip:"Basic drone frigate — low individual threat, just numerous."},
      {name:"Grip-line (Spark/Strike/Ember/Blast)", cls:"Battlecruiser", deals:["em","kin","therm","exp"], weak:"em", ewar:[], priority:"normal",
       tip:"Named by the damage it deals — Sparkgrip=EM, Strikegrip=Kin, Embergrip=Therm, Blastgrip=Exp."},
      {name:"Snarecaster", cls:"Drone", deals:[], weak:"em", ewar:["web"], priority:"tackle",
       tip:"Webbing drone — clear early to keep your speed/kite."},
      {name:"Fogcaster", cls:"Drone", deals:[], weak:"em", ewar:["td"], priority:"tackle",
       tip:"Tracking / missile disruption drone — trashes your application."},
      {name:"Gazedimmer", cls:"Drone", deals:[], weak:"em", ewar:["damp"], priority:"normal",
       tip:"Sensor-dampening drone — cuts your lock range."},
      {name:"Fieldweaver / Plateweaver", cls:"Logistics", deals:[], weak:"em", ewar:["rr"], priority:"high",
       tip:"Shield (Field) / armor (Plate) remote-rep drones. KILL FIRST."},
      {name:"Abyssal Overmind", cls:"Battleship", deals:["therm","kin"], weak:"em", ewar:["web"], priority:"normal",
       tip:"Rogue-drone battleship boss — railguns deal Therm+Kin; webs you at T5–T6."},
    ],
  },
  {
    key:"drifters", name:"Drifters & Seekers", glyph:"✧", color:"#8a7fd0",
    prob:[1,6,9,11,12,13,13],
    spot:"Big black Jove-style ships named '…Tyrannos' (Karybdis, Scylla) and Ephialtes, with small Seeker drones. Heavily omni-tanked. Barely appear below T1.",
    deals:["em","therm","kin","exp"], weak:null,
    strategy:"Omni damage incoming — apply your single strongest damage type. Drifters carry heavy (~50%) omni resists; Seekers are lightly tanked (~10%). The Karybdis boss kites away with poor tracking — chase it and orbit close.",
    note:"Drifter role ships by suffix: Dissipator=neut · Entangler=web · Spearfisher=scram · Illuminator=painter · Obfuscator=damp · Confuser=tracking-disrupt.",
    enemies:[
      {name:"Seeker", cls:"Drone", deals:["em","therm","kin","exp"], weak:null, ewar:[], priority:"normal",
       tip:"Only ~10% omni resist — quick kills with any damage type."},
      {name:"Ephialtes (role variants)", cls:"Cruiser", deals:["em","therm","kin","exp"], weak:null, ewar:["neut","web","scram","paint","damp","td"], priority:"tackle",
       tip:"EWAR by suffix (see note). Clear the scram/web ones early."},
      {name:"Scylla Tyrannos", cls:"Cruiser", deals:["em","therm","kin","exp"], weak:null, ewar:["web","neut"], priority:"normal",
       tip:"Drifter cruiser; web/neut variants — heavy omni resists, grind it."},
      {name:"Karybdis Tyrannos", cls:"Battleship", deals:["em","therm","kin","exp"], weak:null, ewar:[], priority:"normal",
       tip:"BOSS — often the hardest room. Kites away ~300 m/s with poor tracking; run it down and orbit close."},
    ],
  },
  {
    key:"sleepers", name:"Sleepers", glyph:"◉", color:"#29b6f6",
    prob:[14,11,9,8,9,9,9],
    spot:"Every ship is prefixed 'Lucid …' (Aegis, Warden, Firewatcher, Preserver). Sleeper-drone look, omni damage.",
    deals:["em","therm","kin","exp"], weak:null,
    strategy:"Omni damage — use your strongest type. Kill the Preserver logi first (it reps ~3× a normal logi). Clear web/neut support before the DPS ships.",
    note:"'Lucid' prefix ships. Preserver = logistics; Warden = web; Firewatcher = neut.",
    enemies:[
      {name:"Lucid Aegis / Escort", cls:"Frigate", deals:["em","therm","kin","exp"], weak:null, ewar:[], priority:"normal",
       tip:"Sleeper damage frigates — omni, low individual threat."},
      {name:"Lucid Warden", cls:"Cruiser", deals:["em","therm","kin","exp"], weak:null, ewar:["web"], priority:"tackle",
       tip:"Webbing Sleeper — clear early."},
      {name:"Lucid Firewatcher", cls:"Frigate", deals:["em","therm","kin","exp"], weak:null, ewar:["neut"], priority:"high",
       tip:"Neuting frigate — drains your cap; kill it before it caps out your tank."},
      {name:"Preserver", cls:"Logistics", deals:[], weak:null, ewar:["rr"], priority:"high",
       tip:"Reps ~3× a normal logi. KILL FIRST or nothing else dies."},
    ],
  },
  {
    key:"sansha", name:"Sansha's Nation", glyph:"✦", color:"#e05555",
    prob:[6,5,5,5,4,4,4],
    spot:"Every ship is prefixed 'Devoted …' (Hunter, Trapper, Knight). Dark Sansha hulls, slow, long-range lasers. Rare overall.",
    deals:["em","therm"], weak:"em",
    strategy:"Deal EM (Thermal second). Sansha are slow and fight at long range — control the distance. Kill the Devoted Knight first. Hardest room under Dark weather (their range isn't hurt by it).",
    note:"'Devoted' prefix. The Devoted Knight is the exception to the EM weakness — it's weak to EM then Explosive, and strong vs Thermal/Kinetic.",
    enemies:[
      {name:"Devoted Hunter", cls:"Frigate", deals:["em","therm"], weak:"em", ewar:[], priority:"normal",
       tip:"Slow, long-range frigate — easy once you're inside its game."},
      {name:"Devoted Trapper", cls:"Frigate", deals:["em","therm"], weak:"em", ewar:["web","scram"], priority:"tackle",
       tip:"Tackle frigate — clear early so you keep range control."},
      {name:"Devoted Knight", cls:"Cruiser", deals:["em","therm"], weak:"em", weak2:"exp", ewar:["web","neut"], priority:"high",
       tip:"Strong web + neut + shield booster. KILL FIRST — it will cap out and web down a frigate. Weak to EM, then Explosive."},
    ],
  },
  {
    key:"angel", name:"Angel Cartel", glyph:"⟁", color:"#f0c040",
    prob:[8,6,5,5,5,5,5],
    spot:"Real Angel hulls — Dramiel, Cynabal, Ixion — plus 'Lucifer …' support ships. Very fast, red/brown hulls. Rare overall.",
    deals:["exp","em"], weak:"exp",
    strategy:"Deal Explosive (Kinetic second). Angels are FAST and web you — brawl at close range, don't try to kite. Kill the neut (Fury) and logi (Burst) ships first; hit normal Cynabals before Elite ones.",
    note:"'Lucifer' prefix role ships plus real Angel hulls (Dramiel, Cynabal, Ixion).",
    enemies:[
      {name:"Dramiel", cls:"Frigate", deals:["exp","em"], weak:"exp", ewar:["web"], priority:"tackle",
       tip:"Blisteringly fast tackle frigate — clear early."},
      {name:"Lucifer Fury", cls:"Cruiser", deals:["exp","em"], weak:"exp", ewar:["neut"], priority:"high",
       tip:"Neuting ship — drains your cap; kill before your tank/prop dies."},
      {name:"Lucifer Burst", cls:"Logistics", deals:[], weak:"exp", ewar:["rr"], priority:"high",
       tip:"Remote-rep logi. KILL FIRST."},
      {name:"Cynabal", cls:"Cruiser", deals:["exp","em"], weak:"exp", ewar:["web"], priority:"normal",
       tip:"Fast web cruiser — main DPS. Prioritize normal Cynabals over Elite variants."},
      {name:"Ixion", cls:"Cruiser", deals:["exp","em"], weak:"exp", ewar:["paint","td"], priority:"normal",
       tip:"Paints & tracking-disrupts you — annoying but low direct threat."},
    ],
  },
  {
    key:"edencom", name:"CONCORD / EDENCOM", glyph:"✚", color:"#4fc3f7",
    prob:[9,7,6,5,5,5,5],
    spot:"CONCORD hulls (Marshal, Enforcer, Pacifier) firing missiles, or EDENCOM (Thunderchild, Skybreaker, Stormbringer) firing blue arcing Vorton weapons. Rare overall.",
    deals:["therm","exp","em","kin"], weak:null,
    strategy:"CONCORD hulls (Marshal/Enforcer/Pacifier): flat resists, Therm/Exp missiles — keep velocity/transversal high to spoil application. EDENCOM hulls (Thunderchild/Skybreaker/Stormbringer): weak to Thermal & EM, fire chaining EM/Kin Vorton arcs. Both bring heavy neuts.",
    note:"Two sub-factions on one filament type. CONCORD = flat resists (use any type); EDENCOM = weak to Thermal, then EM.",
    enemies:[
      {name:"Pacifier (CONCORD)", cls:"Frigate", deals:["therm","exp"], weak:null, ewar:[], priority:"normal",
       tip:"Missile frigate — keep moving to reduce its application."},
      {name:"Skybreaker (EDENCOM)", cls:"Frigate", deals:["em","kin"], weak:"therm", ewar:[], priority:"normal",
       tip:"Vorton arc frigate — weak to Thermal/EM."},
      {name:"Enforcer / Drainer (CONCORD)", cls:"Cruiser", deals:["therm","exp"], weak:null, ewar:["neut"], priority:"high",
       tip:"Neuting cruiser — kill before it caps you out."},
      {name:"Stormbringer (EDENCOM)", cls:"Cruiser", deals:["em","kin"], weak:"therm", ewar:[], priority:"normal",
       tip:"High-DPS Vorton cruiser — weak to Thermal/EM."},
      {name:"Marshal (CONCORD)", cls:"Battleship", deals:["therm","exp"], weak:null, ewar:["web","paint"], priority:"normal",
       tip:"~427 DPS of missiles — flat resists, so keep velocity high and grind it."},
      {name:"Thunderchild (EDENCOM)", cls:"Battleship", deals:["em","kin"], weak:"therm", ewar:[], priority:"normal",
       tip:"Chaining Vorton arcs with huge alpha (~2000). Weak to Thermal, then EM."},
    ],
  },
];

// ── State ────────────────────────────────────────────────────────────────────
// Server-authoritative: seeded by loadSettings via abyApplyStored(), persisted
// as one 'aby_state' pref (tier/weather/faction) so the selection follows the
// account across devices.
const ABY = {tier:3, weather:"electrical", faction:"triglavian"};
function abyApplyStored(saved){
  try {
    if(saved && typeof saved==="string") saved = JSON.parse(saved);
    if(saved){
      if(Number.isInteger(saved.tier) && saved.tier>=0 && saved.tier<=6) ABY.tier = saved.tier;
      if(ABY_WEATHER.some(w=>w.key===saved.weather)) ABY.weather = saved.weather;
      if(ABY_FACTIONS.some(f=>f.key===saved.faction)) ABY.faction = saved.faction;
    }
  } catch(e){}
}
function abySave(){ if(typeof setPref==="function") setPref('aby_state', {tier:ABY.tier, weather:ABY.weather, faction:ABY.faction}); }

function abyEsc(s){ return s==null?"":String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;"); }

// ── Small render helpers ───────────────────────────────────────────────────────
function abyDmgPip(type, {big=false, short=false}={}){
  const d = ABY_DMG[type]; if(!d) return "";
  return `<span class="aby-pip${big?" aby-pip-lg":""}" style="--pip:${d.color}" data-tip="${abyEsc(d.label)} damage">${abyEsc(short?d.short:d.label)}</span>`;
}
function abyEwarChip(key){
  const e = ABY_EWAR[key]; if(!e) return "";
  return `<span class="aby-ewar" data-tip="${abyEsc(e.tip)}">${abyEsc(e.label)}</span>`;
}
function abyClassGlyphSvg(cls, color){
  const path = ABY_CLASS_GLYPH[cls] || ABY_CLASS_GLYPH["Cruiser"];
  return `<svg viewBox="0 0 24 24" class="aby-hull" aria-hidden="true"><path d="${path}" fill="${color}"/></svg>`;
}

// Spawn likelihood (per TIER, not weather — from logged-run telemetry).
function abyProbLabel(pct){ return pct>=20?"Very common":pct>=10?"Common":pct>=5?"Uncommon":"Rare"; }
function abyProbTip(f){
  const t = ABY.tier;
  return `Best-effort estimate: ~${f.prob[t]}% of logged T${t} rooms are ${f.name}. `
    + `Source: Abyssal.Space telemetry via qsna.eu. Spawns depend on the tier, not the weather.`;
}
function abyProbBar(f){
  const pct = f.prob[ABY.tier];
  const w = Math.min(100, pct*2.6);
  return `<span class="aby-prob-bar"><span class="aby-prob-fill" style="width:${w}%"></span></span>`;
}

// ── Tier + weather selectors ────────────────────────────────────────────────────
function abyRenderTiers(){
  const wrap = $("#aby-tier-sel");
  wrap.innerHTML = ABY_TIERS.map(t=>
    `<button class="aby-tier-btn${t.n===ABY.tier?" active":""}" data-tier="${t.n}" style="--tc:${t.color}"
       data-tip="${abyEsc(t.name)} · reward ${abyEsc(t.reward)} per cruiser run">
       <span class="aby-tier-n">T${t.n}</span><span class="aby-tier-name">${abyEsc(t.name)}</span></button>`
  ).join("");
  wrap.querySelectorAll(".aby-tier-btn").forEach(b=>{
    b.onclick = ()=>{ ABY.tier = +b.dataset.tier; abySave(); abyRenderTiers(); abyRenderConditions(); abyRenderFactionList(); abyRenderCards(); };
  });
}
function abyRenderWeather(){
  const wrap = $("#aby-weather-sel");
  wrap.innerHTML = ABY_WEATHER.map(w=>
    `<button class="aby-weather-btn${w.key===ABY.weather?" active":""}" data-w="${w.key}" style="--wc:${w.color}"
       data-tip="${abyEsc(w.bonus)} · ${abyEsc(w.penalty)}">${abyEsc(w.name)}</button>`
  ).join("");
  wrap.querySelectorAll(".aby-weather-btn").forEach(b=>{
    b.onclick = ()=>{ ABY.weather = b.dataset.w; abySave(); abyRenderWeather(); abyRenderConditions(); };
  });
}

// ── Conditions band ──────────────────────────────────────────────────────────
function abyRenderConditions(){
  const tier = ABY_TIERS.find(t=>t.n===ABY.tier);
  const w = ABY_WEATHER.find(x=>x.key===ABY.weather);
  const penaltyBand = ABY.tier<=3 ? "−30% / −50%" : "−50% / −70%";
  const exploit = w.exploit
    ? `<span class="aby-pip aby-pip-lg" style="--pip:${ABY_DMG[w.exploit].color}">${abyEsc(ABY_DMG[w.exploit].short)}</span> ${abyEsc(ABY_DMG[w.exploit].label)} ${abyMissileTag(w.exploit)}`
    : `<span class="aby-dim">No resistance hole — bring raw DPS</span>`;
  $("#aby-conditions").innerHTML = `
    <div class="aby-cond-row">
      <div class="aby-cond-chip" style="--wc:${w.color}">
        <span class="aby-cond-label">Weather</span>
        <span class="aby-cond-value" style="color:${w.color};font-weight:700">${abyEsc(w.name)}</span>
      </div>
      <div class="aby-cond-chip" style="--wc:${tier.color}">
        <span class="aby-cond-label">Tier</span>
        <span class="aby-cond-value" style="color:${tier.color};font-weight:700">T${tier.n} ${abyEsc(tier.name)}</span>
      </div>
      <div class="aby-cond-chip aby-cond-good">
        <span class="aby-cond-label">Bonus (+50%)</span>
        <span class="aby-cond-value">${abyEsc(w.bonus)}</span>
      </div>
      <div class="aby-cond-chip aby-cond-bad">
        <span class="aby-cond-label">Penalty (${penaltyBand})</span>
        <span class="aby-cond-value">${abyEsc(w.penalty)}</span>
      </div>
      <div class="aby-cond-chip aby-cond-exploit">
        <span class="aby-cond-label">Exploit with</span>
        <span class="aby-cond-value">${exploit}</span>
      </div>
      <div class="aby-cond-chip">
        <span class="aby-cond-label">Reward</span>
        <span class="aby-cond-value" style="color:var(--gold)">${abyEsc(tier.reward)}</span>
      </div>
    </div>
    <div class="aby-cond-note">${abyEsc(w.note)}</div>
    <div class="aby-cond-rules">
      <span data-tip="After 20 minutes your ship AND pod are destroyed on the spot.">⏱ 20-min hard timer</span>
      <span data-tip="1 filament = 1 cruiser · 2 filaments = up to 2 destroyers · 3 filaments = up to 3 frigates.">🚀 1 cruiser / 2 destroyers / 3 frigates</span>
      <span data-tip="Strategic (T3) cruisers cannot enter.">⛔ No T3 cruisers</span>
      <a href="https://caldarijoans.streamlit.app/" target="_blank" rel="noopener" class="aby-fit-link" data-tip="Ship fits live on Caldari Joans — this guide focuses on the enemies.">Need a fit? → Caldari Joans ↗</a>
    </div>`;
}

// ── Faction sidebar + search ────────────────────────────────────────────────────
function abyRenderFactionList(){
  const list = $("#aby-faction-list");
  list.innerHTML = ABY_FACTIONS.map(f=>{
    const pct = f.prob[ABY.tier];
    return `<div class="aby-faction-row${f.key===ABY.faction?" active":""}" data-f="${f.key}" style="--fc:${f.color}">
       <span class="aby-faction-glyph">${abyEsc(f.glyph)}</span>
       <div class="aby-faction-mid">
         <div class="aby-faction-name">${abyEsc(f.name)}</div>
         <div class="aby-faction-prob" data-tip="${abyEsc(abyProbTip(f))}">
           ${abyProbBar(f)}<span>~${pct}% ${abyProbLabel(pct)}</span>
         </div>
       </div>
       <span class="aby-faction-weak">${f.weak?abyDmgPip(f.weak,{short:true}):"omni"}</span>
     </div>`;
  }).join("");
  list.querySelectorAll(".aby-faction-row").forEach(el=>{
    el.onclick = ()=> abySelectFaction(el.dataset.f);
  });
}
function abySelectFaction(key, {highlight}={}){
  ABY.faction = key; abySave();
  $("#aby-search").value = "";
  $("#aby-results").classList.add("hidden");
  abyRenderFactionList();
  abyRenderCards(highlight);
}
function abySearch(){
  const q = ($("#aby-search").value||"").trim().toLowerCase();
  const results = $("#aby-results");
  if(q.length < 2){ results.classList.add("hidden"); return; }
  const hits = [];
  ABY_FACTIONS.forEach(f=> f.enemies.forEach(e=>{
    if(e.name.toLowerCase().includes(q)) hits.push({f, e});
  }));
  if(!hits.length){
    results.innerHTML = `<div class="aby-result-item"><span style="color:var(--dim)">No matching enemies</span></div>`;
    results.classList.remove("hidden"); return;
  }
  results.innerHTML = hits.slice(0,15).map(h=>
    `<div class="aby-result-item" data-f="${h.f.key}" data-e="${abyEsc(h.e.name)}">
       <span class="aby-result-name">${abyEsc(h.e.name)}</span>
       <span class="aby-result-fac" style="color:${h.f.color}">${abyEsc(h.f.name)}</span>
     </div>`
  ).join("");
  results.classList.remove("hidden");
  results.querySelectorAll(".aby-result-item[data-f]").forEach(el=>{
    el.onclick = ()=> abySelectFaction(el.dataset.f, {highlight:el.dataset.e});
  });
}

// ── Enemy cards ──────────────────────────────────────────────────────────────
function abyEnemyCard(e, faction, highlight){
  const gated = ABY_CLASS_MINTIER[e.cls] && ABY.tier < ABY_CLASS_MINTIER[e.cls];
  const prio = ABY_PRIO[e.priority] || ABY_PRIO.normal;
  const deals = e.deals.length ? e.deals.map(t=>abyDmgPip(t)).join("") : `<span class="aby-dim">EWAR / support</span>`;
  const weakPips = e.weak ? abyDmgPip(e.weak,{big:true}) + (e.weak2?" "+abyDmgPip(e.weak2):"") + " " + abyMissileTag(e.weak)
                          : `<span class="aby-dim">omni — use your strongest (match your hull's bonus)</span>`;
  const ewar = e.ewar.length ? e.ewar.map(k=>abyEwarChip(k)).join("") : `<span class="aby-dim">none</span>`;
  return `<div class="aby-card${highlight&&highlight===e.name?" aby-card-hl":""}" style="--fc:${faction.color}">
    ${prio.label?`<div class="aby-ribbon ${prio.cls}">${prio.label}</div>`:""}
    <div class="aby-card-top">
      ${abyClassGlyphSvg(e.cls, faction.color)}
      <div class="aby-card-id">
        <div class="aby-card-name">${abyEsc(e.name)}</div>
        <div class="aby-card-cls">${abyEsc(e.cls)}${gated?` · <span class="aby-gate" data-tip="Typically appears from T${ABY_CLASS_MINTIER[e.cls]} up">T${ABY_CLASS_MINTIER[e.cls]}+</span>`:""}</div>
      </div>
    </div>
    <div class="aby-card-stat"><span class="aby-stat-k">Deals</span><span class="aby-stat-v">${deals}</span></div>
    <div class="aby-card-stat"><span class="aby-stat-k">Weak to</span><span class="aby-stat-v">${weakPips}</span></div>
    <div class="aby-card-stat"><span class="aby-stat-k">EWAR</span><span class="aby-stat-v aby-ewar-row">${ewar}</span></div>
    <div class="aby-card-tip">${abyEsc(e.tip)}</div>
  </div>`;
}
function abyRenderCards(highlight){
  const f = ABY_FACTIONS.find(x=>x.key===ABY.faction) || ABY_FACTIONS[0];
  $("#aby-empty").classList.add("hidden");
  $("#aby-cards").classList.remove("hidden");
  // Faction header
  const facWeak = f.weak ? `Weak to ${abyDmgPip(f.weak,{big:true})}` : `Omni-resist — use your strongest type`;
  const facDeals = f.deals.length ? f.deals.map(t=>abyDmgPip(t)).join("") : "";
  const pct = f.prob[ABY.tier];
  $("#aby-faction-hero").innerHTML = `
    <div class="aby-fh-title" style="color:${f.color}"><span class="aby-fh-glyph">${abyEsc(f.glyph)}</span>${abyEsc(f.name)}</div>
    <div class="aby-fh-line"><span class="aby-stat-k">They deal</span> <span class="aby-stat-v">${facDeals||'<span class="aby-dim">mixed</span>'}</span>
      <span class="aby-fh-sep">·</span> <span class="aby-stat-v">${facWeak}</span></div>
    ${f.spot?`<div class="aby-spot"><b>Spot the room:</b> ${abyEsc(f.spot)}</div>`:""}
    <div class="aby-fh-missile">${f.weak
      ? `<b>Best missile:</b> ${abyMissileTag(f.weak)} <span class="aby-dim">— load ${abyEsc(ABY_DMG[f.weak].label)}${f.key==="sansha"?"; switch to Nova on the Devoted Knight":""}</span>`
      : `<b>Best missile:</b> <span class="aby-dim">omni-resist — no damage hole, so load whatever your hull is bonused for (e.g. a Gila/Cerberus runs Scourge/Kinetic)</span>`}</div>
    <div class="aby-fh-prob" data-tip="${abyEsc(abyProbTip(f))}">
      <span class="aby-stat-k">Likelihood</span> ${abyProbBar(f)}
      <span>~${pct}% of T${ABY.tier} rooms · ${abyProbLabel(pct)}</span>
      <span class="aby-dim">— varies by tier, not weather</span></div>
    <div class="aby-fh-strategy">${abyEsc(f.strategy)}</div>
    ${f.note?`<div class="aby-fh-note">${abyEsc(f.note)}</div>`:""}`;
  // Cards, small→large by class
  const enemies = f.enemies.slice().sort((a,b)=>
    ABY_CLASS_ORDER.indexOf(a.cls)-ABY_CLASS_ORDER.indexOf(b.cls));
  $("#aby-card-grid").innerHTML = enemies.map(e=>abyEnemyCard(e, f, highlight)).join("");
  if(highlight){
    const hl = $("#aby-card-grid").querySelector(".aby-card-hl");
    if(hl) hl.scrollIntoView({behavior:"smooth", block:"center"});
  }
}

// ── Init ───────────────────────────────────────────────────────────────────────
function abyInit(){
  if(ABY._inited) return; ABY._inited = true;
  abyRenderTiers();
  abyRenderWeather();
  abyRenderConditions();
  abyRenderFactionList();
  abyRenderCards();
}
$("#aby-search").addEventListener("input", abySearch);
document.addEventListener("click", (e)=>{
  if(!e.target.closest(".aby-sidebar")){ const r=$("#aby-results"); if(r) r.classList.add("hidden"); }
});

// Sidebar resize handle (mirrors exploration.js)
(function(){
  const handle = $("#aby-resize-handle");
  if(!handle) return;
  const layout = handle.parentElement;
  let dragging = false, startX, startW;
  const saved = localStorage.getItem("aby-sidebar-width");
  if(saved){ layout.style.gridTemplateColumns = saved + "px 6px 1fr"; }
  handle.addEventListener("mousedown", (e)=>{
    e.preventDefault(); dragging = true; startX = e.clientX;
    startW = layout.querySelector(".aby-sidebar").offsetWidth;
    handle.classList.add("active");
    document.body.style.cursor = "col-resize"; document.body.style.userSelect = "none";
  });
  document.addEventListener("mousemove", (e)=>{
    if(!dragging) return;
    const w = Math.max(200, Math.min(600, startW + (e.clientX - startX)));
    layout.style.gridTemplateColumns = w + "px 6px 1fr";
  });
  document.addEventListener("mouseup", ()=>{
    if(!dragging) return; dragging = false; handle.classList.remove("active");
    document.body.style.cursor = ""; document.body.style.userSelect = "";
    localStorage.setItem("aby-sidebar-width", layout.querySelector(".aby-sidebar").offsetWidth);
  });
})();
