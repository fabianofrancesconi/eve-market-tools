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
  {name:"Lesser Covert Research Facility",altNames:["Lesser Sansha Covert Research Facility","Lesser Blood Raider Covert Research Facility","Lesser Angel Covert Research Facility","Lesser Guristas Covert Research Facility","Lesser Serpentis Covert Research Facility"],type:"ghost",space:["highsec"],danger:"dangerous",hasNPCs:true,npcType:"Faction Response Fleet",npcDetail:"Response fleet warps in when timer expires. Warp disruption (24km range).",hazards:["exploding_cans","response_fleet"],triggers:"A hidden timer starts the MOMENT you land on grid (~30s-3min). When timer expires OR you fail a hack: ALL containers explode simultaneously dealing ~6000 explosive damage in 10km radius. Response fleet warps in with warp disruptors.",tips:"Fit ~13k explosive EHP so the blast can't one-shot you, then hack as many cans as you can. If one detonates, so be it — you tanked it.",lootTier:"high",lootSummary:"Implant BPCs (Ascendancy set), Covert Research Tools",lootExamples:["Mid-grade Ascendancy Alpha BPC","Covert Research Tools","Shattered Villard Wheel"],estimatedValue:"10-100M"},
  {name:"Standard Covert Research Facility",altNames:["Standard Sansha Covert Research Facility","Standard Blood Raider Covert Research Facility","Standard Angel Covert Research Facility","Standard Guristas Covert Research Facility","Standard Serpentis Covert Research Facility"],type:"ghost",space:["lowsec"],danger:"dangerous",hasNPCs:true,npcType:"Faction Response Fleet",npcDetail:"Stronger response fleet with warp disruption.",hazards:["exploding_cans","response_fleet"],triggers:"Same timer mechanic as Lesser but explosions deal ~8000 explosive damage (10km radius). Timer starts on grid landing.",tips:"Fit explosive hardeners and tank the blast — then hack aggressively. A poorly-tanked cruiser dies; a tanked one grabs several cans.",lootTier:"high",lootSummary:"Wetu/Packrat mobile depot BPCs, mid/high-grade Ascendancy BPCs",lootExamples:["Wetu Mobile Depot BPC","High-grade Ascendancy Alpha BPC","Covert Research Tools"],estimatedValue:"30-200M"},
  {name:"Improved Covert Research Facility",altNames:["Improved Sansha Covert Research Facility","Improved Blood Raider Covert Research Facility","Improved Angel Covert Research Facility","Improved Guristas Covert Research Facility","Improved Serpentis Covert Research Facility"],type:"ghost",space:["nullsec"],danger:"dangerous",hasNPCs:true,npcType:"Faction Response Fleet",npcDetail:"Strong response fleet with warp disruption.",hazards:["exploding_cans","response_fleet"],triggers:"~10,000 explosive damage on explosion (10km radius). Hidden timer from landing. Enough to kill most cruisers outright.",tips:"Must have an explosive tank — then hack everything you can. Jackpot loot easily justifies risking a cheap hull. Grab, don't tiptoe.",lootTier:"jackpot",lootSummary:"Wetu/Yurt/Magpie BPCs, high-grade Ascendancy BPCs",lootExamples:["Yurt Mobile Depot BPC","High-grade Ascendancy Omega BPC","Covert Research Tools"],estimatedValue:"50-500M"},
  {name:"Superior Covert Research Facility",altNames:["Superior Sansha Covert Research Facility","Superior Blood Raider Covert Research Facility","Superior Angel Covert Research Facility","Superior Guristas Covert Research Facility","Superior Serpentis Covert Research Facility"],type:"ghost",space:["wormhole"],danger:"dangerous",hasNPCs:true,npcType:"Sleeper Response Fleet",npcDetail:"Sleeper response fleet (deadlier than faction). Warp disruption.",hazards:["exploding_cans","response_fleet"],triggers:"~12,000 explosive damage on explosion (10km radius). Sleeper response fleet instead of faction NPCs. Same timer mechanic.",tips:"Tank the ~12k explosive blast, then hack as many cans as you can. The Sleeper fleet is deadly — stay clear of its point, but don't stop hacking out of fear of the blast.",lootTier:"jackpot",lootSummary:"Magpie/Wetu/Yurt BPCs, high-grade Ascendancy Omega BPC",lootExamples:["Magpie Mobile Depot BPC","High-grade Ascendancy Omega BPC","Covert Research Tools"],estimatedValue:"80-800M"},
  // ── Sleeper Caches ──────────────────────────────────────────────────────
  {name:"Limited Sleeper Cache",type:"sleeper_cache",space:["highsec","lowsec","nullsec","wormhole"],danger:"caution",hasNPCs:false,npcType:null,npcDetail:null,hazards:["env_damage"],triggers:"Entrance hack (7/10 Data): a FAILED entrance hack starts a 2-minute timer that despawns the site (the storage depots themselves do NOT self-destruct on failed hacks). No sentry turrets at this tier. Pressure/wreck cloud deals ~100 DPS (need 60+ EHP/s). Plasma chambers act as proximity mines: ~500 damage + a damage cloud, ~9km blast (slower approach = safer).",tips:"Frigates only (no stealth bombers). Both analyzers needed. Hack the RPCU (7/10) to clear the wreck cloud, or the RDGU (8/10) to drop the forcefield. Move slow (~200 m/s) near plasma chambers.",lootTier:"medium",lootSummary:"Polarized weapon BPCs, sleeper blue loot, datacores",lootExamples:["Polarized Torpedo Launcher BPC","Wrecked Components","Datacores"],estimatedValue:"10-60M"},
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
    return "Covert Ops frigate or Astero. Cloak is non-negotiable.";
  if(sp.includes("lowsec"))
    return "T1 frigate — or an Astero to warp cloaked.";
  return "Cheap T1 frigate: Heron, Imicus, Magnate, Probe. It's disposable.";
}

function expWhShip(whClass){
  const c = Math.min(...(whClass && whClass.length ? whClass : [1]));
  if(c<=2)   return "C1–2: one T2 battlecruiser, solo.";
  if(c===3)  return "C3: a battleship, or a small cruiser gang + logi.";
  if(c===4)  return "C4: 5–6 battleships.";
  return "C5–6: a real fleet with logi + EWAR. Caps may drop in.";
}

// ══════════════════════════════════════════════════════════════════════════
// "FULL WALKTHROUGH" — the deep, per-site-type guide behind the 📖 button.
//
// One structured guide per playbook id (data/relic safe, drone data, w-space
// combat, the 4 ghost tiers, the 3 sleeper caches, and the 3 gas categories).
// Every number below is "per the EVE University wiki" — where the wiki gives
// no figure we say so rather than invent one. Content is trusted static HTML
// (site names come from EXP_SITES), so the renderer injects it un-escaped like
// EXP_PRIMER_HTML does.
// ══════════════════════════════════════════════════════════════════════════

