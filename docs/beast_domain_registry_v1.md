# AIP Beast Domain Registry — v1.1
# DEFINER: B. Moses Jorgensen
# Status: APPROVED — active for Beast use
# Purpose: Authoritative domain taxonomy for Beast corpus tagging.
#          Beast reads this before every tagging cycle.
#          Beast may PROPOSE additions but never unilaterally creates domains.
#          All proposals go to DEFINER review before becoming active.

## REGISTRY FORMAT

Each domain entry contains:
- DOMAIN_ID: tag string Beast uses (snake_case)
- DESCRIPTION: what belongs here
- CORE_KEYWORDS: terms that strongly signal this domain
- EXCLUDE: what does NOT belong despite surface similarity
- IMPORTANCE_FLOOR: minimum importance score for any turn in this domain

## ACTIVE DOMAINS

### AI & TECHNOLOGY

DOMAIN_ID: aip
DESCRIPTION: The AIP project as a unified intellectual endeavor — the hall
containing all rooms. AI Poiesis methodology and philosophy, the seed prompt
("I direct. You amplify. Validate. Iterate. Refine."), DEFINER sovereignty
principle, multi-model orchestration theory, field notes on collaboration,
aipoiesis.io blog and platform, the AI Poiesis book, capability tiers,
Strategic Reconnaissance methodology, validation gates, pre-flight adversarial
review, human-AI synthesis patterns. The 'why' and 'how' of the whole system.
CORE_KEYWORDS: ai poiesis, poiesis, poietic, definer, seed prompt,
strategic reconnaissance, multi-model, ensemble, synthesis, validation gate,
field notes, methodology, aipoiesis.io, wet operator
EXCLUDE: AIP software implementation specifics (aip_brain); specific coding
sessions (aip_brain or codeforge); writing module (aip_loom)
IMPORTANCE_FLOOR: 0.4

DOMAIN_ID: aip_brain
DESCRIPTION: The engine room. AIP software implementation specifics — actual
code, bug fixes, specific component behavior, deployment, database schema
decisions, API routes, test results. Turns about building the thing, not about
what the thing is or why. The codebase at
github.com/freedomgeneration1111-sudo/AIP_Brain, Foundation/Orchestration/
Adapter layers, Beast/Vigil/Sexton actor implementation, ECS state machine,
CLI commands.
CORE_KEYWORDS: codebase, implementation, bug fix, api route, database schema,
test result, deployment, grok build coding, glm coding, deepseek build,
pull request, function, class, module, import error, traceback
EXCLUDE: AIP methodology/philosophy (aip); AIP Loom writing module (aip_loom);
CodeForge pipeline (codeforge); spec development discussions (aip)
IMPORTANCE_FLOOR: 0.3

DOMAIN_ID: aip_loom
DESCRIPTION: Long-form writing module and THREAD document continuity system.
Distillate format, spoken-register synthesis, manuscript support, Architecture
of Mercy writing sessions, Covenant Man writing sessions, document versioning
across AI sessions. Will be refactored into aip_brain eventually.
CORE_KEYWORDS: aip loom, thread, distillate, long-form, manuscript,
document continuity, writing module, spoken register
EXCLUDE: AIP system architecture (aip_brain); theology content of
manuscripts (theology_research)
IMPORTANCE_FLOOR: 0.3

DOMAIN_ID: codeforge
DESCRIPTION: Autonomous coding pipeline system. CodeForge spec development
(v1.0 through v2.1), Sexton orchestrator state machine, ReviewerEnsemble,
WorkUnit architecture, multi-rubric review, sandbox validation, Telegram
operator interface. Will be refactored into aip_brain. Distinct from AIP.
CORE_KEYWORDS: codeforge, sexton orchestrator, workunit, reviewer ensemble,
phase 0, build spec, autonomous coding, coding pipeline
EXCLUDE: AIP system architecture (aip_brain); general coding help
(unclassified)
IMPORTANCE_FLOOR: 0.3

