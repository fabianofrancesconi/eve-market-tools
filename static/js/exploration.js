// ══════════════════════════════════════════════════════════════════════════
// EXPLORATION TAB
// ══════════════════════════════════════════════════════════════════════════
const EXP_SITES = [
  // ── Highsec Data Sites (Local prefix) ───────────────────────────────────
  {name:"Local Mainframe",altNames:["Local Angel Mainframe","Local Blood Raider Mainframe","Local Guristas Mainframe","Local Sansha Mainframe","Local Serpentis Mainframe"],type:"data",space:["highsec"],danger:"safe",hasNPCs:false,npcType:null,npcDetail:null,hazards:[],triggers:"No triggers. Site is static.",tips:"No NPCs, no timers. Hack all containers freely.",lootTier:"low",lootSummary:"Low-tier datacores, minor decryptors",lootExamples:["Datacores","Minor faction BPCs"],estimatedValue:"0.5-3M"},
  {name:"Local Virus Test Site",altNames:["Local Angel Virus Test Site","Local Blood Raider Virus Test Site","Local Guristas Virus Test Site","Local Sansha Virus Test Site","Local Serpentis Virus Test Site"],type:"data",space:["highsec"],danger:"safe",hasNPCs:false,npcType:null,npcDetail:null,hazards:[],triggers:"No triggers.",tips:"No NPCs. Good practice for new explorers.",lootTier:"low",lootSummary:"Low-tier datacores",lootExamples:["Datacores"],estimatedValue:"0.5-2M"},
  {name:"Local Data Processing Center",altNames:["Local Angel Data Processing Center","Local Blood Raider Data Processing Center","Local Guristas Data Processing Center","Local Sansha Data Processing Center","Local Serpentis Data Processing Center"],type:"data",space:["highsec"],danger:"safe",hasNPCs:false,npcType:null,npcDetail:null,hazards:[],triggers:"No triggers.",tips:"No NPCs. Hack freely.",lootTier:"low",lootSummary:"Datacores, minor decryptors",lootExamples:["Datacores"],estimatedValue:"0.5-3M"},
  {name:"Local Shattered Life-Support Unit",altNames:["Local Angel Shattered Life-Support Unit","Local Blood Raider Shattered Life-Support Unit","Local Guristas Shattered Life-Support Unit","Local Sansha Shattered Life-Support Unit","Local Serpentis Shattered Life-Support Unit"],type:"data",space:["highsec"],danger:"safe",hasNPCs:false,npcType:null,npcDetail:null,hazards:[],triggers:"No triggers.",tips:"No NPCs.",lootTier:"low",lootSummary:"Datacores",lootExamples:["Datacores"],estimatedValue:"0.5-3M"},
  {name:"Local Data Terminal",altNames:["Local Angel Data Terminal","Local Blood Raider Data Terminal","Local Guristas Data Terminal","Local Sansha Data Terminal","Local Serpentis Data Terminal"],type:"data",space:["highsec"],danger:"safe",hasNPCs:false,npcType:null,npcDetail:null,hazards:[],triggers:"No triggers.",tips:"No NPCs.",lootTier:"low",lootSummary:"Datacores",lootExamples:["Datacores"],estimatedValue:"0.5-3M"},
  {name:"Local Backup Server",altNames:["Local Angel Backup Server","Local Blood Raider Backup Server","Local Guristas Backup Server","Local Sansha Backup Server","Local Serpentis Backup Server"],type:"data",space:["highsec"],danger:"safe",hasNPCs:false,npcType:null,npcDetail:null,hazards:[],triggers:"No triggers.",tips:"Hardest highsec data site to scan down. No NPCs.",lootTier:"low",lootSummary:"Datacores, decryptors",lootExamples:["Datacores","Decryptors"],estimatedValue:"1-5M"},
  // ── Lowsec Data Sites (Regional prefix) ─────────────────────────────────
  {name:"Regional Data Fortress",altNames:["Regional Angel Data Fortress","Regional Blood Raider Data Fortress","Regional Guristas Data Fortress","Regional Sansha Data Fortress","Regional Serpentis Data Fortress"],type:"data",space:["lowsec"],danger:"safe",hasNPCs:false,npcType:null,npcDetail:null,hazards:[],triggers:"No triggers. Static site.",tips:"No NPCs. Better loot than highsec equivalents.",lootTier:"low",lootSummary:"Datacores, decryptors, faction BPCs",lootExamples:["Datacores","Decryptors"],estimatedValue:"2-10M"},
  {name:"Regional Mainframe",altNames:["Regional Angel Mainframe","Regional Blood Raider Mainframe","Regional Guristas Mainframe","Regional Sansha Mainframe","Regional Serpentis Mainframe"],type:"data",space:["lowsec"],danger:"safe",hasNPCs:false,npcType:null,npcDetail:null,hazards:[],triggers:"No triggers.",tips:"No NPCs.",lootTier:"low",lootSummary:"Datacores, decryptors",lootExamples:["Datacores","Decryptors"],estimatedValue:"2-10M"},
  {name:"Regional Command Center",altNames:["Regional Angel Command Center","Regional Blood Raider Command Center","Regional Guristas Command Center","Regional Sansha Command Center","Regional Serpentis Command Center"],type:"data",space:["lowsec"],danger:"safe",hasNPCs:false,npcType:null,npcDetail:null,hazards:[],triggers:"No triggers.",tips:"No NPCs.",lootTier:"low",lootSummary:"Datacores, decryptors",lootExamples:["Datacores","Decryptors"],estimatedValue:"2-10M"},
  // ── Nullsec Data Sites (Central prefix) ─────────────────────────────────
  {name:"Central Sparking Transmitter",altNames:["Central Angel Sparking Transmitter","Central Blood Raider Sparking Transmitter","Central Guristas Sparking Transmitter","Central Sansha Sparking Transmitter","Central Serpentis Sparking Transmitter"],type:"data",space:["nullsec","wormhole"],whClass:[1,2,3],danger:"safe",hasNPCs:false,npcType:null,npcDetail:null,hazards:[],triggers:"No triggers. Static site.",tips:"No NPCs. Also spawns in C1-C3 wormholes (pirate faction variant).",lootTier:"medium",lootSummary:"Datacores, decryptors, faction BPCs",lootExamples:["Datacores","Decryptors","Faction module BPCs"],estimatedValue:"5-20M"},
  {name:"Central Survey Site",altNames:["Central Angel Survey Site","Central Blood Raider Survey Site","Central Guristas Survey Site","Central Sansha Survey Site","Central Serpentis Survey Site"],type:"data",space:["nullsec","wormhole"],whClass:[1,2,3],danger:"safe",hasNPCs:false,npcType:null,npcDetail:null,hazards:[],triggers:"No triggers.",tips:"No NPCs. Also found in C1-C3 wormholes.",lootTier:"medium",lootSummary:"Datacores, decryptors, faction BPCs",lootExamples:["Datacores","Decryptors"],estimatedValue:"5-20M"},
  {name:"Central Command Center",altNames:["Central Angel Command Center","Central Blood Raider Command Center","Central Guristas Command Center","Central Sansha Command Center","Central Serpentis Command Center"],type:"data",space:["nullsec","wormhole"],whClass:[1,2,3],danger:"safe",hasNPCs:false,npcType:null,npcDetail:null,hazards:[],triggers:"No triggers.",tips:"No NPCs.",lootTier:"medium",lootSummary:"Datacores, decryptors, faction BPCs",lootExamples:["Datacores","Decryptors"],estimatedValue:"5-20M"},
  {name:"Central Data Mining Site",altNames:["Central Angel Data Mining Site","Central Blood Raider Data Mining Site","Central Guristas Data Mining Site","Central Sansha Data Mining Site","Central Serpentis Data Mining Site"],type:"data",space:["nullsec","wormhole"],whClass:[1,2,3],danger:"safe",hasNPCs:false,npcType:null,npcDetail:null,hazards:[],triggers:"No triggers.",tips:"No NPCs. Hardest to scan of the null data sites.",lootTier:"medium",lootSummary:"Datacores, decryptors, faction BPCs",lootExamples:["Datacores","Decryptors"],estimatedValue:"5-20M"},
  // ── Detected Data Sites (Sov nullsec) ───────────────────────────────────
  {name:"Detected Central Sparking Transmitter",altNames:["Detected Central Angel Sparking Transmitter","Detected Central Blood Raider Sparking Transmitter","Detected Central Guristas Sparking Transmitter","Detected Central Sansha Sparking Transmitter","Detected Central Serpentis Sparking Transmitter"],type:"data",space:["nullsec"],danger:"safe",hasNPCs:false,npcType:null,npcDetail:null,hazards:[],triggers:"No triggers. Spawned by sov infrastructure hub upgrades.",tips:"Same as Central-tier. No NPCs. Only appears in upgraded sov nullsec.",lootTier:"medium",lootSummary:"Datacores, decryptors, faction BPCs",lootExamples:["Datacores","Decryptors","Faction BPCs"],estimatedValue:"5-25M"},
  {name:"Detected Central Survey Site",altNames:["Detected Central Angel Survey Site","Detected Central Blood Raider Survey Site","Detected Central Guristas Survey Site","Detected Central Sansha Survey Site","Detected Central Serpentis Survey Site"],type:"data",space:["nullsec"],danger:"safe",hasNPCs:false,npcType:null,npcDetail:null,hazards:[],triggers:"Sov upgrade spawn.",tips:"Same as Central-tier. No NPCs.",lootTier:"medium",lootSummary:"Datacores, decryptors",lootExamples:["Datacores","Decryptors"],estimatedValue:"5-25M"},
  {name:"Detected Central Command Center",altNames:["Detected Central Angel Command Center","Detected Central Blood Raider Command Center","Detected Central Guristas Command Center","Detected Central Sansha Command Center","Detected Central Serpentis Command Center"],type:"data",space:["nullsec"],danger:"safe",hasNPCs:false,npcType:null,npcDetail:null,hazards:[],triggers:"Sov upgrade spawn.",tips:"No NPCs.",lootTier:"medium",lootSummary:"Datacores, decryptors",lootExamples:["Datacores","Decryptors"],estimatedValue:"5-25M"},
  {name:"Detected Central Data Mining Site",altNames:["Detected Central Angel Data Mining Site","Detected Central Blood Raider Data Mining Site","Detected Central Guristas Data Mining Site","Detected Central Sansha Data Mining Site","Detected Central Serpentis Data Mining Site"],type:"data",space:["nullsec"],danger:"safe",hasNPCs:false,npcType:null,npcDetail:null,hazards:[],triggers:"Sov upgrade spawn.",tips:"No NPCs. Hardest to scan.",lootTier:"medium",lootSummary:"Datacores, decryptors",lootExamples:["Datacores","Decryptors"],estimatedValue:"5-25M"},
  // ── Drone Region Data Sites ─────────────────────────────────────────────
  {name:"Abandoned Research Complex",altNames:["Abandoned Research Complex DA005","Abandoned Research Complex DA015","Abandoned Research Complex DA025","Abandoned Research Complex DC007","Abandoned Research Complex DC035","Abandoned Research Complex DG003","Abandoned Research Complex DG018","Abandoned Research Complex DM083","Detected Abandoned Research Complex"],type:"data",space:["nullsec"],danger:"caution",hasNPCs:true,npcType:"Rogue Drones",npcDetail:"Frigates spawn on FAILED hacks only. Light tackle drones.",hazards:["combat_npcs"],triggers:"NPCs spawn only when you FAIL a hack attempt. Each failed hack spawns a small wave of drone frigates. Successful hacks spawn nothing.",tips:"Drone regions only (Cobalt Edge, Perrigen Falls, Malpais, Oasa, Kalevala Expanse, Outer Passage, Etherium Reach, The Spire). Bring light tank. 'Detected' prefix version spawns from sov upgrades.",lootTier:"medium",lootSummary:"Drone component BPCs, datacores",lootExamples:["Drone component BPCs","Datacores"],estimatedValue:"5-30M"},
  // ── Highsec Relic Sites (Crumbling prefix) ──────────────────────────────
  {name:"Crumbling Antiquated Outpost",altNames:["Crumbling Angel Antiquated Outpost","Crumbling Blood Raider Antiquated Outpost","Crumbling Guristas Antiquated Outpost","Crumbling Sansha Antiquated Outpost","Crumbling Serpentis Antiquated Outpost"],type:"relic",space:["highsec"],danger:"safe",hasNPCs:false,npcType:null,npcDetail:null,hazards:[],triggers:"No triggers. Static site.",tips:"No NPCs. Hack freely. Low value salvage materials.",lootTier:"low",lootSummary:"T1 salvage materials, rig components",lootExamples:["Charred Micro Circuit","Burned Logic Circuit","Fried Interface Circuit"],estimatedValue:"1-5M"},
  {name:"Crumbling Excavation",altNames:["Crumbling Angel Excavation","Crumbling Blood Raider Excavation","Crumbling Guristas Excavation","Crumbling Sansha Excavation","Crumbling Serpentis Excavation"],type:"relic",space:["highsec"],danger:"safe",hasNPCs:false,npcType:null,npcDetail:null,hazards:[],triggers:"No triggers.",tips:"No NPCs.",lootTier:"low",lootSummary:"T1 salvage materials",lootExamples:["Charred Micro Circuit","Burned Logic Circuit"],estimatedValue:"1-5M"},
  {name:"Crumbling Crystal Quarry",altNames:["Crumbling Angel Crystal Quarry","Crumbling Blood Raider Crystal Quarry","Crumbling Guristas Crystal Quarry","Crumbling Sansha Crystal Quarry","Crumbling Serpentis Crystal Quarry"],type:"relic",space:["highsec"],danger:"safe",hasNPCs:false,npcType:null,npcDetail:null,hazards:[],triggers:"No triggers.",tips:"No NPCs.",lootTier:"low",lootSummary:"T1 salvage materials",lootExamples:["Charred Micro Circuit","Burned Logic Circuit"],estimatedValue:"1-5M"},
  // ── Lowsec Relic Sites (Decayed prefix) ─────────────────────────────────
  {name:"Decayed Excavation",altNames:["Decayed Angel Excavation","Decayed Blood Raider Excavation","Decayed Guristas Excavation","Decayed Sansha Excavation","Decayed Serpentis Excavation"],type:"relic",space:["lowsec"],danger:"safe",hasNPCs:false,npcType:null,npcDetail:null,hazards:[],triggers:"No triggers. Static site.",tips:"No NPCs. Better salvage than highsec. Main danger is other players.",lootTier:"low",lootSummary:"T1/T2 salvage materials, rig components",lootExamples:["Armor Plates","Power Circuits","Charred Micro Circuit"],estimatedValue:"3-12M"},
  {name:"Decayed Collision Site",altNames:["Decayed Angel Collision Site","Decayed Blood Raider Collision Site","Decayed Guristas Collision Site","Decayed Sansha Collision Site","Decayed Serpentis Collision Site"],type:"relic",space:["lowsec"],danger:"safe",hasNPCs:false,npcType:null,npcDetail:null,hazards:[],triggers:"No triggers.",tips:"No NPCs.",lootTier:"low",lootSummary:"T1/T2 salvage, rig components",lootExamples:["Armor Plates","Power Circuits"],estimatedValue:"3-12M"},
  {name:"Decayed Crystal Quarry",altNames:["Decayed Angel Crystal Quarry","Decayed Blood Raider Crystal Quarry","Decayed Guristas Crystal Quarry","Decayed Sansha Crystal Quarry","Decayed Serpentis Crystal Quarry"],type:"relic",space:["lowsec"],danger:"safe",hasNPCs:false,npcType:null,npcDetail:null,hazards:[],triggers:"No triggers.",tips:"No NPCs.",lootTier:"low",lootSummary:"T1/T2 salvage",lootExamples:["Armor Plates","Power Circuits"],estimatedValue:"3-12M"},
  // ── Nullsec Relic Sites (Ruined prefix) ─────────────────────────────────
  {name:"Ruined Monument Site",altNames:["Ruined Angel Monument Site","Ruined Blood Raider Monument Site","Ruined Guristas Monument Site","Ruined Sansha Monument Site","Ruined Serpentis Monument Site"],type:"relic",space:["nullsec","wormhole"],whClass:[1,2,3],danger:"safe",hasNPCs:false,npcType:null,npcDetail:null,hazards:[],triggers:"No triggers. Static site.",tips:"No NPCs. Most common null relic site. Also appears in C1-C3 wormholes as pirate faction variant.",lootTier:"medium",lootSummary:"T2 salvage materials, intact armor plates",lootExamples:["Intact Armor Plates","Power Circuits","Armor Plates"],estimatedValue:"5-25M"},
  {name:"Ruined Temple Site",altNames:["Ruined Angel Temple Site","Ruined Blood Raider Temple Site","Ruined Guristas Temple Site","Ruined Sansha Temple Site","Ruined Serpentis Temple Site"],type:"relic",space:["nullsec","wormhole"],whClass:[1,2,3],danger:"safe",hasNPCs:false,npcType:null,npcDetail:null,hazards:[],triggers:"No triggers.",tips:"No NPCs.",lootTier:"medium",lootSummary:"T2 salvage, intact armor plates",lootExamples:["Intact Armor Plates","Power Circuits"],estimatedValue:"5-30M"},
  {name:"Ruined Science Outpost",altNames:["Ruined Angel Science Outpost","Ruined Blood Raider Science Outpost","Ruined Guristas Science Outpost","Ruined Sansha Science Outpost","Ruined Serpentis Science Outpost"],type:"relic",space:["nullsec","wormhole"],whClass:[1,2,3],danger:"safe",hasNPCs:false,npcType:null,npcDetail:null,hazards:[],triggers:"No triggers.",tips:"No NPCs.",lootTier:"medium",lootSummary:"T2 salvage, intact armor plates",lootExamples:["Intact Armor Plates","Power Circuits"],estimatedValue:"8-35M"},
  {name:"Ruined Crystal Quarry",altNames:["Ruined Angel Crystal Quarry","Ruined Blood Raider Crystal Quarry","Ruined Guristas Crystal Quarry","Ruined Sansha Crystal Quarry","Ruined Serpentis Crystal Quarry"],type:"relic",space:["nullsec","wormhole"],whClass:[1,2,3],danger:"safe",hasNPCs:false,npcType:null,npcDetail:null,hazards:[],triggers:"No triggers.",tips:"No NPCs. Rarest null relic. Hardest to scan.",lootTier:"medium",lootSummary:"T2 salvage, intact armor plates",lootExamples:["Intact Armor Plates","Power Circuits"],estimatedValue:"8-40M"},
  // ── Detected Relic Sites (Sov nullsec) ──────────────────────────────────
  {name:"Detected Ruined Monument Site",altNames:["Detected Ruined Angel Monument Site","Detected Ruined Blood Raider Monument Site","Detected Ruined Guristas Monument Site","Detected Ruined Sansha Monument Site","Detected Ruined Serpentis Monument Site"],type:"relic",space:["nullsec"],danger:"safe",hasNPCs:false,npcType:null,npcDetail:null,hazards:[],triggers:"Spawned by sov infrastructure hub upgrades.",tips:"Same as Ruined-tier. No NPCs. Only in upgraded sov null.",lootTier:"medium",lootSummary:"T2 salvage, intact armor plates",lootExamples:["Intact Armor Plates","Power Circuits"],estimatedValue:"5-30M"},
  {name:"Detected Ruined Temple Site",altNames:["Detected Ruined Angel Temple Site","Detected Ruined Blood Raider Temple Site","Detected Ruined Guristas Temple Site","Detected Ruined Sansha Temple Site","Detected Ruined Serpentis Temple Site"],type:"relic",space:["nullsec"],danger:"safe",hasNPCs:false,npcType:null,npcDetail:null,hazards:[],triggers:"Sov upgrade spawn.",tips:"Same as Ruined-tier. No NPCs.",lootTier:"medium",lootSummary:"T2 salvage, intact armor plates",lootExamples:["Intact Armor Plates","Power Circuits"],estimatedValue:"5-30M"},
  {name:"Detected Ruined Science Outpost",altNames:["Detected Ruined Angel Science Outpost","Detected Ruined Blood Raider Science Outpost","Detected Ruined Guristas Science Outpost","Detected Ruined Sansha Science Outpost","Detected Ruined Serpentis Science Outpost"],type:"relic",space:["nullsec"],danger:"safe",hasNPCs:false,npcType:null,npcDetail:null,hazards:[],triggers:"Sov upgrade spawn.",tips:"No NPCs.",lootTier:"medium",lootSummary:"T2 salvage, intact armor plates",lootExamples:["Intact Armor Plates","Power Circuits"],estimatedValue:"8-35M"},
  {name:"Detected Ruined Crystal Quarry",altNames:["Detected Ruined Angel Crystal Quarry","Detected Ruined Blood Raider Crystal Quarry","Detected Ruined Guristas Crystal Quarry","Detected Ruined Sansha Crystal Quarry","Detected Ruined Serpentis Crystal Quarry"],type:"relic",space:["nullsec"],danger:"safe",hasNPCs:false,npcType:null,npcDetail:null,hazards:[],triggers:"Sov upgrade spawn.",tips:"No NPCs.",lootTier:"medium",lootSummary:"T2 salvage, intact armor plates",lootExamples:["Intact Armor Plates","Power Circuits"],estimatedValue:"8-40M"},
  // ── Wormhole Sleeper Relic Sites (Forgotten prefix) ─────────────────────
  {name:"Forgotten Perimeter Coronation Platform",type:"relic",space:["wormhole"],whClass:[1],danger:"dangerous",hasNPCs:true,npcType:"Sleeper",npcDetail:"Frigates and cruisers, ~100-150 DPS total",hazards:["combat_npcs"],triggers:"Sleeper NPCs are already on grid when you land. They aggress immediately on warp-in.",tips:"Cannot be speed-tanked. Need a combat ship (T3D, cruiser+) or fleet. Kill everything first, then hack.",lootTier:"high",lootSummary:"Blue loot + T3 manufacturing materials (ancient salvage)",lootExamples:["Sleeper Drone AI Nexus","Neural Network Analyzer","Ancient Coordinates Database"],estimatedValue:"20-60M"},
  {name:"Forgotten Perimeter Power Array",type:"relic",space:["wormhole"],whClass:[1],danger:"dangerous",hasNPCs:true,npcType:"Sleeper",npcDetail:"Frigates and cruisers, ~100-150 DPS",hazards:["combat_npcs"],triggers:"Sleepers on grid from start. Immediate aggression.",tips:"Clear all NPCs first, then hack.",lootTier:"high",lootSummary:"Blue loot + T3 materials",lootExamples:["Sleeper Drone AI Nexus","Neural Network Analyzer"],estimatedValue:"20-60M"},
  {name:"Forgotten Perimeter Gateway",type:"relic",space:["wormhole"],whClass:[2],danger:"dangerous",hasNPCs:true,npcType:"Sleeper",npcDetail:"Frigates and cruisers, ~150-250 DPS",hazards:["combat_npcs"],triggers:"Sleepers on grid from start. Immediate aggression.",tips:"Tougher than C1 variant. Need cruiser+ or fleet.",lootTier:"high",lootSummary:"Blue loot + T3 materials",lootExamples:["Sleeper Drone AI Nexus","Neural Network Analyzer"],estimatedValue:"25-70M"},
  {name:"Forgotten Perimeter Habitation Coils",type:"relic",space:["wormhole"],whClass:[2],danger:"dangerous",hasNPCs:true,npcType:"Sleeper",npcDetail:"Frigates and cruisers, ~150-250 DPS",hazards:["combat_npcs"],triggers:"Sleepers on grid from start.",tips:"Need cruiser+ or fleet.",lootTier:"high",lootSummary:"Blue loot + T3 materials",lootExamples:["Sleeper Drone AI Nexus","Neural Network Analyzer"],estimatedValue:"25-70M"},
  {name:"Forgotten Frontier Quarantine Outpost",type:"relic",space:["wormhole"],whClass:[3],danger:"dangerous",hasNPCs:true,npcType:"Sleeper",npcDetail:"Cruisers and battlecruisers, ~400-600 DPS",hazards:["combat_npcs"],triggers:"Sleepers on grid from start. Heavy presence.",tips:"Need T3C, battleship, or small fleet.",lootTier:"high",lootSummary:"Blue loot + T3 materials, higher quantities",lootExamples:["Sleeper Drone AI Nexus","Neural Network Analyzer","Ancient Coordinates Database"],estimatedValue:"40-120M"},
  {name:"Forgotten Frontier Recursive Depot",type:"relic",space:["wormhole"],whClass:[3],danger:"dangerous",hasNPCs:true,npcType:"Sleeper",npcDetail:"Cruisers and battlecruisers, ~400-600 DPS",hazards:["combat_npcs"],triggers:"Sleepers on grid from start.",tips:"Need T3C or battleship.",lootTier:"high",lootSummary:"Blue loot + T3 materials",lootExamples:["Sleeper Drone AI Nexus","Neural Network Analyzer"],estimatedValue:"40-120M"},
  {name:"Forgotten Frontier Conversion Module",type:"relic",space:["wormhole"],whClass:[4],danger:"dangerous",hasNPCs:true,npcType:"Sleeper",npcDetail:"Battlecruisers and battleships, ~600-1000 DPS",hazards:["combat_npcs"],triggers:"Sleepers on grid from start. Very heavy.",tips:"Need strong T3C, battleship, or fleet.",lootTier:"high",lootSummary:"Blue loot + T3 materials",lootExamples:["Sleeper Drone AI Nexus","Neural Network Analyzer"],estimatedValue:"60-150M"},
  {name:"Forgotten Frontier Evacuation Center",type:"relic",space:["wormhole"],whClass:[4],danger:"dangerous",hasNPCs:true,npcType:"Sleeper",npcDetail:"Battlecruisers and battleships, ~600-1000 DPS",hazards:["combat_npcs"],triggers:"Sleepers on grid from start.",tips:"Fleet recommended.",lootTier:"high",lootSummary:"Blue loot + T3 materials",lootExamples:["Sleeper Drone AI Nexus","Neural Network Analyzer"],estimatedValue:"60-150M"},
  {name:"Forgotten Core Data Field",type:"relic",space:["wormhole"],whClass:[5],danger:"dangerous",hasNPCs:true,npcType:"Sleeper",npcDetail:"Battleships, ~1200-1800 DPS",hazards:["combat_npcs"],triggers:"Capital-class Sleepers on grid. Immediate aggression.",tips:"Fleet mandatory. Capital escalation possible.",lootTier:"high",lootSummary:"Large quantities of blue loot + T3 materials",lootExamples:["Sleeper Drone AI Nexus","Neural Network Analyzer","Ancient Coordinates Database"],estimatedValue:"80-250M"},
  {name:"Forgotten Core Information Pen",type:"relic",space:["wormhole"],whClass:[5],danger:"dangerous",hasNPCs:true,npcType:"Sleeper",npcDetail:"Battleships, ~1200-1800 DPS",hazards:["combat_npcs"],triggers:"Capital-class Sleepers on grid.",tips:"Fleet mandatory.",lootTier:"high",lootSummary:"Blue loot + T3 materials",lootExamples:["Sleeper Drone AI Nexus","Neural Network Analyzer"],estimatedValue:"80-250M"},
  {name:"Forgotten Core Assembly Hall",type:"relic",space:["wormhole"],whClass:[6],danger:"dangerous",hasNPCs:true,npcType:"Sleeper",npcDetail:"Battleships + dreadnought-class, ~2000+ DPS",hazards:["combat_npcs"],triggers:"Strongest Sleepers on grid from landing. Capital escalation.",tips:"Capital fleet required. Solo impossible.",lootTier:"high",lootSummary:"Massive blue loot + T3 materials",lootExamples:["Sleeper Drone AI Nexus","Neural Network Analyzer","Ancient Coordinates Database"],estimatedValue:"100-300M"},
  {name:"Forgotten Core Circuitry Disassembler",type:"relic",space:["wormhole"],whClass:[6],danger:"dangerous",hasNPCs:true,npcType:"Sleeper",npcDetail:"Battleships + dreadnought-class, ~2000+ DPS",hazards:["combat_npcs"],triggers:"Strongest Sleepers on grid.",tips:"Capital fleet required.",lootTier:"high",lootSummary:"Massive blue loot + T3 materials",lootExamples:["Sleeper Drone AI Nexus","Neural Network Analyzer"],estimatedValue:"100-300M"},
  // ── Wormhole Sleeper Data Sites (Unsecured prefix) ──────────────────────
  {name:"Unsecured Perimeter Amplifier",type:"data",space:["wormhole"],whClass:[1],danger:"dangerous",hasNPCs:true,npcType:"Sleeper",npcDetail:"Frigates and cruisers, ~100-150 DPS",hazards:["combat_npcs"],triggers:"Sleepers on grid when you land. Aggress immediately.",tips:"Must clear NPCs before hacking. Combat ship required.",lootTier:"medium",lootSummary:"Datacores, sleeper components, skillbooks",lootExamples:["Sleeper Data Library","Sleeper Drone AI Nexus"],estimatedValue:"10-40M"},
  {name:"Unsecured Perimeter Information Center",type:"data",space:["wormhole"],whClass:[1],danger:"dangerous",hasNPCs:true,npcType:"Sleeper",npcDetail:"Frigates and cruisers, ~100-150 DPS",hazards:["combat_npcs"],triggers:"Sleepers on grid. Immediate aggression.",tips:"Combat ship required.",lootTier:"medium",lootSummary:"Datacores, sleeper components",lootExamples:["Sleeper Data Library"],estimatedValue:"10-40M"},
  {name:"Unsecured Perimeter Comms Relay",type:"data",space:["wormhole"],whClass:[2],danger:"dangerous",hasNPCs:true,npcType:"Sleeper",npcDetail:"Frigates and cruisers, ~150-250 DPS",hazards:["combat_npcs"],triggers:"Sleepers on grid. Immediate aggression.",tips:"Need cruiser+ or fleet.",lootTier:"medium",lootSummary:"Datacores, sleeper components",lootExamples:["Sleeper Data Library"],estimatedValue:"15-50M"},
  {name:"Unsecured Perimeter Transponder Farm",type:"data",space:["wormhole"],whClass:[2],danger:"dangerous",hasNPCs:true,npcType:"Sleeper",npcDetail:"Frigates and cruisers, ~150-250 DPS",hazards:["combat_npcs"],triggers:"Sleepers on grid. Immediate aggression.",tips:"Need cruiser+ or fleet.",lootTier:"medium",lootSummary:"Datacores, sleeper components",lootExamples:["Sleeper Data Library","Sleeper Drone AI Nexus"],estimatedValue:"15-50M"},
  {name:"Unsecured Frontier Database",type:"data",space:["wormhole"],whClass:[3],danger:"dangerous",hasNPCs:true,npcType:"Sleeper",npcDetail:"Cruisers and battlecruisers, ~400-600 DPS",hazards:["combat_npcs"],triggers:"Sleepers on grid. Heavy presence.",tips:"T3C or battleship needed.",lootTier:"medium",lootSummary:"Datacores, sleeper components",lootExamples:["Sleeper Data Library","Sleeper Drone AI Nexus"],estimatedValue:"25-70M"},
  {name:"Unsecured Frontier Receiver",type:"data",space:["wormhole"],whClass:[3],danger:"dangerous",hasNPCs:true,npcType:"Sleeper",npcDetail:"Cruisers and battlecruisers, ~400-600 DPS",hazards:["combat_npcs"],triggers:"Sleepers on grid.",tips:"T3C or battleship needed.",lootTier:"medium",lootSummary:"Datacores, sleeper components",lootExamples:["Sleeper Data Library"],estimatedValue:"25-70M"},
  {name:"Unsecured Frontier Digital Nexus",type:"data",space:["wormhole"],whClass:[4],danger:"dangerous",hasNPCs:true,npcType:"Sleeper",npcDetail:"Battlecruisers and battleships, ~600-1000 DPS",hazards:["combat_npcs"],triggers:"Very heavy Sleeper presence on grid.",tips:"Fleet recommended.",lootTier:"high",lootSummary:"High-value datacores, sleeper components",lootExamples:["Sleeper Data Library","Sleeper Drone AI Nexus"],estimatedValue:"40-100M"},
  {name:"Unsecured Frontier Trinary Hub",type:"data",space:["wormhole"],whClass:[4],danger:"dangerous",hasNPCs:true,npcType:"Sleeper",npcDetail:"Battlecruisers and battleships, ~600-1000 DPS",hazards:["combat_npcs"],triggers:"Heavy Sleeper presence.",tips:"Fleet recommended.",lootTier:"high",lootSummary:"Datacores, sleeper components",lootExamples:["Sleeper Data Library","Sleeper Drone AI Nexus"],estimatedValue:"40-100M"},
  {name:"Unsecured Frontier Enclave Relay",type:"data",space:["wormhole"],whClass:[5],danger:"dangerous",hasNPCs:true,npcType:"Sleeper",npcDetail:"Battleships, ~1200-1800 DPS",hazards:["combat_npcs"],triggers:"Capital-class Sleepers on grid.",tips:"Fleet mandatory.",lootTier:"high",lootSummary:"High-value datacores and sleeper components",lootExamples:["Sleeper Data Library","Sleeper Drone AI Nexus"],estimatedValue:"60-180M"},
  {name:"Unsecured Frontier Server Bank",type:"data",space:["wormhole"],whClass:[5],danger:"dangerous",hasNPCs:true,npcType:"Sleeper",npcDetail:"Battleships, ~1200-1800 DPS",hazards:["combat_npcs"],triggers:"Capital-class Sleepers.",tips:"Fleet mandatory.",lootTier:"high",lootSummary:"Datacores, sleeper components",lootExamples:["Sleeper Data Library","Sleeper Drone AI Nexus"],estimatedValue:"60-180M"},
  {name:"Unsecured Core Backup Array",type:"data",space:["wormhole"],whClass:[6],danger:"dangerous",hasNPCs:true,npcType:"Sleeper",npcDetail:"Battleships + dreads, ~2000+ DPS",hazards:["combat_npcs"],triggers:"Strongest Sleepers on grid.",tips:"Capital fleet required.",lootTier:"high",lootSummary:"Highest-value sleeper components",lootExamples:["Sleeper Data Library","Sleeper Drone AI Nexus"],estimatedValue:"80-200M"},
  {name:"Unsecured Core Emergence",type:"data",space:["wormhole"],whClass:[6],danger:"dangerous",hasNPCs:true,npcType:"Sleeper",npcDetail:"Battleships + dreads, ~2000+ DPS",hazards:["combat_npcs"],triggers:"Strongest Sleepers.",tips:"Capital fleet required.",lootTier:"high",lootSummary:"Highest-value sleeper components",lootExamples:["Sleeper Data Library","Sleeper Drone AI Nexus"],estimatedValue:"80-200M"},
  // ── Ghost Sites ─────────────────────────────────────────────────────────
  {name:"Lesser Covert Research Facility",altNames:["Lesser Sansha Covert Research Facility","Lesser Blood Raider Covert Research Facility","Lesser Angel Covert Research Facility","Lesser Guristas Covert Research Facility","Lesser Serpentis Covert Research Facility"],type:"ghost",space:["highsec"],danger:"dangerous",hasNPCs:true,npcType:"Faction Response Fleet",npcDetail:"Response fleet warps in when timer expires. Warp disruption (24km range).",hazards:["exploding_cans","response_fleet"],triggers:"A hidden timer starts the MOMENT you land on grid (~30s-3min). When timer expires OR you fail a hack: ALL containers explode simultaneously dealing ~6000 explosive damage in 10km radius. Response fleet warps in with warp disruptors.",tips:"Hack ONE container as fast as possible, loot it, warp out immediately. Never attempt a second container. Fit explosive hardeners if worried.",lootTier:"high",lootSummary:"Implant BPCs (Ascendancy set), Covert Research Tools",lootExamples:["Mid-grade Ascendancy Alpha BPC","Covert Research Tools","Shattered Villard Wheel"],estimatedValue:"10-100M"},
  {name:"Standard Covert Research Facility",altNames:["Standard Sansha Covert Research Facility","Standard Blood Raider Covert Research Facility","Standard Angel Covert Research Facility","Standard Guristas Covert Research Facility","Standard Serpentis Covert Research Facility"],type:"ghost",space:["lowsec"],danger:"dangerous",hasNPCs:true,npcType:"Faction Response Fleet",npcDetail:"Stronger response fleet with warp disruption.",hazards:["exploding_cans","response_fleet"],triggers:"Same timer mechanic as Lesser but explosions deal ~8000 explosive damage (10km radius). Timer starts on grid landing.",tips:"One hack, one loot, warp out. Explosions can kill poorly-tanked cruisers. Fit explosive hardeners.",lootTier:"high",lootSummary:"Wetu/Packrat mobile depot BPCs, mid/high-grade Ascendancy BPCs",lootExamples:["Wetu Mobile Depot BPC","High-grade Ascendancy Alpha BPC","Covert Research Tools"],estimatedValue:"30-200M"},
  {name:"Improved Covert Research Facility",altNames:["Improved Sansha Covert Research Facility","Improved Blood Raider Covert Research Facility","Improved Angel Covert Research Facility","Improved Guristas Covert Research Facility","Improved Serpentis Covert Research Facility"],type:"ghost",space:["nullsec"],danger:"dangerous",hasNPCs:true,npcType:"Faction Response Fleet",npcDetail:"Strong response fleet with warp disruption.",hazards:["exploding_cans","response_fleet"],triggers:"~10,000 explosive damage on explosion (10km radius). Hidden timer from landing. Enough to kill most cruisers outright.",tips:"One hack, loot, warp. Must have explosive tank or be prepared to lose your ship. Jackpot-tier loot justifies the risk.",lootTier:"jackpot",lootSummary:"Wetu/Yurt/Magpie BPCs, high-grade Ascendancy BPCs",lootExamples:["Yurt Mobile Depot BPC","High-grade Ascendancy Omega BPC","Covert Research Tools"],estimatedValue:"50-500M"},
  {name:"Superior Covert Research Facility",altNames:["Superior Sansha Covert Research Facility","Superior Blood Raider Covert Research Facility","Superior Angel Covert Research Facility","Superior Guristas Covert Research Facility","Superior Serpentis Covert Research Facility"],type:"ghost",space:["wormhole"],danger:"dangerous",hasNPCs:true,npcType:"Sleeper Response Fleet",npcDetail:"Sleeper response fleet (deadlier than faction). Warp disruption.",hazards:["exploding_cans","response_fleet"],triggers:"~12,000 explosive damage on explosion (10km radius). Sleeper response fleet instead of faction NPCs. Same timer mechanic.",tips:"One hack, loot, warp immediately. Sleeper response fleet will kill anything that stays. Found in all wormhole classes.",lootTier:"jackpot",lootSummary:"Magpie/Wetu/Yurt BPCs, high-grade Ascendancy Omega BPC",lootExamples:["Magpie Mobile Depot BPC","High-grade Ascendancy Omega BPC","Covert Research Tools"],estimatedValue:"80-800M"},
  // ── Sleeper Caches ──────────────────────────────────────────────────────
  {name:"Limited Sleeper Cache",type:"sleeper_cache",space:["highsec","lowsec","nullsec","wormhole"],danger:"caution",hasNPCs:false,npcType:null,npcDetail:null,hazards:["env_damage"],triggers:"Entrance hack (7/10 Data): failure starts a 2-minute self-destruct timer for the entire site. Sentry turrets activate when you approach containers. Pressure clouds deal ~100 DPS (omni). Proximity mines deal 500 damage + spawn damage cloud.",tips:"Frigates only (no stealth bombers). Hack the entrance container first. Use tractor beam to pull containers out of clouds. Bring tank for ~200 DPS.",lootTier:"medium",lootSummary:"Polarized weapon BPCs, sleeper blue loot, datacores",lootExamples:["Polarized Torpedo Launcher BPC","Wrecked Components","Datacores"],estimatedValue:"10-60M"},
  {name:"Standard Sleeper Cache",type:"sleeper_cache",space:["lowsec","nullsec","wormhole"],danger:"dangerous",hasNPCs:false,npcType:null,npcDetail:null,hazards:["env_damage"],triggers:"Entrance hack (7/10 Data): failure = 2-min site despawn. Alarm system increments on failed hacks. Sentry Towers deal ~1000 damage every 15s (250km range). Guardian Extermination Units create damage clouds (400-1000 DPS). Hidden Room containers on 3-minute self-destruct timer.",tips:"Need BOTH Data AND Relic Analyzers. Multiple puzzle rooms. Bring good tank (500+ DPS in clouds), mobile depot to refit. 20-30 min to complete.",lootTier:"high",lootSummary:"Polarized weapon BPCs, faction modules, implant BPCs, blue loot",lootExamples:["Polarized Torpedo Launcher BPC","Intact Armor Plates","Sleeper Components"],estimatedValue:"50-250M"},
  {name:"Superior Sleeper Cache",type:"sleeper_cache",space:["nullsec","wormhole"],danger:"dangerous",hasNPCs:false,npcType:null,npcDetail:null,hazards:["env_damage"],triggers:"Entrance hack (7/10): failure = 2-min despawn. Solray room: Solar Flare deals ~600 DPS EM if alignment disc not placed. Mine Room: hidden mine deals 5,000 damage on entry. Turret Room: up to 32 sentry towers. Plasma Chambers explode for 100,000 damage (250km radius).",tips:"Most complex PvE site in EVE. Need Data + Relic Analyzers, 800+ DPS tank, mobile depot. 45-90 min. Remote-rep the Pristine cache for bonus loot. Use AB not MWD (bloom triggers mines).",lootTier:"jackpot",lootSummary:"Polarized BPCs, Isotropic Deposition Guides, storyline module BPCs",lootExamples:["Polarized Torpedo Launcher BPC","Isotropic Deposition Guide","Neural Network Analyzer"],estimatedValue:"100-600M+"},
  // ── Wormhole Gas Sites (Fullerite) ──────────────────────────────────────
  {name:"Barren Perimeter Reservoir",type:"gas",space:["wormhole"],whClass:[1,2,3],danger:"caution",hasNPCs:true,npcType:"Sleeper",npcDetail:"Frigates/cruisers spawn 15-20 min after first warp-in",hazards:["timed_spawn"],triggers:"NPC spawn timer starts when the FIRST player warps into the site (not necessarily you). Sleeper frigates/cruisers appear 15-20 minutes later.",tips:"Set a 15-min timer on landing. Huff gas aligned to a celestial. Warp out before timer expires.",lootTier:"low",lootSummary:"Fullerite C50/C60 gas (T3 production)",lootExamples:["Fullerite-C50","Fullerite-C60"],estimatedValue:"5-20M"},
  {name:"Token Perimeter Reservoir",type:"gas",space:["wormhole"],whClass:[1,2,3],danger:"caution",hasNPCs:true,npcType:"Sleeper",npcDetail:"Frigates/cruisers spawn 15-20 min after first warp-in",hazards:["timed_spawn"],triggers:"Same 15-20 min delayed spawn mechanic. Timer is per-site, not per-player.",tips:"Huff aligned, warp at 15 min.",lootTier:"low",lootSummary:"Fullerite C60/C70 gas",lootExamples:["Fullerite-C60","Fullerite-C70"],estimatedValue:"5-20M"},
  {name:"Minor Perimeter Reservoir",type:"gas",space:["wormhole"],whClass:[1,2,3],danger:"caution",hasNPCs:true,npcType:"Sleeper",npcDetail:"Frigates/cruisers spawn 15-20 min after first warp-in",hazards:["timed_spawn"],triggers:"15-20 min delayed NPC spawn.",tips:"Huff aligned, warp at 15 min.",lootTier:"low",lootSummary:"Fullerite C70/C72 gas",lootExamples:["Fullerite-C70","Fullerite-C72"],estimatedValue:"8-25M"},
  {name:"Ordinary Perimeter Reservoir",type:"gas",space:["wormhole"],whClass:[1,2,3],danger:"caution",hasNPCs:true,npcType:"Sleeper",npcDetail:"Frigates/cruisers spawn 15-20 min after first warp-in",hazards:["timed_spawn"],triggers:"15-20 min delayed NPC spawn.",tips:"Huff aligned, warp at 15 min.",lootTier:"low",lootSummary:"Fullerite C72/C84 gas",lootExamples:["Fullerite-C72","Fullerite-C84"],estimatedValue:"10-30M"},
  {name:"Sizable Perimeter Reservoir",type:"gas",space:["wormhole"],whClass:[1,2,3,4],danger:"caution",hasNPCs:true,npcType:"Sleeper",npcDetail:"Frigates/cruisers spawn 15-20 min after first warp-in",hazards:["timed_spawn"],triggers:"15-20 min delayed NPC spawn.",tips:"Huff aligned, warp at 15 min.",lootTier:"medium",lootSummary:"Fullerite C84/C50 gas",lootExamples:["Fullerite-C84","Fullerite-C50"],estimatedValue:"10-35M"},
  {name:"Bountiful Frontier Reservoir",type:"gas",space:["wormhole"],whClass:[3,4],danger:"caution",hasNPCs:true,npcType:"Sleeper",npcDetail:"Cruisers/battlecruisers spawn 15-20 min after first warp-in",hazards:["timed_spawn"],triggers:"15-20 min timer. Stronger NPCs than Perimeter sites (cruisers/BCs).",tips:"Higher-class gas. NPCs are stronger when they spawn. Consider Prospect (covert cloak).",lootTier:"medium",lootSummary:"Fullerite C28/C32 gas",lootExamples:["Fullerite-C28","Fullerite-C32"],estimatedValue:"15-50M"},
  {name:"Vast Frontier Reservoir",type:"gas",space:["wormhole"],whClass:[3,4],danger:"caution",hasNPCs:true,npcType:"Sleeper",npcDetail:"Cruisers/battlecruisers spawn 15-20 min after first warp-in",hazards:["timed_spawn"],triggers:"15-20 min timer. Cruiser-class NPCs.",tips:"Stronger NPCs. Huff and run.",lootTier:"medium",lootSummary:"Fullerite C32/C28 gas",lootExamples:["Fullerite-C32","Fullerite-C28"],estimatedValue:"15-50M"},
  {name:"Instrumental Core Reservoir",type:"gas",space:["wormhole"],whClass:[5,6],danger:"caution",hasNPCs:true,npcType:"Sleeper",npcDetail:"Battleships spawn 15-20 min after first warp-in",hazards:["timed_spawn"],triggers:"15-20 min timer. Battleship-class Sleepers spawn.",tips:"C5/C6 gas. Highest value fullerites. Battleship Sleepers will one-shot a Venture. Must warp before spawn.",lootTier:"high",lootSummary:"Fullerite C320/C540 gas (very valuable)",lootExamples:["Fullerite-C320","Fullerite-C540"],estimatedValue:"60-250M"},
  {name:"Vital Core Reservoir",type:"gas",space:["wormhole"],whClass:[5,6],danger:"caution",hasNPCs:true,npcType:"Sleeper",npcDetail:"Battleships spawn 15-20 min after first warp-in",hazards:["timed_spawn"],triggers:"15-20 min timer. Battleship Sleepers.",tips:"Highest value gas. Must warp before spawn.",lootTier:"high",lootSummary:"Fullerite C540/C320 gas",lootExamples:["Fullerite-C540","Fullerite-C320"],estimatedValue:"60-250M"},
  // ── K-space Gas Sites (Mykoserocin) ─────────────────────────────────────
  {name:"Mykoserocin Nebula",altNames:["Sister Nebula","Helix Nebula","Wild Nebula","Blackeye Nebula","Sunspark Nebula","Diablo Nebula","Smoking Nebula","Ring Nebula","Calabash Nebula","Glass Nebula","Bright Nebula","Sparking Nebula","Ghost Nebula","Eagle Nebula","Flame Nebula","Pipe Nebula","Mykoserocin"],type:"gas",space:["highsec","lowsec"],danger:"safe",hasNPCs:false,npcType:null,npcDetail:null,hazards:[],triggers:"No triggers. Static site. Mine indefinitely.",tips:"K-space gas sites with no NPCs. Safe to mine without time pressure. Found in specific regions based on nebula name. Used for Synth booster production.",lootTier:"low",lootSummary:"Mykoserocin gas (Synth booster production)",lootExamples:["Lime Mykoserocin","Azure Mykoserocin","Golden Mykoserocin"],estimatedValue:"5-20M"},
  // ── K-space Gas Sites (Cytoserocin) ─────────────────────────────────────
  {name:"Cytoserocin Nebula",altNames:["Emerald Nebula","Cheetah Nebula","Duo Nebula","Leopard Nebula","Rimy Nebula","Crimson Nebula","Cobra Nebula","Bandit Nebula","Profiteer Nebula","Phoenix Nebula","Forgotten Nebula","Rapture Nebula","Saintly Nebula","Crystal Nebula","Moth Nebula","Lion Nebula","Diamond Nebula","Cardinal Nebula","Cytoserocin"],type:"gas",space:["lowsec","nullsec"],danger:"caution",hasNPCs:true,npcType:"Varies",npcDetail:"Some sites have NPCs on grid, others are NPC-free. Some gas clouds deal damage per mining cycle.",hazards:["combat_npcs","env_damage"],triggers:"Varies by specific nebula. Some have NPCs on grid from the start. Some gas clouds deal 800-6000 damage per mining cycle.",tips:"Used for Improved booster production. Found in specific lowsec/nullsec regions. Check the specific nebula name for hazards.",lootTier:"medium",lootSummary:"Cytoserocin gas (Improved booster production)",lootExamples:["Lime Cytoserocin","Amber Cytoserocin","Vermillion Cytoserocin"],estimatedValue:"20-80M"},
];