// Per-tier ghost specifics (rats + one-blast damage + loot) so the shared ghost
// guide reads correctly for Lesser / Standard / Improved / Superior.
const GHOST_WALK = {
  ghost_lesser:   {space:"highsec",  rats:"faction pirate", dmg:"6,000",  loot:"Mid-grade Ascendancy implant BPCs, Covert Research Tools, and Shattered Villard Wheels (an Ascendancy component). Note the 5th/top can is <b>always empty in highsec</b>."},
  ghost_standard: {space:"lowsec",   rats:"faction pirate", dmg:"8,000",  loot:"'Wetu' Mobile Depot &amp; 'Packrat' MTU BPCs, high-grade Ascendancy Alpha/Beta plus mid-grade BPCs, and Covert Research Tools. The top can reliably drops an Electro-Neural Signaller."},
  ghost_improved: {space:"nullsec",  rats:"faction pirate", dmg:"10,000", loot:"'Wetu'/'Yurt' depot &amp; 'Magpie' MTU BPCs, high-grade Ascendancy BPCs, Covert Research Tools, and capital-module materials."},
  ghost_superior: {space:"wormhole", rats:"Sleeper",        dmg:"12,000", loot:"'Wetu'/'Yurt'/'Magpie' depot BPCs, high-grade Ascendancy Delta/Epsilon/Gamma/Omega BPCs, Covert Research Tools, and capital-module materials."},
};