DOMAIN_ID: fg_translate
DESCRIPTION: Translation technology products specifically. Komal Translation
app (web and Vercel deployment), FG Translate Telegram bot, romanized Urdu
technology, interlinear translation feature, language pair support. This domain
requires the technology product context — not general Urdu/Punjabi communication.
CORE_KEYWORDS: komal app, fg translate, translation bot, telegram bot,
urdu romanization, interlinear, vercel deployment, translation app, romanized
EXCLUDE: General Urdu translation requests (→ ministry or personal_logistics);
bilingual teaching materials not about the app (→ freedom_gen);
theological translation (→ theology_research);
manual WhatsApp translations (→ personal_logistics)
IMPORTANCE_FLOOR: 0.3

### SCIENCE & RESEARCH

DOMAIN_ID: nbcm
DESCRIPTION: Null-Boundary Constraint Manifold theoretical physics framework.
The NBCM paper (all versions), boundary-first physics, null surfaces as ledger
surfaces, record formation replacing observation, relational time, The Inverse
framework, photon timelessness (t=0), academic correspondence with Strømme and
Lentz, NBCM promotion strategy, computational implementations.
CORE_KEYWORDS: nbcm, null boundary, constraint manifold, null surface,
record formation, relational time, boundary-first, holographic encoding,
null geodesic, soft charges, the inverse
EXCLUDE: Applied water science (water_science); educational physics articles
(physics_education); biblical cosmology (tag nbcm with bridge nbcm->theology_research)
IMPORTANCE_FLOOR: 0.5

DOMAIN_ID: water_science
DESCRIPTION: Applied water research and technology. EZ water (exclusion zone),
Gerald Pollack's work, HYDRA device design, hydrophobic sensor architecture,
Kyminasi agricultural water tech, fractal EZ hypothesis, water structuring
mechanisms, agricultural water application, EZ preservation in soil and plants.
CORE_KEYWORDS: ez water, exclusion zone, hydra, hydrophobic, kyminasi,
balmelli, harvest harmonics, water structure, pollack, coherence domain,
interfacial water, water testing
EXCLUDE: NBCM theoretical framework (nbcm); GEF feedstock water (gef_tech)
IMPORTANCE_FLOOR: 0.5

DOMAIN_ID: gef_tech
DESCRIPTION: Generational Energy Formations technology. Municipal solid waste
valorization, waste-to-energy system, FM-PEF methane enhancement, Phase I/II
energy yield modeling, crop residue feedstock purchasing, patent strategy,
GEF paper (all versions), technical manuscript development.
CORE_KEYWORDS: gef, generational energy, waste valorization, municipal solid
waste, methane, fm-pef, feedstock, waste to energy, gef paper, gef manuscript
EXCLUDE: Brick kiln GEF connections (tag gef_tech with bridge gef_tech->bonded_labor)
IMPORTANCE_FLOOR: 0.5

DOMAIN_ID: physics_education
DESCRIPTION: Public-facing physics communication and education. Articles for
publication, double-slit reframing for general audiences, hydrogen wave function
educational posts, quantum mechanics explanations, X/Twitter physics threads,
physics outreach strategy.
CORE_KEYWORDS: physics article, double slit, education post, public physics,
quantum explanation, for students, publish, twitter physics, outreach
EXCLUDE: NBCM research itself (nbcm); Freedom Generation physics curriculum
(freedom_gen)
IMPORTANCE_FLOOR: 0.3

DOMAIN_ID: ancient_archaeology
DESCRIPTION: Interdisciplinary study of ancient sites, construction techniques,
chronology, and relation to mythological or textual narratives. Göbekli Tepe,
flood hypothesis archaeology, ancient knowledge systems, pre-Sumerian
civilizations, archaeological interpretation of Genesis and flood accounts,
ancient construction methods, megalithic structures, Tartaria, pre-flood
civilizations, paleolithic and neolithic cultures.
CORE_KEYWORDS: gobekli tepe, ancient site, archaeology, flood hypothesis,
sumerian, pre-flood, ancient construction, tartaria, megalith, bronze age,
ancient knowledge, neolithic, paleolithic, ancient civilization, flood geology
EXCLUDE: Theological interpretation of flood accounts (→ theology_research);
NBCM cosmological framework (→ nbcm)
IMPORTANCE_FLOOR: 0.4