// ══════════════════════════════════════════════════════════════════════════
// "HOW TO TACKLE IT" — per-site playbooks
//
// Rather than hand-writing 60 near-identical guides, we derive a playbook id
// from the site's type / space / wormhole-class and render a tailored plan.
// The tricky sites (ghost, sleeper caches, gas) get dedicated, per-tier plans.
// Sources: EVE University wiki (Hacking, Exploration, Data/Relic site, Ghost
// Sites, Sleeper Cache, Wormhole sites, Gas cloud harvesting). Exact numbers
// are "per wiki" — CCP tweaks them between patches.
// ══════════════════════════════════════════════════════════════════════════

function expPlaybookId(site){
  if(site.type==="ghost"){
    const sp = site.space[0];
    return sp==="highsec" ? "ghost_lesser"
         : sp==="lowsec"  ? "ghost_standard"
         : sp==="nullsec" ? "ghost_improved"
         : "ghost_superior";
  }
  if(site.type==="sleeper_cache"){
    return site.name.includes("Limited") ? "cache_limited"
         : site.name.includes("Superior") ? "cache_superior"
         : "cache_standard";
  }
  if(site.type==="gas"){
    if(site.space.includes("wormhole")) return "gas_wh";
    return site.name.includes("Mykoserocin") ? "gas_myko" : "gas_cyto";
  }
  // Sleeper-guarded relic/data in w-space = a combat site you clear, then hack.
  if(site.hasNPCs && site.npcType && site.npcType.indexOf("Sleeper")>=0
     && site.space.includes("wormhole")) return "wh_combat";
  if(site.hasNPCs && site.npcType==="Rogue Drones") return "data_drone";
  return site.type==="relic" ? "relic_safe" : "data_safe";
}