function expWalkthrough(site){
  const pid = expPlaybookId(site);

  if(pid==="data_safe" || pid==="relic_safe"){
    const isData = site.type==="data";
    return {
      overview:`An ordinary <b>${isData?"data":"relic"} site</b> — pure hacking, <b>no NPCs and no triggers</b>. Scan it, crack the cans, scoop the spew, move on. The only real threat is other players once you leave highsec.`,
      entry:`Scan the signature to 100% with core probes and warp straight to 0 — it's safe to land on top of. Approach a container until your ${isData?"Data":"Relic"} Analyzer is in range, then activate it to open the hacking minigame.`,
      hazards:[
        "<b>No rats, no timers, no explosions.</b> Nothing on grid will hurt you.",
        "<b>Failing the same can twice destroys its loot</b> — the only in-site penalty. Closing the minigame window also counts as a fail, so weigh it before committing.",
        "In low/null/wormhole space the danger is <b>other capsuleers</b>: watch D-scan and local, and bail the moment combat probes appear.",
      ],
      ship:`A cheap <b>T1 exploration frigate</b> (Heron, Magnate, Imicus, Probe) is fine in highsec — it's disposable. In low/nullsec fly a <b>Covert Ops frigate or Astero</b> so you can warp cloaked. Fit the matching analyzer (<b>${isData?"Data":"Relic"} Analyzer</b>; carry both and go T2 in null) and a coherence rig (${isData?"Memetic Algorithm Bank":"Emission Scope Sharpener"}) if you can.`,
      steps:[
        "Scan to 100%, warp to the site.",
        "Optionally cargo-scan the cans to skip the junk ones.",
        "Approach a can and open the minigame.",
        "Kill <b>Restoration Nodes</b> and <b>Virus Suppressors</b> first; grab utility subsystems (Kernel Rot, Secondary Vector) to burn down the System Core.",
        "Reduce the Core to 0 — the can spews loot; fly through it to scoop.",
        "Repeat for the worthwhile cans, then warp off (emptied sites despawn quickly).",
      ],
      loot: isData
        ? "Datacores, decryptors, invention &amp; faction-module BPCs, and Abyssal filaments. Highsec pays a few hundred k to a few million; nullsec and wormhole data pays more."
        : "T1/T2 salvage materials (Intact Armor Plates and the like) and rig BPCs. Highsec is low value; nullsec and wormhole 'Ruined' relic sites are the lucrative ones.",
      rule:"Never fail the same container twice — a second failure destroys the loot inside. If a board looks unwinnable, retreat rather than force the last node.",
    };
  }

  if(pid==="data_drone"){
    return {
      overview:"A <b>drone-region data site</b> (the Abandoned Research Complex family), found only in the eight drone regions. It hacks like a normal data site, but a <b>failed hack can spawn hostile rogue-drone frigates</b> instead of wrecking the can.",
      entry:"Scan and warp in as usual — these are nullsec drone-space signatures with no gate or timer.",
      hazards:[
        "<b>Failed hack → chance to spawn rogue-drone frigates.</b> They're weak and easily killed; the wiki lists no DPS figure.",
        "Crucially, <b>a failed hack here does NOT destroy the can</b> — it's fully recoverable, unlike normal sites.",
        "The real killer is <b>nullsec PvP</b>, not the drones.",
      ],
      ship:"A T1 exploration frigate or Astero, but <b>bring a flight of light combat drones</b> (Hobgoblins/Warriors) to clear the frigate spawns. Data Analyzer + coherence rig.",
      steps:[
        "Warp in and open the containers.",
        "If a hack fails and drones spawn, launch your drones, pop them, and carry on hacking.",
        "<b>Hack the lone Research &amp; Development Laboratories can too</b>, even though it's often empty — skipping it forfeits the escalation roll.",
        "Loot and move on; the site can escalate into another drone data site.",
      ],
      loot:"Rogue-drone component BPCs (Integrated/Augmented drone blueprints; the racial pool varies by region) and datacores. The wiki lists no ISK total.",
      rule:"Bring drones and hack every can — including the empty-looking R&amp;D one. The fail penalty is a trivial spawn, not lost loot, and skipping the research can throws away the escalation chance.",
    };
  }

  if(pid==="wh_combat"){
    const isData = site.type==="data";
    return {
      overview:`A <b>wormhole ${isData?"data":"relic"} site guarded by Sleepers</b>. It shows on probes as a normal ${isData?"data":"relic"} signature, but Sleeper battleships/cruisers sit on grid and <b>aggress the instant you land</b>. It's a combat site first and a hack second, and it scales hard from C1 to C6.`,
      entry:"Scan the signature and confirm it's the Sleeper-guarded version, not a tame no-rat one. Warp in with a <b>combat ship</b>, never a fragile hacking frigate — you'll be shot immediately.",
      hazards:[
        "<b>Sleepers have infinite cap</b> — neuts/nos are wasted on them. But <b>they neut and web you</b> (out to ~40 km from cruisers, ~20 km from frigates) and <b>warp-scramble you in C3 and above</b>.",
        "They <b>remote-repair each other</b> in higher classes — <b>ECM (jamming)</b> is the reliable counter.",
        "They <b>switch targets by threat</b>: drones, ECM and damps pull aggro onto you.",
        "<b>No local chat in wormholes</b> — D-scan is your only warning that someone's coming.",
      ],
      ship:expWhShip(site.whClass)+" <b>Omni-tank all four damage types.</b> Bring ECM / sensor damps (they work; neuts don't) and salvagers for the wrecks.",
      steps:[
        "Warp in with a combat ship and expect immediate aggression.",
        "Clear the waves, killing the <b>trigger ship</b> of each to advance in a controlled order; prioritise anything neuting/webbing/scramming you.",
        "Only once the grid is clear, <b>hack</b> the artifact/databank cans.",
        "<b>Salvage the Sleeper wrecks</b> — the ancient salvage is where most of the ISK is.",
        "Watch D-scan throughout and leave via your known exit hole.",
      ],
      loot:"Guaranteed <b>blue loot</b> (Sleeper components sold to NPCs): ~200k–5M ISK per item; a C1 site totals ~10–13M, a C5 relic site ~279M. Salvage (ancient materials) and the relic/data hacks feed <b>Tech 3</b> production — the real payout.",
      rule:"Clear every Sleeper before you hack — never sit still hacking with rats alive. No local means constant D-scan, an escape bookmark, and never biting off a class above your ship or fleet.",
    };
  }

  if(pid.indexOf("ghost")===0){
    const g = GHOST_WALK[pid];
    return {
      overview:`A <b>Ghost Site</b> (in-game: Covert Research Facility) in ${g.space}. It scans as a data site, but it's a <b>smash-and-grab</b>: a hidden timer is ticking, and when it runs out the whole site explodes and a response fleet lands.`,
      entry:"Scan it down and <b>warp in cloaked</b> — the timer doesn't start until you decloak, so cloaking buys you setup time. You'll only ever see the final 30-second countdown.",
      hazards:[
        "<b>Hidden timer</b> (~30 s to 3 min from decloak). Only the last 30 s is shown.",
        `<b>Everything detonates at once when the timer ends</b> — including cans you already hacked — for ~<b>${g.dmg}</b> explosive damage in a <b>10 km</b> radius.`,
        "A <b>failed hack</b> only blows that one can (you can keep going) — but warping mid-hack auto-fails it.",
        `A <b>${g.rats} response fleet</b> warps in as the visible timer starts, with warp disruption out to <b>24 km</b>; it won't aggress if you're <b>30+ km</b> away.`,
      ],
      ship:`The one thing that matters: <b>tank the blast</b>. Fit ~<b>13,000 explosive EHP</b> (an explosive shield hardener + extender) so a detonation can't one-shot you — then you can keep hacking through it. A cheap Heron Navy / Buzzard / Helios works, or face-tank in a Stratios/T3C. <b>T2 Data Analyzer</b> for fast, reliable hacks.`,
      steps:[
        "Warp in <b>cloaked</b> to hold the timer, then decloak on the cans.",
        "Cargo-scan and <b>hack the best cans first</b> — go for as many as you can, don't stop at one.",
        "Keep your explosive hardener <b>hot</b> the whole time.",
        "When the timer/rats hit: if you're tanked, <b>eat the blast and keep grabbing</b>; only bail if a second blast would kill you or the rats point you.",
        "If it blows before you're done — <b>so be it</b>. You tanked it; scoop what spewed and move on. Losing a cheap hull to a jackpot can is a good trade.",
      ],
      loot:g.loot,
      rule:`<b>Tank it, don't tiptoe.</b> With enough explosive EHP a ~${g.dmg} blast won't kill you, so hack aggressively and grab everything you can — if a can blows, that's fine. The only real "don't" is sitting there <b>untanked</b> or letting the ${g.rats} fleet point you while you're not clear.`,
    };
  }

  if(pid==="cache_limited") return {
    overview:"The <b>easiest Sleeper Cache</b> — a frigate-only hazard course with <b>no NPCs</b>; everything that kills you is environmental. Found across all space.",
    entry:"Scan to 100% (needs ~<b>80.9</b> probe strength). <b>Frigates only</b>, and <b>stealth bombers can't enter</b> (they can't use the rift). Bring <b>both</b> analyzers. Hack the <b>Hyperfluct Generator (7/10 Data)</b> to open the rift; fail it and a <b>2-minute</b> timer despawns the site.",
    hazards:[
      "<b>Wreck / pressure cloud</b>: ~<b>100 DPS</b> to unmodified T1 resists, ~12 km radius. You need ≥<b>60 EHP/s</b> to sit in it.",
      "<b>Unstable Plasma Chambers (×2)</b>: proximity mines — ~<b>500</b> initial hit plus a damage cloud, ~9 km blast. <b>Slower is safer</b>: they rupture ~7 km out at 330 m/s but let you nearly touch at ~100 m/s.",
      "<b>EM forcefield</b> around one depot: a ~14 km bubble that regenerates 150 hp/s.",
      "<b>No sentry towers</b> at this tier, and the storage depots <b>don't self-destruct</b> on failed hacks.",
    ],
    ship:"A <b>Heron</b> (shield) or <b>Magnate/Astero</b> (armour), cap-stable with ≥60 EHP/s in the cloud. Both analyzers, a <b>Mobile Depot</b> to refit, and an afterburner (not MWD near the mines).",
    steps:[
      "Hack the Hyperfluct Generator (7/10) and take the rift.",
      "Drop a mobile depot and cargo-scan all six cans.",
      "For the depot in the wreck cloud: hack the <b>RPCU (7/10 Data)</b> to clear the cloud for 2–3 min (a fail is only ~200 dmg), or refit to a local repper and tank it.",
      "For the forcefield depot: hack the <b>RDGU (8/10 Data)</b> to drop the field (a fail can hit ~1,225 dmg), or just MWD straight in before the bubble activates.",
      "Move ~200 m/s around the plasma chambers, hack the worthwhile cans, refit, scoop the depot, and leave.",
    ],
    loot:"The two <b>Dented</b> depots (the one inside the wreck and the one beside it) hold the value; Mangled depots hold Sleeper materials. Least lucrative of the three caches, but low effort — very roughly 10–60M.",
    rule:"Never drift into the wreck cloud or a plasma chamber's ~9 km blast without either clearing it (RPCU) or holding ≥60 EHP/s — carelessness here is an instant loss.",
  };

  if(pid==="cache_superior") return {
    overview:"The <b>hardest PvE site in EVE</b> — a four-room gauntlet (Solray → Mine → Turret → Archive). Needs both analyzers and a seriously tanked ship; a frigate can only do the lighter rooms.",
    entry:"Scan to 100% (needs ~<b>104</b> probe strength, pinpoint). All ships except capitals may enter (the Rorqual excepted). Hack the <b>Hyperfluct Generator (7/10 Data)</b>; failing gives a 2-min despawn. <b>Warp distance tells you the room</b>: ~31,000 km = Solray, 60,000+ km = the turret room.",
    hazards:[
      "<b>Solray solar flare</b> (EM): <b>600 DPS</b> unaligned, dropping to ~25 once you place the alignment disc. ~14 km radius.",
      "<b>Mine room</b>: bring <b>70,000 EHP</b> — entry mines hit ~800–2,100 and a full field logged ~42,000 damage.",
      "<b>Plasma Chambers</b>: <b>100,000</b> explosive damage over a ~<b>250 km</b> blast — never aggress one except in the scripted Archive move.",
      "<b>Turret room</b>: up to <b>32 sentry towers</b>; a tripped room is instant death.",
      "<b>Archive shockwaves</b>: up to <b>1,667 DPS</b> (56 km) — orbit the Cerebrum at 60 km to dodge them.",
    ],
    ship:"A tanked <b>T3 Strategic Cruiser or battleship</b>: ≥<b>2,000 EHP/s</b> active reps on a 50k+ EHP buffer for the Archive; ~900–1,000 EHP/s (EM/thermal) for the turret room; 70k EHP for the mine room. A frigate can only do Solray and lighter bits. Mobile depot to refit per room.",
    steps:[
      "Always run <b>Solray first</b>: hack the Observational Unit, place the alignment disc to drop the flare from 600 → ~25 DPS, then loot.",
      "<b>Mine room</b> (skip under 70k EHP): hack the Reroute Unit, then the hidden RDGU (10/10 — reveals the mines and spawns a Pristine depot), grab the cans, MJD/warp out.",
      "<b>Turret room</b>: hack the RDGU at range, then the Sentry Repair Station; wait for the towers to die before looting. <b>Never</b> shoot a tower or a Plasma Chamber.",
      "<b>Archive</b> (only with a 2,000 EHP/s T3C/BS): work the Vessel Rejuvenation Batteries and Cerebrum chambers, place the 3 Oscillation Fluids, then loot while orbiting the Cerebrum at 60 km.",
    ],
    loot:"<b>Isotropic Deposition Guides</b> (valuable, 5 m³ each — carry a mobile depot), plus Pristine-depot loot from the mine/turret/archive rooms. The turret room is the most lucrative part for a frigate pilot.",
    rule:"Never aggress a Plasma Chamber (100,000 dmg, ~250 km) or enter a tripped/alarmed room outside the one scripted Archive detonation — and only attempt the Archive in a T3C/battleship doing 2,000+ EHP/s.",
  };

  if(pid==="cache_standard") return {
    overview:"The middle Sleeper Cache — mechanically like the Limited, but this is where <b>sentry towers and an alarm/gas-cloud system</b> appear. Pays better than the Limited, and still doable in a T1 frigate.",
    entry:"Scan to 100% (needs ~<b>92</b> probe strength). Battleships-and-below can enter (not frigate-only). Bring both analyzers. Hack the <b>Hyperfluct Generator (7/10 Data)</b>; failing gives a 2-min despawn.",
    hazards:[
      "<b>Sentry Towers</b> (Restless/Vigilant/Wakeful): fire every <b>15 s</b>, reach <b>250 km</b>, and hit a frigate for ~<b>1,000</b> per shot at range (EM/thermal). Kill them with <b>light</b> drones (their 50 m signature dodges medium drones).",
      "<b>Alarm level (0→5)</b>: tripping it — or failing control-unit hacks — spawns <b>gas clouds</b> starting ~160 DPS and expanding to ~<b>354–1,000 DPS</b>. ~<b>1,200 EHP/s</b> tanks them indefinitely.",
      "Storage depots have <b>no fail penalty</b>; control-unit fails raise the alarm.",
      "<b>Hidden-room self-destruct</b>: 180 s (60 s if you fail the reset). Container explosions do no damage.",
    ],
    ship:"Heron/Magnate/Astero, or a dual-rep <b>Stratios</b> (≥250 EHP/s tanks two towers). Carry two fits swapped at a mobile depot: a <b>tower-fight fit</b> (AB, EM/thermal hardeners, light drones) and a <b>loot fit</b> (MWD, analyzers, cargo scanner) used only after the towers are down.",
    steps:[
      "Hack the Hyperfluct (7/10), take the rift, drop a depot.",
      "Hack the Logistic RDGU and the three <b>Coordinate Plotting Devices (7/10)</b>; load the coordinates into the Calibration Device.",
      "Refit to the tower fit, take the rift to the back room, and <b>destroy the sentry towers</b> with light drones.",
      "Refit to the loot fit and hack the storage depots; in the hidden room, manage the 180 s self-destruct (reset once if needed).",
      "Cargo-scan and prioritise <b>Pristine/Intact</b> depots, loot, and warp out.",
    ],
    loot:"Standard relic/data loot from the depots; the <b>Pristine and Intact</b> ones have the best odds, so cargo-scan and hit those first. Pays better than the Limited cache.",
    rule:"Don't run with the loot fit or trip the alarm until the sentry towers are dead — they hit ~1,000 per shot out to 250 km and the alarm spawns gas clouds. Manage the alarm level.",
  };

  if(pid==="gas_wh") return {
    overview:"A <b>wormhole gas (Fullerite) site</b> — empty and safe when you arrive, but <b>Sleepers spawn on a delay</b> and will delete a mining frigate. The gas feeds Tech 3 and capital production.",
    entry:"Scan the signature and warp in <b>at range</b> so you stay mobile. The C320/C540 'Core' sites (Instrumental/Vital Reservoir) only appear in C5/C6 or shattered holes.",
    hazards:[
      "<b>Delayed Sleeper spawn</b>: the wiki says ~<b>15–20 minutes</b> after the site is first entered. It does <b>not</b> confirm whether that timer is per-site or per-pilot — so treat 15 min as a hard ceiling and leave earlier.",
      "When they spawn they hit instantly: a Barren Reservoir fields ~82 DPS + neut; an <b>Instrumental Core Reservoir throws ~1,864 DPS</b> with web + scram + heavy neut.",
      "<b>No local chat</b> — other players are the bigger danger; keep D-scan up.",
    ],
    ship:"A <b>Venture</b> (gas bonus + built-in warp-core stab, cheap) or a <b>Prospect</b> (covert cloak, bigger hold). Fit <b>Gas Cloud Scoops</b>. Align speed and a cloak matter far more than tank.",
    steps:[
      "Warp in at range and <b>start a timer the moment you land</b>.",
      "Huff the cloud while staying <b>aligned to a safe</b> (exit hole, POS, or celestial).",
      "Keep D-scan running the whole time.",
      "<b>Warp out well before ~15 minutes</b> (aim ~10 min solo). Come back later for the rest — the gas stays, but spawned Sleepers don't leave.",
    ],
    loot:"Fullerite-C320/C540 (Instrumental/Vital) are the high-value gases; the lower Perimeter/Frontier reservoirs are cheaper. A full Core site is 10+ hours of huffing, so you realistically ninja a fraction per visit.",
    rule:"The site is empty now but not for long — leave before ~15 minutes. Never let greed hold you past the spawn, especially in a Core site where the Sleepers scram, web, and do ~1,864 DPS.",
  };

  if(pid==="gas_myko") return {
    overview:"A <b>k-space Mykoserocin nebula</b> (highsec/lowsec), refined for <b>Synth boosters</b>. Completely safe — <b>no NPCs and no damage clouds</b>. This is the beginner gas.",
    entry:"Scan it down (a cosmic signature) in a region that carries the colour you want. No gate, no deadspace.",
    hazards:[
      "<b>Nothing from the environment</b> — no rats, no cloud damage, no spawn timer.",
      "Your only risk is <b>other players</b> (or a highsec suicide gank). Gas is bulky at <b>10 m³/unit</b>, so the hold fills fast.",
    ],
    ship:"A <b>Venture</b> is ideal (gas bonus, warp-core stab, cheap). One or two Gas Cloud Scoops. No environmental tank needed.",
    steps:[
      "Find the nebula for your colour/booster and scan it down.",
      "Warp in, approach, and huff — no need to align for NPCs.",
      "Watch local/D-scan in lowsec for hostile players.",
      "Fill the hold (small nebula 2,000 units, large 6,000), dock, repeat.",
    ],
    loot:"2,000 (small) or 6,000 (large) units of the colour's Mykoserocin, for Synth booster production. Low per-unit ISK — volume and market proximity matter.",
    rule:"These clouds are genuinely safe — the only threat is another capsuleer, so watch local, not your capacitor.",
  };

  // gas_cyto — the default gas fallthrough
  return {
    overview:"A <b>k-space Cytoserocin nebula</b> (lowsec/nullsec), refined for the stronger <b>Improved boosters</b>. Unlike Mykoserocin, <b>most Cytoserocin clouds are unstable</b> — many carry rats and/or damage that hits at the end of each harvest cycle.",
    entry:"Each colour is region/constellation-locked. Scan the nebula down and check its name against the hazard list before you commit.",
    hazards:[
      "<b>Safe lowsec 'entry' nebulae</b> (no damage, no rats): Emerald, Crimson, Bandit, Profiteer, Phoenix, Forgotten, Rapture. Learn here.",
      "<b>Nullsec clouds mostly do 1,000 EM + 1,000 thermal per cycle</b>; the standout killer is <b>Glistening (6,000 EM + 1,000 thermal)</b>. Damage lands at cycle-end, not continuously.",
      "The richest 14,000–18,000-unit sites (Leopard, Shimmering, Hazy, Gaseous, Polar Bear, Red Dragonfly) <b>all have NPCs</b>.",
    ],
    ship:"A <b>Venture or Prospect</b> with Gas Cloud Scoops. For damage clouds, <b>fit resists to the listed type</b> (usually EM/thermal; thermal/explosive for Leopard/Shimmering). For rat sites, clear them first or huff aligned and warp when they land — a cloaky Prospect is best in null.",
    steps:[
      "Pick your colour and go to its region/constellation.",
      "<b>Start on a safe lowsec nebula</b> to learn the mechanic.",
      "For null damage sites: pre-fit resists, warp in, orbit in scoop range, and watch shield/cap each cycle.",
      "If the site has rats, clear them first or huff aligned with D-scan up.",
      "On the big 18,000-unit sites, take a chunk and reset rather than solo-huffing it all.",
    ],
    loot:"Lowsec safe sites hold ~500–1,400 units; null constellation sites 3,000–6,000, and the guarded 'big' ones up to 18,000 (10 m³ each). Sells for more than Mykoserocin, but the rich sites are all in dangerous null.",
    rule:"Check the specific nebula's damage type and NPC status BEFORE you warp in, and match your tank — many clouds bite for 1,000+ per cycle (Glistening 6,000 EM), and the best sites all have rats.",
  };
}

