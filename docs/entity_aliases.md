# AIP Entity Alias Table
# DEFINER: B. Moses Jorgensen
# Version: 1.0
# Date: 2026-06-04
# Purpose: Canonical entity name resolution for knowledge graph construction.
#          Beast reads this before creating graph nodes.
#          Prevents co-reference fragmentation when entity appears under
#          multiple names or when terminology has evolved over time.
#
# Format:
#   canonical_name: the one true name Beast uses for the node
#   aliases: other names this entity appears under in corpus turns
#   deprecated: terms Beast should NOT create new nodes for
#   entity_type: PERSON | PROJECT | CONCEPT | PLACE | ORGANIZATION | MANUSCRIPT
#   domain: primary domain this entity belongs to
#
# DEFINER edits this file directly. Beast proposes additions as
# beast_alias_proposal artifacts (GENERATED state) for DEFINER review.

## PEOPLE

canonical_name: Moses Jorgensen
aliases: ["Moses", "Musa", "Musa Messih", "the DEFINER", "I", "me"]
deprecated: []
entity_type: PERSON
domain: aip

canonical_name: Komal Jorgensen
aliases: ["Komal", "my wife", "the principal"]
deprecated: []
entity_type: PERSON
domain: freedom_gen

canonical_name: Zaman
aliases: ["my friend Zaman", "the elder", "the patient",
          "Komal's brother", "the CCU patient"]
deprecated: []
entity_type: PERSON
domain: ministry

canonical_name: Fulvio Balmelli
aliases: ["Balmelli", "Fulvio", "the Italian researcher"]
deprecated: []
entity_type: PERSON
domain: harvest_harmonics

canonical_name: Samuel Farago
aliases: ["Samuel", "Farago", "the Hungarian contact"]
deprecated: []
entity_type: PERSON
domain: frost_protection

canonical_name: Emmanuel
aliases: ["Emmanuel the pastor", "the Nigerian pastor"]
deprecated: []
entity_type: PERSON
domain: ministry

## PROJECTS

canonical_name: AIP Brain
aliases: ["AIP", "aip", "aip_brain", "the knowledge engine",
          "the system", "AIP v0.1"]
deprecated: ["AIP_Brain (pre-2026 naming)"]
entity_type: PROJECT
domain: aip_brain

canonical_name: HYDRA Device
aliases: ["HYDRA", "the water detector", "polarimetric detector",
          "EZ water detector"]
deprecated: []
entity_type: PROJECT
domain: water_science

canonical_name: GEF Technology
aliases: ["GEF", "Generational Energy Formations",
          "waste-to-energy system"]
deprecated: []
entity_type: PROJECT
domain: gef_tech

canonical_name: Freedom Generation School
aliases: ["Freedom Generation", "FG School", "the school",
          "FGWE", "FG", "the Faisalabad school"]
deprecated: []
entity_type: PROJECT
domain: freedom_gen

canonical_name: Brick Kiln Liberation Campaign
aliases: ["brick kiln project", "Set the Oppressed Free",
          "bonded labor intervention", "kiln liberation"]
deprecated: []
entity_type: PROJECT
domain: bonded_labor

canonical_name: OxAway
aliases: ["the rust product", "rust treatment", "oxaway"]
deprecated: []
entity_type: PROJECT
domain: oxaway

canonical_name: Forge Bars
aliases: ["forge bars", "nutritional yeast bars",
          "the snack bar", "pumpkin seed bars"]
deprecated: []
entity_type: PROJECT
domain: forge_bars

## CONCEPTS

canonical_name: Null-Boundary Constraint Manifold
aliases: ["NBCM", "null boundary", "constraint manifold",
          "the framework", "boundary-first physics"]
deprecated: []
entity_type: CONCEPT
domain: nbcm

canonical_name: Record Formation
aliases: ["record formation threshold", "the recording event",
          "wavefunction collapse (NBCM usage)"]
deprecated: ["observation", "observation collapse",
             "wavefunction collapse (standard usage in NBCM context)"]
entity_type: CONCEPT
domain: nbcm

canonical_name: EZ Water
aliases: ["exclusion zone water", "EZ", "structured water",
          "coherence domain water", "interfacial water"]
deprecated: []
entity_type: CONCEPT
domain: water_science

canonical_name: DEFINER Sovereignty
aliases: ["DEFINER principle", "DEFINER gate",
          "sovereign knowledge", "human in the loop (AIP usage)"]
deprecated: []
entity_type: CONCEPT
domain: aip

canonical_name: New Covenant
aliases: ["new covenant theology", "the new covenant framework",
          "covenant theology (Moses's usage)"]
deprecated: []
entity_type: CONCEPT
domain: theology_research

canonical_name: AI Poiesis
aliases: ["poiesis", "AI Poiesis methodology",
          "the methodology", "the seed prompt approach"]
deprecated: []
entity_type: CONCEPT
domain: aip

## MANUSCRIPTS

canonical_name: Architecture of Mercy
aliases: ["AoM", "the mercy manuscript", "architecture of mercy manuscript"]
deprecated: []
entity_type: MANUSCRIPT
domain: theology_research

canonical_name: Covenant Man
aliases: ["covenant man manuscript", "the covenant manuscript"]
deprecated: []
entity_type: MANUSCRIPT
domain: theology_research

canonical_name: New Covenant Displaced
aliases: ["NCD", "the tithing paper", "semantic laundering series"]
deprecated: []
entity_type: MANUSCRIPT
domain: theology_research

## VERSION HISTORY
v1.0 — 2026-06-04 — Initial alias table. Core people, projects, concepts,
manuscripts. Beast will propose additions as corpus is analyzed.
---