// Ship advice for the "safe" k-space data/relic sites scales with the most
// dangerous space the site appears in.
function expShipFor(site){
  const sp = site.space;
  if(sp.includes("nullsec") || sp.includes("wormhole"))
    return "A Covert Ops frigate (Buzzard, Helios, Anathema, Cheetah) or an Astero — a Covert Ops Cloak is essential this deep.";
  if(sp.includes("lowsec"))
    return "A T1 exploration frigate works, but an Astero or Covert Ops frigate lets you warp cloaked and shake hunters.";
  return "A cheap T1 exploration frigate — Heron, Imicus, Magnate or Probe. Losing one costs almost nothing.";
}

function expWhShip(whClass){
  const c = Math.min(...(whClass && whClass.length ? whClass : [1]));
  if(c<=2)   return "C1–C2 can be soloed by a single T2-fit battlecruiser.";
  if(c===3)  return "C3: a battleship (Rattlesnake / well-fit Praxis) or a small T2 cruiser gang with logi.";
  if(c===4)  return "C4: a fleet of 5–6 battleships.";
  return "C5–C6: an organised fleet with dedicated logi + EWAR (capital escalation likely).";
}

const GHOST_INFO = {
  ghost_lesser:   {rats:"faction",  dmg:"6,000"},
  ghost_standard: {rats:"faction",  dmg:"8,000"},
  ghost_improved: {rats:"faction",  dmg:"10,000"},
  ghost_superior: {rats:"Sleeper",  dmg:"12,000"},
};