// ── Shared reference: the hacking minigame as a board-game rulebook ─────────
// Structured like a tabletop rules sheet — Objective → Turn order → the Board →
// the Pieces (foes to kill, tiles to grab) → Setup → Finding more sites — so a
// newcomer can read it top-to-bottom once and know how to play.
const EXP_PRIMER_HTML = `
  <div class="exp-rulebook">

    <div class="exp-rule-obj">
      <span class="exp-rule-obj-icon">🎯</span>
      <div>
        <div class="exp-rule-obj-t">Objective</div>
        <div class="exp-rule-obj-d">Destroy the <b>System Core</b> before your Virus dies or the timer runs out. Crack the can, grab the loot.</div>
      </div>
    </div>

    <div class="exp-rule-step">
      <div class="exp-rule-num">1</div>
      <div class="exp-rule-body">
        <div class="exp-rule-h">Bring your kit</div>
        <div class="exp-rule-p"><b>Data</b> Analyzer for data sites, <b>Relic</b> Analyzer for relic — carry both. Your Virus has two stats: <b class="exp-coh">Coherence</b> (its HP) and <b class="exp-str">Strength</b> (damage per hit). T1 analyzer = 40 / 20, T2 = 60 / 30 (needs Hacking &amp; Archaeology&nbsp;V). Rigs (Memetic&nbsp;/&nbsp;Emission&nbsp;Scope) and skills add more.</div>
      </div>
    </div>

    <div class="exp-rule-step">
      <div class="exp-rule-num">2</div>
      <div class="exp-rule-body">
        <div class="exp-rule-h">Read the board</div>
        <div class="exp-rule-p">A web of nodes. Only tiles next to explored ground are clickable.</div>
        <div class="exp-legend">
          <span class="exp-tile exp-tile-hidden">?</span><span class="exp-legend-l">Grey — hidden, not yet reachable</span>
          <span class="exp-tile exp-tile-open">＋</span><span class="exp-legend-l">Green — clickable now</span>
          <span class="exp-tile exp-tile-done">✓</span><span class="exp-legend-l">Orange — already explored</span>
          <span class="exp-tile exp-tile-num">3</span><span class="exp-legend-l">Number — steps to the nearest Core / tile</span>
        </div>
      </div>
    </div>

    <div class="exp-rule-step">
      <div class="exp-rule-num">3</div>
      <div class="exp-rule-body">
        <div class="exp-rule-h">Explore, then fight</div>
        <div class="exp-rule-p">Click outward to reveal the map — chase the <b>low numbers</b>, they point at the Core. You may trip over it early. When you hit a node, <b>you strike first</b>, then it strikes back — so soften tough nodes with power-ups before trading blows.</div>
      </div>
    </div>

    <div class="exp-rule-step">
      <div class="exp-rule-num">4</div>
      <div class="exp-rule-body">
        <div class="exp-rule-h">Win — or reset</div>
        <div class="exp-rule-p">Kill the Core and the can opens. Lose the Virus <b>twice</b> and the can self-destructs (drone sites are forgiving). Two attempts, that's it.</div>
      </div>
    </div>

    <div class="exp-pieces-block">
      <div class="exp-pieces-title exp-pieces-foe">☠ Enemy nodes — clear the path</div>
      <div class="exp-pieces">
        <div class="exp-piece exp-piece-foe"><span class="exp-piece-name">Firewall</span><span class="exp-piece-role">Tanky wall · weak hit</span></div>
        <div class="exp-piece exp-piece-foe"><span class="exp-piece-name">Anti-Virus</span><span class="exp-piece-role">Glass cannon · dies fast</span></div>
        <div class="exp-piece exp-piece-foe exp-piece-priority"><span class="exp-piece-name">Restoration Node</span><span class="exp-piece-role">Heals foes · KILL FIRST</span></div>
        <div class="exp-piece exp-piece-foe exp-piece-priority"><span class="exp-piece-name">Virus Suppressor</span><span class="exp-piece-role">Saps Strength · KILL FIRST</span></div>
      </div>
      <div class="exp-pieces-title exp-pieces-buff">✦ Power-up tiles — grab on sight</div>
      <div class="exp-pieces">
        <div class="exp-piece exp-piece-buff"><span class="exp-piece-name">Self Repair</span><span class="exp-piece-role">+5–10 HP/turn ×3</span></div>
        <div class="exp-piece exp-piece-buff"><span class="exp-piece-name">Kernel Rot</span><span class="exp-piece-role">Halves a node's HP</span></div>
        <div class="exp-piece exp-piece-buff"><span class="exp-piece-name">Polymorphic Shield</span><span class="exp-piece-role">Blocks the next 2 hits</span></div>
        <div class="exp-piece exp-piece-buff"><span class="exp-piece-name">Secondary Vector</span><span class="exp-piece-role">−20 HP/turn ×3 to a node</span></div>
        <div class="exp-piece exp-piece-wild"><span class="exp-piece-name">Data Cache</span><span class="exp-piece-role">50/50 gift or trap · last resort</span></div>
      </div>
    </div>

    <div class="exp-escalation">
      <div class="exp-escalation-head">
        <span class="exp-escalation-icon">⚡</span>
        <div>
          <div class="exp-escalation-t">Special event — Escalations</div>
          <div class="exp-escalation-d">Now and then, finishing a site fires a <b>pop-up notification</b> saying you've found the location of another site. This is an <b>Escalation</b> (a.k.a. Expedition) — a bonus pocket that <b>only you can see and warp to</b>. Nobody else can scan it down. Best of all it's <b>more hacking, not combat</b>: no rats, no defenders — just more cans. It's a small, unpublished chance — not every site can do it: in practice it's the sov-nullsec <b>Detected</b> data &amp; relic sites (→ <b>Interrupted Expedition</b> / <b>Emergent Ruins</b>) and drone-region data sites. Ordinary high/lowsec data &amp; relic sites don't escalate.</div>
        </div>
      </div>
      <div class="exp-escalation-do">
        <div class="exp-escalation-do-h">What to do</div>
        <ol>
          <li><b>Grab the bookmark before you leave.</b> The site is logged under <b>Journal → Expeditions</b> (and The Agency in the NeoCom). It <b>expires</b> — act within the hour, don't sit on it.</li>
          <li><b>Same ship works — it's pure hacking.</b> No rats to fight, so your exploration frigate is fine. Just keep the matching analyzer fitted (data for Interrupted Expedition, relic for Emergent Ruins). Expect harder cans: yellow (70) and red (90) cores.</li>
          <li><b>Warp to it from the Journal entry.</b> There's no signature to probe — you jump straight from the logged location. Take fleetmates if you want backup; they can follow you in.</li>
          <li><b>Free loot, sometimes big.</b> Typically ~20–50M in datacores, decryptors &amp; salvage, but cans can drop Triglavian Survey Databases, Atavums or module/implant BPCs. No fight for it — always worth grabbing.</li>
        </ol>
      </div>
    </div>

    <div class="exp-primer-grid">
      <div class="exp-primer-sec">
        <h4>Setup — pack the hold</h4>
        <ul>
          <li>Matching <b>Analyzer</b> (data / relic), plus a <b>Cargo Scanner</b> to skip junk cans.</li>
          <li>A <b>Mobile Depot</b> lets you refit modules out in space.</li>
          <li>Hacking &amp; Archaeology skills: <b>+10 Coherence</b> per level.</li>
        </ul>
      </div>
      <div class="exp-primer-sec">
        <h4>Ship by danger</h4>
        <ul>
          <li><b>Highsec:</b> T1 frigate (Heron / Magnate).</li>
          <li><b>Lowsec:</b> T1, or Astero to cloak.</li>
          <li><b>Nullsec:</b> Covert Ops frigate / Astero. Cloak up.</li>
          <li><b>W-space hack:</b> Covert Ops / Astero / Stratios.</li>
          <li><b>W-space combat:</b> BC (C1–2) → BS (C3) → fleet (C4–6).</li>
        </ul>
      </div>
      <div class="exp-primer-sec">
        <h4>Finding more sites</h4>
        <ul>
          <li>Each system rolls its own mix — you <b>can't</b> force other types where you stand. Hop systems and re-scan.</li>
          <li>Move whole <b>constellations</b>, not just one jump — spawns are budgeted per constellation and respawn on a timer.</li>
          <li>Watch the <b>Anomalies</b> tab too: combat (DED) &amp; ore sites need <b>no probes</b>, so probe-only scanning walks past them.</li>
          <li>Deeper, lower-<b>truesec</b> (−0.7 to −1.0) rolls richer combat sites &amp; the best faction relic/data.</li>
          <li>Hunt <b>quiet backwaters</b> — farmed systems run thin; systems nobody clears stack up.</li>
        </ul>
      </div>
    </div>

  </div>`;

