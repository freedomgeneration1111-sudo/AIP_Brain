# AIP Entity Alias Table
# DEFINER: B. Moses Jorgensen
# Version: 1.0
# Date: 2026-06-04
# Purpose: Canonical entity name resolution for knowledge graph construction.
#          Beast reads this before creating graph nodes.
#          Prevents co-reference fragmentation.
#
# DEFINER edits directly. Beast proposes additions as
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
          "the CCU patient"]
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
aliases: ["AIP", "aip", "the knowledge engine", "AIP v0.1"]
deprecated: []
entity_type: PROJECT
domain: aip_brain

canonical_name: HYDRA Device
aliases: ["HYDRA", "the water detector", "EZ water detector"]
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
          "FGWE", "the Faisalabad school"]
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
aliases: ["the rust product", "rust treatment"]
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
          "boundary-first physics"]
deprecated: []
entity_type: CONCEPT
domain: nbcm

canonical_name: Record Formation
aliases: ["record formation threshold", "the recording event"]
deprecated: ["observation", "observation collapse",
             "wavefunction collapse (NBCM context)"]
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
          "sovereign knowledge"]
deprecated: []
entity_type: CONCEPT
domain: aip

canonical_name: New Covenant
aliases: ["new covenant theology", "the new covenant framework"]
deprecated: []
entity_type: CONCEPT
domain: theology_research

canonical_name: AI Poiesis
aliases: ["poiesis", "AI Poiesis methodology", "the methodology"]
deprecated: []
entity_type: CONCEPT
domain: aip

## MANUSCRIPTS

canonical_name: Architecture of Mercy
aliases: ["AoM", "the mercy manuscript"]
deprecated: []
entity_type: MANUSCRIPT
domain: theology_research

canonical_name: Covenant Man
aliases: ["covenant man manuscript"]
deprecated: []
entity_type: MANUSCRIPT
domain: theology_research

canonical_name: New Covenant Displaced
aliases: ["NCD", "the tithing paper", "semantic laundering series"]
deprecated: []
entity_type: MANUSCRIPT
domain: theology_research

## VERSION HISTORY
v1.0 — 2026-06-04 — Initial alias table. Core people, projects,
concepts, manuscripts. Beast proposes additions via corpus analysis.