// Returns {modules, ship, steps[], note?, safety}. `note` is the minigame-
// specific gotcha; `safety` is the single most important rule for the site.
function expTackle(site){
  const pid = expPlaybookId(site);

  if(pid==="data_safe" || pid==="relic_safe"){
    const isData = site.type==="data";
    return {
      modules: isData
        ? "A Data Analyzer (carry a Relic Analyzer too so you can run whatever you scan). T2 pays off in null."
        : "A Relic Analyzer (carry a Data Analyzer too). T2 pays off in null.",
      ship: expShipFor(site),
      steps: [
        "Scan the signature to 100% with combat probes, then warp in.",
        "There are no NPCs and no triggers — cargo-scan the cans and skip the junk.",
        "Hack the good cans (see the minigame primer at the top of this tab).",
        "Fly through the loot spew to scoop the cans that burst open on a successful hack.",
        "Warp out — the loot is worth far more than the paper-thin hull.",
      ],
      note: "Two failed hacks destroy a can and its loot. Grab utility subsystems early and kill Restoration Nodes / Virus Suppressors first.",
      safety: "No NPCs, no triggers — the only threat is other capsuleers. Watch D-scan and Local, and warp off the instant someone probes you.",
    };
  }

  if(pid==="data_drone"){
    return {
      modules: "A Data Analyzer, plus a flight of light combat drones.",
      ship: "A T1 exploration frigate is plenty — the drones are trivial. Just carry drones.",
      steps: [
        "Scan down and warp in (drone regions, nullsec only).",
        "Start hacking the High-Security Containment Facility cans.",
        "If a hack FAILS, a few rogue-drone frigates spawn — pop them with your drones, then resume hacking.",
        "Hack the Research & Development can too (usually empty, but skipping it forfeits the escalation chance).",
        "Watch for an escalation into a follow-up drone site, then warp out.",
      ],
      note: "Unlike pirate data sites, a failed hack here does NOT destroy the can — it spawns drones instead, so a fail is recoverable.",
      safety: "The drones are weak — nullsec PvP is the real danger. Always carry drones so a failed hack can't strand you mid-site.",
    };
  }

  if(pid==="wh_combat"){
    const isData = site.type==="data";
    return {
      modules: (isData ? "A Data Analyzer" : "A Relic Analyzer")
        + " on a full combat, omni-tanked fit — bring salvagers too.",
      ship: expWhShip(site.whClass),
      steps: [
        "Scan the signature and warp in with a COMBAT ship — this is a combat site, not a quiet hack.",
        "Clear every Sleeper wave (kill the trigger ship to spawn the next). Omni-tank all four damage types.",
        "Only once the grid is completely clear, hack the relic/data cans.",
        "Salvage the Sleeper wrecks — the blue loot is most of the ISK.",
        "Warp back to your exit hole.",
      ],
      note: "Sleepers can't be neuted (infinite cap) but can be jammed/damped. Higher classes remote-rep each other and warp-scramble.",
      safety: "No Local chat in wormhole space — watch D-scan constantly and always know your way out. Never stop to hack while Sleepers are alive.",
    };
  }

  if(pid.indexOf("ghost")===0){
    const g = GHOST_INFO[pid];
    return {
      modules: "A Data Analyzer II — speed matters, so fit for a fast lock and a fast align.",
      ship: "A tank-one-blast fit (~13k explosive shield EHP: MWD, Data Analyzer II, explosive shield hardener, extender) — Heron Navy Issue on a budget, or a Buzzard/Helios. Armour face-tankers (Stratios, T3C) can simply sit through the blast.",
      steps: [
        "Warp in — landing cloaked delays the hidden timer until you decloak.",
        "Cargo-scan and pick the single best can.",
        "Hack that ONE can and grab the loot.",
        "Warp out before the visible 30-second countdown hits 0. Skilled pilots grab 2–3 cans, but never gamble the final seconds.",
      ],
      note: "A failed hack destroys only that can. Warping mid-hack auto-fails it and can trigger the blast early.",
      safety: `One hack, grab, warp. When the countdown ends the ${g.rats} response fleet detonates EVERY can for ~${g.dmg} explosive damage in a 10 km radius, then warp-disrupts survivors (24 km). Be finished and 40+ km clear of the cans.`,
    };
  }

  if(pid.indexOf("cache")===0){
    const modules = "BOTH a Data AND a Relic Analyzer (T2 recommended). Turn Auto-Repeat OFF, and bring a Mobile Depot to refit and stash bulky loot.";
    const note = "Shows up as a data site but needs both analyzers. The site clears from the scanner the moment you hack the first relic can, so save that for last.";
    if(pid==="cache_limited") return {
      modules, note,
      ship: "Frigate-only site — a hacking frigate (Heron/Magnate), or an Astero on an armour fit. You need ~60 EHP/s sustained and cap stability to sit in the gas cloud.",
      steps: [
        "Hack the Hyperfluct entry can (7/10 — a fail despawns the whole site in 2 min) and take the Spatial Rift.",
        "Cargo-scan the depots. Approach on AFTERBURNER at ~200 m/s — never MWD.",
        "Clear the wreck-cloud depot: hack its Remote Pressure Control Unit (7/10) to disperse the ~100 DPS cloud.",
        "Handle the force-field depot — hack the RDGU (8/10), or DPS it down, or MWD inside before the bubble forms.",
        "Hack the remaining depots, refit at your depot if needed, scoop, and leave.",
      ],
      safety: "Move slowly on an afterburner — MWD signature bloom trips the Unstable Plasma 'mines' (500 dmg + AoE) from much farther away. Do not fit an MWD for the approach.",
    };
    if(pid==="cache_superior") return {
      modules, note,
      ship: "The hardest PvE site in EVE. A strongly-tanked T3C or battleship (~900–2,000 EHP/s, cap-stable) for the Turret/Archive rooms. A frigate can do everything EXCEPT the Archive, alarm-check and Mine rooms.",
      steps: [
        "Always run the Solray room first — the entry rift's warp distance tells you where you land (≈31,000 km = Solray).",
        "Stabilise the Solray: hack the Observational Unit and place the matching disc to cut its ~600 DPS to ~20.",
        "Mine room ONLY with ~70k EHP (a failed hack hits for 10–25k). No MWD — mines trip at 17–30 km.",
        "Sentry/Turret room: ~900–1,000 EHP/s cap-stable, and NEVER shoot a tower — it trips the alarm and you get alpha'd.",
        "Archive room only with a ~2,000 EHP/s tank; orbit the Cerebrum at 60 km to dodge the shockwaves.",
        "Remote-rep the lone Pristine cache's battery for the bonus loot.",
      ],
      safety: "Never trip the alarm or aggress a Plasma Chamber (100,000 dmg, ~250 km) unless the procedure calls for it. Match your ship to each room and skip any phase your tank can't cover.",
    };
    return { // cache_standard
      modules, note,
      ship: "Runnable in a T1 frigate but tight — you need ~1,200 EHP/s to tank the gas clouds indefinitely, plus light drones/sentries for the Sentry Towers. Bring a depot to refit between rooms.",
      steps: [
        "Hack the Hyperfluct (7/10), take the rift, and drop your depot.",
        "Stay ~100 km off the alarm-cloud spawns; hack the 3 Coordinate Plotting cans (7/10 each).",
        "Refit to an AB buffer + EM/Thermal hardeners + guns, rift to the back room, and kill the Sentry Towers (orbit tight, on an afterburner).",
        "Refit to Relic + cargo + MWD, hack the storage depots, then the hidden room.",
        "Cargo-scan and hack the valuable cans first; you may reset the self-destruct once (8/10).",
      ],
      safety: "Sentry Towers hit for ~1,000 out to 250 km with no cover — orbit tight on an afterburner and let drones/sentries kill them. Use an MWD only to escape; its sig bloom gets you alpha'd.",
    };
  }

  if(pid==="gas_wh") return {
    modules: "Gas Cloud Scoops (or Gas Cloud Harvesters on a mining hull).",
    ship: "A Venture to start (gas bonus + built-in warp-core stab), or a Prospect to warp cloaked in hostile space.",
    steps: [
      "Warp in and start huffing — the site is undefended at first.",
      "Stay ALIGNED to a safe (station/exit) the entire time.",
      "Watch the clock and D-scan: Sleepers spawn ~15–20 min after the FIRST pilot arrived.",
      "Warp off as the timer approaches, or the instant Sleepers show on D-scan.",
    ],
    note: "The spawn timer is per-site (it started when the first pilot warped in), not per-pilot — you may have far less than the full 15–20 min.",
    safety: "'Huff aligned and warp.' There's no Local in wormhole space, so D-scan is your only warning — the delayed Sleeper spawn will wipe an untanked Venture.",
  };

  if(pid==="gas_myko") return {
    modules: "Gas Cloud Scoops.",
    ship: "A Venture — nothing fancy needed.",
    steps: [
      "Warp in.",
      "Huff at your leisure — no NPCs, no timer.",
      "Haul it home for Synth booster production.",
    ],
    safety: "K-space Mykoserocin sites have no NPCs and no timer — completely safe. Mine as long as you like.",
  };

  // gas_cyto
  return {
    modules: "Gas Cloud Scoops — plus a tank and/or drones for the unstable nebulae.",
    ship: "A Venture for the safe nebulae; a tanked ship for the ones with NPCs or damaging clouds.",
    steps: [
      "Check the specific nebula name first — hazards vary a lot between them.",
      "Safe nebulae (Emerald, Crimson, Bandit, Phoenix, Forgotten, Rapture): warp in and huff freely.",
      "Hostile nebulae: expect NPCs and/or clouds hitting ~1,000–1,400 per mining cycle — tank them or avoid.",
      "Huff and haul home for Improved booster production.",
    ],
    safety: "Most Cytoserocin clouds are unstable — several deal damage every mining cycle and some have NPCs on grid. Know your nebula before you commit.",
  };
}