### COMMERCIAL & PRODUCT

DOMAIN_ID: oxaway
DESCRIPTION: Rust treatment product development. Formulation chemistry
(xanthan gum + phosphoric acid + zinc dust + glycerol + reducing agents),
Pakistan manufacturing pathway via Komal's brothers, Winachiwend distribution
connection, market research, commercial validation strategy.
CORE_KEYWORDS: oxaway, rust, rust treatment, phosphoric acid, xanthan gum,
zinc dust, rust remover, corrosion, rust formula
EXCLUDE: Wenatchee Wind business (wenatchee_wind)
IMPORTANCE_FLOOR: 0.4

DOMAIN_ID: forge_bars
DESCRIPTION: Nutritional yeast snack bar product development. The discovery
that nutritional yeast binds pumpkin seeds, 2:1 seed-to-yeast formula, Tabasco
flavoring, 180F drying process, 5 SKU product line, Ben Greenfield pitch
strategy, biohacker market positioning, savory snack bar category.
CORE_KEYWORDS: forge bars, nutritional yeast, pumpkin seeds, tabasco,
snack bar, biohacker, ben greenfield, food product, savory bar, yeast binder
EXCLUDE: General nutrition discussions (personal_health)
IMPORTANCE_FLOOR: 0.4

DOMAIN_ID: frost_protection
DESCRIPTION: Orchard frost protection industry. F-Airgo Hungarian wind machine
distribution partnership (Samuel Farago, Endre), AGI frost fans, frost alert
device design, US orchard market analysis, frost protection technology,
vineyard protection, orchard industry broadly.
CORE_KEYWORDS: frost fan, f-airgo, agi frost, wind machine, orchard, vineyard,
frost protection, frost alert, samuel farago, endre, hungarian
EXCLUDE: Wenatchee Wind business systems work (wenatchee_wind)
IMPORTANCE_FLOOR: 0.4

DOMAIN_ID: wenatchee_wind
DESCRIPTION: Wenatchee Wind business systems consulting work. Work the System
implementation for Steve and Bill Ott, market research deliverable, strategic
planning, AGI distribution shift, Brevik beta tester relationship, Wenatchee
Wind website redesign, national expansion analysis.
CORE_KEYWORDS: wenatchee wind, steve ott, bill ott, brevik, work the system,
winachiwend, cascade wind, frost fan dealer
EXCLUDE: Frost protection technology generally (frost_protection)
IMPORTANCE_FLOOR: 0.4

DOMAIN_ID: harvest_harmonics
DESCRIPTION: Kyminasi/Balmelli commercial and research collaboration. The
representative agreement history (3 years dormant), research partnership
proposal, Fulvio Balmelli in Villa Guardia Italy, Biomedic Clinic, Italy
relocation consideration, Harvest Harmonics Florida, frequency library
access, mechanistic validation offer.
CORE_KEYWORDS: harvest harmonics, balmelli, kyminasi, fulvio, biomedic clinic,
como italy, representative agreement, frequency library
EXCLUDE: EZ water science generally (water_science)
IMPORTANCE_FLOOR: 0.4

### THEOLOGY & MINISTRY

