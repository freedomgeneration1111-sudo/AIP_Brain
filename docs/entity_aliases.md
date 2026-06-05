# Entity Alias Table v1.0
# docs/entity_aliases.md
#
# Purpose: Canonical co-reference resolution for AIP knowledge graph.
# All entity references in the corpus are resolved to canonical names
# before graph construction. Beast proposes additions; DEFINER approves.
#
# Format:
# canonical_name | type | domain | aliases
#
# Types: person | project | concept | place | manuscript | organization | tool
# Domains: use Beast domain registry codes
#
# Last updated: 2026-06-04
# DEFINER: B. Moses Jorgensen

## === PERSONS ===

B. Moses Jorgensen | person | aip | Moses, Musa, Musa Messih, the user, the DEFINER, I, me, Moses Jorgensen
Komal Jorgensen | person | fg_education | Komal, the principal, my wife, Komal Messih
Zaman | person | fg_ministry | Zaman, brother Zaman
Irfan | person | fg_ministry | Irfan, brother Irfan
Fulvio Balmelli | person | agri_tech | Fulvio, Balmelli, the Kyminasi contact

## === PROJECTS / SYSTEMS ===

AIP | project | aip | AI Poiesis, aip_brain, AIP Brain, AIP system, the system, my system, the knowledge engine, aip_methodology
AIP Loom | project | aip | loom, aip_loom, Loom
CodeForge | project | aip | code_forge, Forge Suite coder, the coder
Forge Suite | project | aip | Forge, the forge suite, idea_forge + architect_forge + spec_forge + code_forge
Beast | project | aip | Beast actor, the Beast, beast agent, the maintenance actor, beast orchestrator
Sexton | project | aip | Sexton orchestrator, sexton agent, the orchestrator
HYDRA | project | nbcm | HYDRA instrument, HYDRA detector, polarimetric detector
NBCM | project | nbcm | Null-Boundary Constraint Manifold, null-boundary framework, the manifold framework, NBCM framework
GEF | project | gef | Generational Energy Formations, GEF white paper, waste valorization project
Freedom Generation School | project | fg_education | FG School, Freedom Gen, FGS, the school, Freedom Generation
Freedom Generation Welfare and Education Society | project | fg_education | FGWES, the society, the NGO
F-AirGo | project | agri_tech | F-AirGo frost system, the frost protection system
Kyminasi | project | agri_tech | Kyminasi plant booster, Harvest Harmonics, the Kyminasi system
Jorgensen Service Company | project | aip | JSC, Jorgensen Service

## === CONCEPTS ===

DEFINER | concept | aip | the DEFINER, definer gate, DEFINER gate, human-in-the-loop authority, the sovereign
ECS lifecycle | concept | aip | ECS, artifact lifecycle, GENERATED/APPROVED lifecycle, state machine
AI Poiesis methodology | concept | aip | AI Poiesis, the methodology, poiesis methodology, expert-directed synthesis
OpenRouter | concept | aip | open router, the universal endpoint, model router
ChromaDB | concept | aip | chroma, vector db, vector store, vectordb
FTS5 | concept | aip | full-text search, SQLite FTS5, fts, keyword search
Hybrid retrieval | concept | aip | RRF, reciprocal rank fusion, hybrid search, FTS5+vector
EZ water | concept | nbcm | exclusion zone water, structured water, fourth phase water, Pollack water
NBCM framework | concept | nbcm | null-boundary constraint manifold, boundary-first physics, null geodesic geometry
New Covenant | concept | theology | the new covenant, new covenant theology, covenant of the Spirit
DEFINER profile | concept | aip | definer profile, user profile, identity profile, Moses profile
Seed corpus | concept | aip | the seed corpus, AIP seed, foundation corpus, self-knowledge corpus
Context advisory | concept | aip | context_advisory, augmented context, beast context advisory, ContextAdvisory
Domain registry | concept | aip | beast domain registry, domain taxonomy, the registry

## === MANUSCRIPTS / DOCUMENTS ===

Architecture of Mercy | manuscript | theology | "Architecture of Mercy", AoM, the soteriology series
Covenant Man and the Fractured Week | manuscript | theology | covenant man paper, fractured week thesis, Adam as covenant man
The New Covenant Displaced | manuscript | theology | NCD paper, tithing critique, institutional Christianity paper
The Sparkle Thirst | manuscript | fiction | Sparkle Thirst, the novel, the science fiction novel
Set the Oppressed Free | manuscript | policy | brick kiln paper, bonded labor paper, PKR 896 billion proposal
GEF White Paper | manuscript | gef | GEF paper, waste valorization paper, the GEF manuscript

## === PLACES / ORGANIZATIONS ===

Faisalabad | place | fg_ministry | Faisalabad Pakistan, FSB, the city
Pacific Northwest National Laboratory | place | chemistry | PNNL, Battelle, the laboratory
Anthropic | organization | aip | Anthropic AI, Claude's maker
GitHub | tool | aip | github.com, the repo, the repository
AIP_Brain repo | tool | aip | AIP_Brain, freedomgeneration1111-sudo/AIP_Brain, the codebase

## === AI TOOLS / MODELS ===

Claude | tool | aip | Claude AI, Anthropic's Claude, Claude Sonnet, Claude Opus
DeepSeek | tool | aip | DeepSeek AI, deepseek, DS
GPT-4o | tool | aip | ChatGPT, GPT, GPT4, OpenAI model
Grok | tool | aip | xAI Grok, Grok 3, the Grok agent
Gemini | tool | aip | Google Gemini, Gemini Pro
GLM | tool | aip | ChatGLM, GLM-4, Zhipu AI
Ollama | tool | aip | Ollama local, local inference, the local runner

## ============================================================
## BEAST PROPOSAL QUEUE
## (Beast proposes; DEFINER approves before moving above the line)
## ============================================================

# [empty — awaiting first Beast entity extraction pass]