// ── Shared reference: the hacking minigame + fitting primer ─────────────────
const EXP_PRIMER_HTML = `
  <div class="exp-primer-grid">
    <div class="exp-primer-sec">
      <h4>The hacking minigame</h4>
      <p>Goal: reduce the <b>System Core</b> to 0 Coherence. You lose if your Virus hits 0 Coherence or the timer runs out. Your Virus has <b>Coherence</b> (its health) and <b>Strength</b> (its damage); combat is turn-based — you hit first, the node hits back.</p>
      <ul>
        <li><b>Node colours:</b> grey = hidden, green = revealable (touching an explored node), orange = explored.</li>
        <li><b>Numbers</b> on empty nodes = distance (1–5) to the nearest Core, utility or Data Cache. Follow the low numbers.</li>
        <li>Explore <i>widely</i> before you pick fights — you may stumble onto the Core early and skip the defences entirely.</li>
        <li><b>Two failed hacks destroy the can</b> (drone sites are the exception — a fail there just spawns drones).</li>
      </ul>
    </div>
    <div class="exp-primer-sec">
      <h4>Defensive nodes — kill these</h4>
      <ul>
        <li><b>Firewall</b> — high Coherence, weak hit. A tanky wall.</li>
        <li><b>Anti-Virus</b> — low Coherence, hits hard. Dies fast.</li>
        <li><b>Restoration Node</b> — heals +20 Coherence to a random defence each turn. <b>Kill first.</b></li>
        <li><b>Virus Suppressor</b> — cuts your Strength by 15 (floor 10). <b>Kill first.</b></li>
      </ul>
      <h4>Utility nodes — grab &amp; use</h4>
      <ul>
        <li><b>Self Repair</b> — +5–10 Virus Coherence/turn for 3 turns.</li>
        <li><b>Kernel Rot</b> — halves a node's Coherence.</li>
        <li><b>Polymorphic Shield</b> — blocks the next 2 hits on you.</li>
        <li><b>Secondary Vector</b> — −20 Coherence/turn for 3 turns (great vs Suppressors).</li>
      </ul>
      <p>Grab utilities the moment they're exposed — a later defensive node can wall them off. Open Data Caches only as a last resort (50/50 to be good or bad).</p>
    </div>
    <div class="exp-primer-sec">
      <h4>Analyzers, rigs &amp; skills</h4>
      <ul>
        <li><b>Data Analyzer</b> opens data sites; <b>Relic Analyzer</b> opens relic sites — carry both. The <b>Integrated Analyzer</b> does both but with weaker stats and fewer utility slots.</li>
        <li><b>T1</b> = 40 Coherence / 20 Strength. <b>T2</b> = 60 / 30 (needs Hacking V / Archaeology V).</li>
        <li><b>Rigs:</b> Memetic Algorithm Bank (data) / Emission Scope Sharpener (relic) — +10 (T1) or +20 (T2) Coherence.</li>
        <li><b>Skills:</b> Hacking (data) &amp; Archaeology (relic) each give +10 Coherence/level. Astrometrics + support skills sharpen scanning.</li>
        <li>Exploration frigates add a <b>+5 virus-strength</b> role bonus. A <b>Cargo Scanner</b> skips junk cans; a <b>Mobile Depot</b> lets you refit in space.</li>
      </ul>
    </div>
    <div class="exp-primer-sec">
      <h4>Ship by danger</h4>
      <ul>
        <li><b>Highsec:</b> T1 exploration frigate (Heron / Imicus / Magnate / Probe).</li>
        <li><b>Lowsec:</b> T1 frigate, or an Astero / Covert Ops frigate to warp cloaked.</li>
        <li><b>Nullsec:</b> Covert Ops frigate or Astero — a Covert Ops Cloak is essential.</li>
        <li><b>Wormhole (scan/hack only):</b> Covert Ops frigate, Astero or Stratios.</li>
        <li><b>Wormhole combat (Sleepers):</b> battlecruiser (C1–2) → battleship / small gang (C3) → fleet (C4–6). T3 cruisers are prized.</li>
      </ul>
    </div>
  </div>`;