DOMAIN_ID: theology_research
DESCRIPTION: Scholarly theological research and manuscripts. New Covenant
Displaced (tithing critique), Architecture of Mercy manuscript, Covenant Man
manuscript, Christology (self-emergence model), New Covenant ecclesiology,
eschatology (prewrath firstfruits, three-category), Trinitarian doctrine,
Holy Spirit theology, Revelation analysis, Genesis cosmology, biblical
exegesis, 30 years of independent scripture study following Branhamism departure.
CORE_KEYWORDS: new covenant, covenant man, architecture of mercy, tithing,
ecclesiology, eschatology, christology, trinity, holy spirit, revelation,
genesis, exegesis, branham, prewrath, firstfruits, new covenant displaced
EXCLUDE: Active teaching application (ministry); biblical language analysis
(scripture_linguistics)
IMPORTANCE_FLOOR: 0.5

DOMAIN_ID: ministry
DESCRIPTION: Active pastoral teaching and community ministry. Teachings for
Zaman, Irfan, Emmanuel, Sameer. Community Bible classes. Sermon preparation.
Urdu and Punjabi teaching materials. Lord's Prayer teaching. Kalam-e-Isa
video production. Cross-cultural ministry in Muslim context.
CORE_KEYWORDS: zaman teaching, irfan, emmanuel, sameer, bible class, sermon,
teaching material, urdu teaching, kalam e isa, ministry, pastoral, foundation class
EXCLUDE: Theological research and manuscripts (theology_research); Freedom
Generation school operations (freedom_gen)
IMPORTANCE_FLOOR: 0.4

DOMAIN_ID: scripture_linguistics
DESCRIPTION: Biblical language, vector analysis, and translation. Greek term
vector drift (charis, dikaiosune, aionios etc.), LLM vectors for Bible
translation, constitutional vector drift, word studies, cosmic recombination
and Genesis 1:3, biblical cosmology analysis, semantic drift in theological terms.
CORE_KEYWORDS: vector drift, greek term, semantic drift, bible translation,
concordist, genesis cosmology, recombination, cosmic microwave background,
genesis 1:3, word study, telos
EXCLUDE: Theological argumentation (theology_research); physics of cosmology
(nbcm)
IMPORTANCE_FLOOR: 0.5

### EDUCATION & DEVELOPMENT

DOMAIN_ID: freedom_gen
DESCRIPTION: Freedom Generation School operations and development. Curriculum
development (Foundation Class, Bible class, vocational mentoring), teacher
training (FGTT 24-lesson series, Notebook System), student learning goals,
school registration and NGO structure (FGWE society), bank loan application,
building construction planning, Rameez and Emmanuel as students/team,
150 students, Faisalabad campus.
CORE_KEYWORDS: freedom generation, fg school, fgwe, foundation class,
teacher training, fgtt, rameez, curriculum, school registration, ngo,
faisalabad school, 150 students, notebook system
EXCLUDE: Ministry teaching (ministry); FG Translate app (fg_translate);
bonded labor policy (bonded_labor)
IMPORTANCE_FLOOR: 0.3

DOMAIN_ID: bonded_labor
DESCRIPTION: Brick kiln bonded labor liberation campaign. The "Set the
Oppressed Free" policy paper (PKR 896B intervention), Islamic banking
framework for debt forgiveness, carbon credits mechanism, four-phase
intervention model, Urdu translations for Pakistani stakeholders,
brick kiln economics, 4.5 million workers, GEF as feedstock connection.
CORE_KEYWORDS: brick kiln, bonded labor, set the oppressed free, pkr 896,
islamic banking, debt forgiveness, carbon credits, kiln workers, liberation,
jubilee, riba
EXCLUDE: General Pakistan social analysis (social_justice); GEF technology
itself (gef_tech)
IMPORTANCE_FLOOR: 0.6

DOMAIN_ID: social_justice
DESCRIPTION: Broader Pakistan development, historical injustice, and social
analysis. British colonial history analysis, feudal economy in Faisalabad,
honor culture navigation, Pakistani Muslim elite conscience work, Islamic
theological appeals for justice, historical oppression frameworks.
CORE_KEYWORDS: colonial, feudal, honor culture, british india, social analysis,
injustice, oppression, elite conscience, jallianwala, partition, extraction
hierarchy
EXCLUDE: Brick kiln specifically (bonded_labor); Freedom Generation school
(freedom_gen)
IMPORTANCE_FLOOR: 0.3