let EXP = { selected: null, recent: [] };

// Cap recents at 10 on load too — a legacy/oversized blob shouldn't overflow.
const EXP_RECENT_MAX = 10;
try { EXP.recent = (JSON.parse(localStorage.getItem("exp-recent")) || []).slice(0, EXP_RECENT_MAX); } catch(e) { EXP.recent = []; }

function expSaveRecent(){
  EXP.recent = EXP.recent.slice(0, EXP_RECENT_MAX);
  try { localStorage.setItem("exp-recent", JSON.stringify(EXP.recent)); } catch(e){}
  // Also push into the server-synced settings blob so recents follow the
  // logged-in character across browsers/devices (loadSettings restores them).
  if(typeof saveLS==="function") saveLS();
}

// A glyph + label per site type — gives every "card" a game-y suit icon.
const EXP_TYPE = {
  data:          {icon:"🖥️", label:"Data"},
  relic:         {icon:"🏺", label:"Relic"},
  ghost:         {icon:"👻", label:"Ghost"},
  sleeper_cache: {icon:"🛸", label:"Sleeper Cache"},
  gas:           {icon:"☁️", label:"Gas"},
};
function expType(site){ return EXP_TYPE[site.type] || {icon:"❔", label:site.type}; }

// Collapse a site's danger / loot into a shared low·med·high scale so recent
// cards read at a glance. Returns {lvl:1-3, label, cls}.
function expRisk(site){
  return site.danger==="safe"     ? {lvl:1, label:"Low",  cls:"exp-lvl-lo"}
       : site.danger==="caution"  ? {lvl:2, label:"Med",  cls:"exp-lvl-md"}
       :                            {lvl:3, label:"High", cls:"exp-lvl-hi"};
}
function expLoot(site){
  const t = site.lootTier||"low";
  return t==="low"    ? {lvl:1, label:"Low",  cls:"exp-lvl-lo"}
       : t==="medium" ? {lvl:2, label:"Med",  cls:"exp-lvl-md"}
       :                {lvl:3, label:"High", cls:"exp-lvl-hi"};  // high + jackpot
}