let EXP = { selected: null, recent: [] };

try { EXP.recent = JSON.parse(localStorage.getItem("exp-recent")) || []; } catch(e) { EXP.recent = []; }

function expSaveRecent(){
  try { localStorage.setItem("exp-recent", JSON.stringify(EXP.recent.slice(0,10))); } catch(e){}
  // Also push into the server-synced settings blob so recents follow the
  // logged-in character across browsers/devices (loadSettings restores them).
  if(typeof saveLS==="function") saveLS();
}

function expRenderRecent(){
  const list = $("#exp-recent-list");
  if(!EXP.recent.length){ list.innerHTML = '<div style="color:var(--dim);font-size:12px;padding:6px 10px">No recent lookups</div>'; return; }
  list.innerHTML = EXP.recent.map((name,i)=>{
    const site = EXP_SITES.find(s=> s.name===name);
    if(!site) return "";
    const dangerCls = site.danger==="safe"?"exp-badge-safe":site.danger==="caution"?"exp-badge-caution":"exp-badge-dangerous";
    return `<div class="exp-recent-item" data-idx="${i}"><span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(site.name)}</span><span class="exp-result-badge ${dangerCls}">${esc(site.danger)}</span><button class="exp-recent-rm" data-ri="${i}" title="Remove">×</button></div>`;
  }).join("");
  list.querySelectorAll(".exp-recent-item").forEach(el=>{
    el.onclick = (e)=>{ if(e.target.classList.contains("exp-recent-rm")) return; const site = EXP_SITES.find(s=>s.name===EXP.recent[+el.dataset.idx]); if(site) expSelect(site); };
  });
  list.querySelectorAll(".exp-recent-rm").forEach(btn=>{
    btn.onclick = (e)=>{ e.stopPropagation(); EXP.recent.splice(+btn.dataset.ri,1); expSaveRecent(); expRenderRecent(); };
  });
}