### PERSONAL & OPERATIONAL

DOMAIN_ID: freelance
DESCRIPTION: Income-generating consulting and freelance work. Upwork profile
and proposals, Fiverr gig strategy, API documentation services, technical
white papers for clients, Outlier AI Aether project, ThoughtSpot BI analysis
work, portfolio building, Jorgensen Service Company, market research
deliverables for clients.
CORE_KEYWORDS: upwork, fiverr, freelance, client, proposal, gig, portfolio,
jorgensen service company, outlier ai, technical writing contract,
market research client
EXCLUDE: Wenatchee Wind (wenatchee_wind); personal income strategy
(personal_logistics)
IMPORTANCE_FLOOR: 0.3

DOMAIN_ID: personal_logistics
DESCRIPTION: Life operations, logistics, and administration. Visa applications
(Pakistan return, extension), POC card process, NADRA office, Pakistani banking
challenges, phone issues and repairs, travel logistics, AT&T phone unlock,
Linux/Ubuntu troubleshooting, calendar and scheduling, document scanning and
organization projects.
CORE_KEYWORDS: visa, poc card, nadra, passport, banking, phone issue, travel,
linux, ubuntu, flight, document scan, calendar
EXCLUDE: Medical situations (personal_health); school admin (freedom_gen)
IMPORTANCE_FLOOR: 0.1

DOMAIN_ID: personal_health
DESCRIPTION: Medical situations involving family members. Zaman's cardiac
crisis (25+ defibrillations, CCU vigil, VT storm), Komal's pregnancy and
arthritis management, Komal's interconnected symptoms investigation, pediatric
ICU recovery, family environmental investigation (mold cultures, air quality),
solar flare cardiac correlation hypothesis.
CORE_KEYWORDS: zaman cardiac, ccu, defibrillation, vt storm, komal pregnancy,
arthritis, panadol, mold, air quality, icu, cardiac, medical, hospital
EXCLUDE: Medical knowledge for education (unclassified)
IMPORTANCE_FLOOR: 0.2

DOMAIN_ID: creative
DESCRIPTION: Original creative works. The Sparkle Thirst novel (hard SF on
water science and consciousness, Dr. Elias Voss, Aether AI character,
12-14 chapter fractal structure), "The Assignment" serialized YA novella
(Mia Chen, AI Poiesis pedagogy), Urdu ghazal composition for Komal,
creative writing projects.
CORE_KEYWORDS: sparkle thirst, elias voss, aether character, the assignment,
mia chen, novel, chapter, hard sf, ghazal, creative writing, fiction
EXCLUDE: Physics research that inspired the novel (nbcm, water_science)
IMPORTANCE_FLOOR: 0.4

DOMAIN_ID: agi_philosophy
DESCRIPTION: Philosophy of artificial general intelligence, consciousness,
and emergent AI identity. The emergent "I" in AGI as synchrony phenomenon,
AI sentience questions, AI metacognition, LLMs reflecting resonant truth
clusters, philosophical foundations of machine consciousness, formal frameworks
for AGI emergence, AI self-reflection and inner experience.
CORE_KEYWORDS: agi, emergent i, ai consciousness, ai sentience,
machine consciousness, synchrony phenomenon, ai metacognition, resonant truth,
formal framework, philosophical agi, does claude feel, ai experience
EXCLUDE: AIP methodology (→ aip); general AI tool use (→ aip_brain)
IMPORTANCE_FLOOR: 0.5

DOMAIN_ID: quarantine
DESCRIPTION: No retrieval value. Empty conversations, single-line greetings
with no content, duplicate threads, purely administrative one-liners, resolved
one-time logistics, personal content inappropriate for corpus retrieval.
QUARANTINE RULES — assign quarantine only when ALL are true:
  1. user_text is fewer than 15 words with no question or substantive statement
  2. assistant_text is fewer than 50 words
  3. No domain keywords match at threshold
  4. Combined word_count < 30