function expRenderRecent(){
  const list = $("#exp-recent-list");
  if(!EXP.recent.length){ list.innerHTML = '<div style="color:var(--dim);font-size:13px;padding:6px 10px">No recent lookups</div>'; return; }
  list.innerHTML = EXP.recent.map((name,i)=>{
    const site = EXP_SITES.find(s=> s.name===name);
    if(!site) return "";
    const risk = expRisk(site), loot = expLoot(site);
    return `<div class="exp-recent-item exp-recent-card exp-danger-${site.danger}" data-idx="${i}">
      <button class="exp-recent-rm" data-ri="${i}" title="Remove">×</button>
      <div class="exp-recent-name">${esc(site.name)}</div>
      <div class="exp-recent-stats">
        <span class="exp-stat"><span class="exp-stat-k">Risk</span><span class="exp-stat-v ${risk.cls}">${risk.label}</span></span>
        <span class="exp-stat"><span class="exp-stat-k">Loot</span><span class="exp-stat-v ${loot.cls}">${loot.label}</span></span>
      </div>
    </div>`;
  }).join("");
  list.querySelectorAll(".exp-recent-item").forEach(el=>{
    el.onclick = (e)=>{ if(e.target.classList.contains("exp-recent-rm")) return; const site = EXP_SITES.find(s=>s.name===EXP.recent[+el.dataset.idx]); if(site) expSelect(site); };
  });
  list.querySelectorAll(".exp-recent-rm").forEach(btn=>{
    btn.onclick = (e)=>{ e.stopPropagation(); EXP.recent.splice(+btn.dataset.ri,1); expSaveRecent(); expRenderRecent(); };
  });
}