function expSearch(){
  const q = ($("#exp-search").value||"").trim().toLowerCase();
  const results = $("#exp-results");
  if(q.length < 2){ results.classList.add("hidden"); return; }
  const matches = EXP_SITES.filter(s=>{
    if(s.name.toLowerCase().includes(q)) return true;
    if(s.altNames && s.altNames.some(a=>a.toLowerCase().includes(q))) return true;
    return false;
  });
  if(!matches.length){
    results.innerHTML = `<div class="exp-result-item"><span class="exp-result-name" style="color:var(--dim)">No matching sites found</span></div>`;
    results.classList.remove("hidden");
    return;
  }
  results.innerHTML = matches.slice(0,15).map((s,i)=>{
    const dangerCls = s.danger==="safe"?"exp-badge-safe":s.danger==="caution"?"exp-badge-caution":"exp-badge-dangerous";
    return `<div class="exp-result-item" data-idx="${EXP_SITES.indexOf(s)}">
      <span class="exp-result-name">${esc(s.name)}</span>
      <span class="exp-type-badge">${esc(s.type.replace("_"," "))}</span>
      <span class="exp-result-badge ${dangerCls}">${esc(s.danger)}</span>
    </div>`;
  }).join("");
  results.classList.remove("hidden");
  results.querySelectorAll(".exp-result-item[data-idx]").forEach(el=>{
    el.onclick = ()=> expSelect(EXP_SITES[+el.dataset.idx]);
  });
}