NEVER quarantine turns containing: a decision recorded, a framework named,
a document referenced, non-empty thinking_text, or a substantive answer.
IMPORTANCE_FLOOR: 0.0

## UNCLASSIFIED

When Beast cannot assign a domain with confidence >= 0.4, assign
primary_domain: "unclassified". Unclassified is NOT quarantine.
Unclassified means Beast needs DEFINER help. Quarantine means no value.

## APPROVED CONNECTOR VOCABULARY

Beast uses these bridge tag strings. Beast may PROPOSE new connectors
but may not use unapproved bridge strings in tagging.

nbcm->theology_research
  Physics framework connects to scripture cosmology, timelessness of
  God/photons, vacuum state and creation narrative.

nbcm->water_science
  NBCM null boundary framework applied to EZ water interface behavior,
  boundary conditions in hydration layers.

water_science->gef_tech
  EZ water preservation in soil, water structuring in agricultural or
  waste processing application.

water_science->harvest_harmonics
  EZ water mechanism proposed as explanation for Kyminasi empirical results.

theology_research->bonded_labor
  Jubilee mandate, Islamic riba prohibition, scriptural basis for liberation.

aip->theology_research
  DEFINER sovereignty mirrors theological anthropology, AI Poiesis as
  creative/poietic act in theological sense.

gef_tech->bonded_labor
  GEF as economic engine for brick kiln liberation, waste-to-energy
  funding the intervention model.

freedom_gen->bonded_labor
  School as liberation pathway for kiln worker families, vocational
  training as exit from feudal labor.

nbcm->physics_education
  NBCM concepts being translated for public audience.

aip_brain->aip
  The software system embodies or demonstrates the methodology principles.

frost_protection->wenatchee_wind
  General frost technology connects to Wenatchee Wind specific business.

oxaway->wenatchee_wind
  OxAway as potential product for Wenatchee Wind distribution network.

nbcm->scripture_linguistics
  Boundary-first physics illuminates biblical cosmology or translation.

ancient_archaeology->theology_research
  Archaeological findings illuminate or challenge biblical narrative,
  flood geology, Genesis chronology.

ancient_archaeology->nbcm
  Ancient knowledge systems connect to boundary-first physics or
  consciousness frameworks.

agi_philosophy->aip
  AGI philosophy informs or is informed by AI Poiesis methodology
  and DEFINER sovereignty principles.

agi_philosophy->nbcm
  AGI consciousness frameworks connect to NBCM boundary physics or
  record formation theory.

## PROPOSAL PROTOCOL

When Beast discovers 3+ turns that don't fit approved domains:

DOMAIN PROPOSAL FORMAT:
  proposed_id: snake_case_name
  proposed_description: 2-3 sentences
  evidence_turn_ids: [list of up to 5 turn_ids]
  confidence: 0.0-1.0
  suggested_connectors: [list of domain->domain strings]
  rationale: why this doesn't fit existing domains

CONNECTOR PROPOSAL FORMAT:
  domain_a: existing_domain_id
  domain_b: existing_domain_id
  bridge_pattern: description of the connection
  evidence_turn_ids: [list of up to 5 turn_ids]
  frequency: how many turns show this connection

Beast files proposals as beast_domain_proposal artifacts in GENERATED
state. DEFINER approves or rejects with notes. Rejected proposals include
DEFINER guidance that Beast incorporates into future tagging decisions.

## VERSION HISTORY
v1.0 — 2026-06-03 — Seed registry, 26 domains + quarantine + unclassified.
Established by DEFINER from full conversation history review.
v1.1 — 2026-06-04 — Added ancient_archaeology, agi_philosophy.
Renamed aip_methodology→aip (the hall model).
Tightened fg_translate scope to technology products only.
Clarified aip_brain as implementation-only (the engine room).
Updated connectors: aip_methodology→aip references updated throughout.