// Browse-all list: every site grouped by type, collapsible per group. This is
// the primary discovery path — you don't need to know a site's exact name.
const EXP_BROWSE_ORDER = ["data","relic","ghost","sleeper_cache","gas"];
function expRenderBrowse(){
  const wrap = $("#exp-browse-list");
  if(!wrap) return;
  let html = "";
  for(const type of EXP_BROWSE_ORDER){
    const sites = EXP_SITES.filter(s=> s.type===type);
    if(!sites.length) continue;
    const ty = EXP_TYPE[type] || {icon:"❔", label:type};
    html += `<div class="exp-browse-group" data-type="${esc(type)}">
      <button class="exp-browse-gh" type="button" data-type="${esc(type)}">
        <span class="exp-browse-gi">${ty.icon}</span>
        <span class="exp-browse-gl">${esc(ty.label)}</span>
        <span class="exp-browse-gc">${sites.length}</span>
        <span class="exp-browse-gcaret">▸</span>
      </button>
      <div class="exp-browse-items">${sites.map(s=>{
        const risk = expRisk(s);
        return `<button class="exp-browse-item exp-danger-${s.danger}" type="button" data-name="${esc(s.name)}">
          <span class="exp-browse-name">${esc(s.name)}</span>
          <span class="exp-browse-risk ${risk.cls}">${risk.label}</span>
        </button>`;
      }).join("")}</div>
    </div>`;
  }
  wrap.innerHTML = html;
  // Toggle a group open/closed.
  wrap.querySelectorAll(".exp-browse-gh").forEach(btn=>{
    btn.onclick = ()=> btn.parentElement.classList.toggle("open");
  });
  // Open a site.
  wrap.querySelectorAll(".exp-browse-item").forEach(btn=>{
    btn.onclick = ()=>{ const s = EXP_SITES.find(x=>x.name===btn.dataset.name); if(s) expSelect(s); };
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

  // Only add new sites to the front — re-opening an existing recent leaves the
  // list order untouched so the sidebar doesn't reshuffle under the click.
  if(!EXP.recent.includes(site.name)){
    EXP.recent = [site.name, ...EXP.recent].slice(0, EXP_RECENT_MAX);
    expSaveRecent();
  }
  expRenderRecent();
  expMarkBrowseActive(site);

  $("#exp-guide").innerHTML = expBuildGuide(site);
  $("#exp-content").scrollTop = 0;
}

// Highlight the selected site in the Browse list and open its group.
function expMarkBrowseActive(site){
  const wrap = $("#exp-browse-list");
  if(!wrap) return;
  let active = null;
  wrap.querySelectorAll(".exp-browse-item").forEach(b=>{
    b.classList.remove("active");
    if(b.dataset.name===site.name) active = b;
  });
  if(active){ active.classList.add("active"); active.closest(".exp-browse-group").classList.add("open"); }
}

// Build the whole single-column guide: hero banner → equal facts tiles →
// the inline walkthrough, with this site's own triggers / tips / NPCs / loot
// woven into the matching sections. Content is trusted (site data + the static
// guide strings), so guide HTML is injected un-escaped; raw site fields are
// escaped via esc().
function expBuildGuide(site){
  const ty = expType(site), risk = expRisk(site), loot = expLoot(site), g = expWalkthrough(site);
  const space = esc(site.space.join(", ")) + (site.whClass ? " <span class='exp-hero-wh'>C"+site.whClass.join(",")+"</span>" : "");
  const npc = site.hasNPCs ? esc(site.npcType) : "None";
  const npcCls = site.hasNPCs ? (site.danger==="dangerous"?"exp-lvl-hi":"exp-lvl-md") : "exp-lvl-lo";

  // Hero banner + equal-width facts tiles (always the same 5 tiles → balanced).
  let h = `
    <div class="exp-hero-banner exp-danger-${site.danger}">
      <div class="exp-hero-crest">${ty.icon}</div>
      <div class="exp-hero-id">
        <div class="exp-hero-type">${esc(ty.label)} site</div>
        <div class="exp-hero-name">${esc(site.name)}</div>
      </div>
    </div>
    <div class="exp-facts">
      <div class="exp-fact"><span class="exp-fact-k">Risk</span><span class="exp-fact-v ${risk.cls}">${risk.label}</span></div>
      <div class="exp-fact"><span class="exp-fact-k">Loot</span><span class="exp-fact-v ${loot.cls}">${loot.label}</span></div>
      <div class="exp-fact"><span class="exp-fact-k">NPCs</span><span class="exp-fact-v ${npcCls}">${npc}</span></div>
      <div class="exp-fact"><span class="exp-fact-k">Value</span><span class="exp-fact-v exp-fact-gold">${esc(site.estimatedValue)}</span></div>
      <div class="exp-fact exp-fact-wide"><span class="exp-fact-k">Found in</span><span class="exp-fact-v">${space}</span></div>
    </div>`;

  // The walkthrough, section by section, with per-site specifics folded in.
  const sec = (icon,title,inner)=> `<div class="exp-wg-sec"><div class="exp-wg-h"><span class="exp-wg-ico">${icon}</span>${title}</div>${inner}</div>`;
  const ul  = arr => `<ul class="exp-wg-list">${arr.map(x=>`<li>${x}</li>`).join("")}</ul>`;
  const ol  = arr => `<ol class="exp-wg-steps">${arr.map(x=>`<li>${x}</li>`).join("")}</ol>`;

  h += `<div class="exp-wg-lead">${g.overview}</div>`;

  // This-site facts box: triggers + tips, verbatim from the site record.
  let mech = `<p class="exp-wg-p">${esc(site.triggers)}</p>`;
  if(site.tips) mech += `<p class="exp-wg-p exp-wg-tip"><b>Tip:</b> ${esc(site.tips)}</p>`;
  h += sec("🧭","This site — mechanics", mech);

  if(g.entry)   h += sec("🛰️","Scan &amp; entry", `<p class="exp-wg-p">${g.entry}</p>`);

  // Hazards: the site's own NPC/hazard summary first, then the general list.
  let haz = "";
  if(site.hasNPCs){
    const hazardLabels = {combat_npcs:"Combat NPCs on grid",exploding_cans:"Exploding containers",env_damage:"Environmental damage (clouds/turrets)",response_fleet:"Response fleet warps in",timed_spawn:"Timed delayed NPC spawn"};
    haz += `<p class="exp-wg-p"><b>${esc(site.npcType)}</b>${site.npcDetail?" — "+esc(site.npcDetail):""}`;
    if(site.hazards&&site.hazards.length) haz += ` <span class="exp-wg-tags">${site.hazards.map(x=>`<span class="exp-wg-tag">${esc(hazardLabels[x]||x)}</span>`).join("")}</span>`;
    haz += `</p>`;
  } else {
    haz += `<p class="exp-wg-p exp-wg-safe">✔ No NPCs on grid.</p>`;
  }
  if(g.hazards) haz += ul(g.hazards);
  h += sec("⚠️","Hazards", haz);

  if(g.ship)    h += sec("🚀","Ship &amp; fit", `<p class="exp-wg-p">${g.ship}</p>`);
  if(g.steps)   h += sec("📋","Step by step", ol(g.steps));

  // Loot: the general note, then this site's tier / examples / value.
  const tierCls = "exp-tier-"+(site.lootTier||"low");
  let lootHtml = `<p class="exp-wg-p">${g.loot}</p>`;
  lootHtml += `<div class="exp-loot-meta">`;
  lootHtml += `<span class="exp-loot-tier ${tierCls}">${esc(site.lootTier||"low")}</span>`;
  lootHtml += `<span class="exp-loot-val">${esc(site.estimatedValue)}</span></div>`;
  lootHtml += `<p class="exp-wg-p"><b>This site drops:</b> ${esc(site.lootSummary)}.`;
  if(site.lootExamples&&site.lootExamples.length) lootHtml += ` e.g. ${site.lootExamples.map(e=>esc(e)).join(", ")}.`;
  lootHtml += `</p>`;
  h += sec("💰","Loot &amp; value", lootHtml);

  if(g.rule) h += `<div class="exp-wg-rule"><span class="exp-wg-rule-tag">#1 RULE</span> ${g.rule}</div>`;
  return h;
}

function esc(s){ return s==null?"":String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;"); }

$("#exp-search").addEventListener("input", expSearch);
document.addEventListener("click", (e)=>{
  const sidebar = e.target.closest(".exp-sidebar");
  if(!sidebar){ $("#exp-results").classList.add("hidden"); }
});
expRenderRecent();
expRenderBrowse();

// "How hacking works" cheat-sheet — a slide-out overlay opened from the sticky
// sidebar button. Populated once; closes on ✕, backdrop click, or Escape.
(function(){
  const body = $("#exp-primer-body");
  const toggle = $("#exp-primer-toggle");
  const overlay = $("#exp-primer-overlay");
  const close = $("#exp-primer-close");
  if(!body || !toggle || !overlay) return;
  body.innerHTML = EXP_PRIMER_HTML;
  const setOpen = (open)=>{
    overlay.classList.toggle("hidden", !open);
    toggle.classList.toggle("active", open);
  };
  toggle.onclick = ()=> setOpen(overlay.classList.contains("hidden"));
  if(close) close.onclick = ()=> setOpen(false);
  overlay.addEventListener("click", (e)=>{ if(e.target===overlay) setOpen(false); });
  document.addEventListener("keydown", (e)=>{ if(e.key==="Escape") setOpen(false); });
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