function expSelect(site){
  EXP.selected = site;
  $("#exp-search").value = "";
  $("#exp-results").classList.add("hidden");
  $("#exp-empty").classList.add("hidden");
  $("#exp-cards").classList.remove("hidden");

  EXP.recent = [site.name, ...EXP.recent.filter(n=>n!==site.name)].slice(0,10);
  expSaveRecent();
  expRenderRecent();

  $("#exp-site-title").textContent = site.name;

  // Hero strip — key metrics at a glance
  const heroDangerCls = site.danger==="safe"?"exp-badge-safe":site.danger==="caution"?"exp-badge-caution":"exp-badge-dangerous";
  const heroTierCls = "exp-tier-"+(site.lootTier||"low");
  $("#exp-hero").innerHTML = `
    <div class="exp-hero-chip"><span class="exp-hero-label">Danger</span><span class="exp-hero-value"><span class="exp-danger-badge ${heroDangerCls}">${esc(site.danger)}</span></span></div>
    <div class="exp-hero-chip"><span class="exp-hero-label">Loot Tier</span><span class="exp-hero-value"><span class="exp-loot-tier ${heroTierCls}">${esc(site.lootTier||"low")}</span></span></div>
    <div class="exp-hero-chip"><span class="exp-hero-label">Est. Value</span><span class="exp-hero-value" style="color:var(--gold)">${esc(site.estimatedValue)}</span></div>
    <div class="exp-hero-chip"><span class="exp-hero-label">Space</span><span class="exp-hero-value">${esc(site.space.join(", "))}${site.whClass?" <span style='font-size:13px;color:var(--dim);font-weight:400'>C"+site.whClass.join(",")+"</span>":""}</span></div>`;

  // WHAT TO EXPECT card
  const dangerCls = site.danger==="safe"?"exp-badge-safe":site.danger==="caution"?"exp-badge-caution":"exp-badge-dangerous";
  let triggerHtml = `<div class="exp-label">DANGER LEVEL</div>
    <div class="exp-value"><span class="exp-danger-badge ${dangerCls}">${esc(site.danger)}</span></div>`;
  triggerHtml += `<div class="exp-label">TYPE</div><div class="exp-value">${esc(site.type.replace("_"," "))} site</div>`;
  triggerHtml += `<div class="exp-label">FOUND IN</div><div class="exp-value">${site.space.join(", ")}${site.whClass?" (C"+site.whClass.join(",")+")":""}</div>`;
  triggerHtml += `<div class="exp-label">TRIGGERS &amp; MECHANICS</div><div class="exp-value">${esc(site.triggers)}</div>`;
  if(site.tips) triggerHtml += `<div class="exp-label">TIPS</div><div class="exp-value">${esc(site.tips)}</div>`;
  $("#exp-trigger-body").innerHTML = triggerHtml;

  // NPCs card
  let npcHtml = "";
  if(!site.hasNPCs){
    npcHtml = `<div class="exp-value" style="color:var(--green);font-weight:600">No NPCs present</div>`;
  } else {
    npcHtml += `<div class="exp-label">NPC TYPE</div><div class="exp-value">${esc(site.npcType)}</div>`;
    if(site.npcDetail) npcHtml += `<div class="exp-label">DETAIL</div><div class="exp-value">${esc(site.npcDetail)}</div>`;
    const hazardLabels = {combat_npcs:"Combat NPCs on grid",exploding_cans:"Exploding containers",env_damage:"Environmental damage (clouds/turrets)",response_fleet:"Response fleet warps in",timed_spawn:"Timed delayed NPC spawn"};
    if(site.hazards.length) npcHtml += `<div class="exp-label">HAZARDS</div><div class="exp-value">${site.hazards.map(h=>esc(hazardLabels[h]||h)).join("<br>")}</div>`;
  }
  $("#exp-npc-body").innerHTML = npcHtml;

  // LOOT card
  const tierCls = "exp-tier-"+(site.lootTier||"low");
  let lootHtml = `<div class="exp-label">LOOT TIER</div>
    <div class="exp-value"><span class="exp-loot-tier ${tierCls}">${esc(site.lootTier||"low")}</span></div>`;
  lootHtml += `<div class="exp-label">WHAT DROPS</div><div class="exp-value">${esc(site.lootSummary)}</div>`;
  if(site.lootExamples&&site.lootExamples.length) lootHtml += `<div class="exp-label">EXAMPLES</div><div class="exp-value">${site.lootExamples.map(e=>esc(e)).join(", ")}</div>`;
  lootHtml += `<div class="exp-label">ESTIMATED VALUE</div><div class="exp-value">${esc(site.estimatedValue)}</div>`;
  $("#exp-loot-body").innerHTML = lootHtml;

  // HOW TO TACKLE IT card — the actionable playbook for this site.
  const t = expTackle(site);
  let tackleHtml = `<div class="exp-label">WHAT TO BRING</div><div class="exp-value">${esc(t.modules)}</div>`;
  tackleHtml += `<div class="exp-label">SHIP</div><div class="exp-value">${esc(t.ship)}</div>`;
  tackleHtml += `<div class="exp-label">STEP BY STEP</div><ol class="exp-steps">${t.steps.map(s=>`<li>${esc(s)}</li>`).join("")}</ol>`;
  if(t.note) tackleHtml += `<div class="exp-label">MINIGAME NOTE</div><div class="exp-value">${esc(t.note)}</div>`;
  tackleHtml += `<div class="exp-safety"><span class="exp-safety-tag">#1 RULE</span> ${esc(t.safety)}</div>`;
  $("#exp-tackle-body").innerHTML = tackleHtml;
}

function esc(s){ return s==null?"":String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;"); }

$("#exp-search").addEventListener("input", expSearch);
document.addEventListener("click", (e)=>{
  const sidebar = e.target.closest(".exp-sidebar");
  if(!sidebar){ $("#exp-results").classList.add("hidden"); }
});
expRenderRecent();

// Collapsible "how hacking works" primer — populated once, toggled open/closed.
(function(){
  const body = $("#exp-primer-body");
  const toggle = $("#exp-primer-toggle");
  if(!body || !toggle) return;
  body.innerHTML = EXP_PRIMER_HTML;
  const KEY = "exp-primer-open";
  const setOpen = (open)=>{
    body.classList.toggle("hidden", !open);
    toggle.classList.toggle("open", open);
    try { localStorage.setItem(KEY, open ? "1" : "0"); } catch(e){}
  };
  // Default open on first visit so newcomers see the mechanics.
  setOpen(localStorage.getItem(KEY) !== "0");
  toggle.onclick = ()=> setOpen(body.classList.contains("hidden"));
})();

// Sidebar resize handle
(function(){
  const handle = $("#exp-resize-handle");
  const layout = handle.parentElement;
  let dragging = false, startX, startW;
  const saved = localStorage.getItem("exp-sidebar-width");
  if(saved){ layout.style.gridTemplateColumns = saved + "px 6px 1fr"; }
  handle.addEventListener("mousedown", (e)=>{
    e.preventDefault();
    dragging = true;
    startX = e.clientX;
    startW = layout.querySelector(".exp-sidebar").offsetWidth;
    handle.classList.add("active");
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  });
  document.addEventListener("mousemove", (e)=>{
    if(!dragging) return;
    const w = Math.max(180, Math.min(600, startW + (e.clientX - startX)));
    layout.style.gridTemplateColumns = w + "px 6px 1fr";
  });
  document.addEventListener("mouseup", ()=>{
    if(!dragging) return;
    dragging = false;
    handle.classList.remove("active");
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
    const w = layout.querySelector(".exp-sidebar").offsetWidth;
    localStorage.setItem("exp-sidebar-width", w);
  });
})();
