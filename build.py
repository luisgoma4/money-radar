"""money-radar: load tracked opportunities into money.db and render dashboard.html.

Run with:  python3 -I monitor.py && python3 -I build.py
Standard library only (``-I`` ignores user site-packages and the script dir).

To track a new call, append a dict to ROWS. Rows are inserted with INSERT OR
IGNORE, so ``first_seen`` is never overwritten; when an existing row's fields
change, the old value is logged to the ``changes`` table before the update.

Entries are never deleted (a trigger in money.db blocks DELETE). They are
archived instead, each with a reason and a ``review_on`` date:
- closed calls are archived automatically (review before the next edition);
- rows removed from ROWS are archived automatically (restore by re-adding them);
- a row can be archived explicitly with archived=True, archive_reason, review_on.
"""

import datetime as dt
import html
import importlib.util
import json
import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB = HERE / "money.db"
DASHBOARD = HERE / "dashboard.html"
ALERTS = HERE / "alerts.json"

TODAY = dt.date.today()
CLOSING_SOON_DAYS = 14
# Closed calls come up for review ~9 months after their deadline: about three
# months before a yearly call's next deadline, in time to prepare.
REVIEW_AFTER_CLOSE_DAYS = 270
REVIEW_AFTER_REMOVAL_DAYS = 90
HIGH_VALUE_EUR = 25_000

# Lines of work the radar matches calls against (see CLAUDE.md).
LINES = {
    "cloudy": "Cloudy · outreach science",
    "semf": "SEMF · research networks",
    "causality": "Causality Graphs · health data",
    "branchout": "BranchOut · tech startups",
    "delfina": "Fundación Delfina León · umbrella",
}

FIELDS = [
    "id", "name", "funder", "kind", "scope", "lines",
    "amount_min", "amount_max", "amount_text",
    "opens", "deadline", "deadline_kind", "status", "verification",
    "url", "source", "fit", "requirements", "action",
    "bdns", "winners",
    "archived", "archive_reason", "review_on",
]
# Fields whose changes are logged (status included, so open -> closed shows up).
TRACKED = [f for f in FIELDS if f != "id"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS opportunities (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    funder        TEXT NOT NULL,
    kind          TEXT NOT NULL,      -- grant | loan | equity | prize | network
    scope         TEXT NOT NULL,      -- Madrid West | Madrid | Spain | EU | International
    lines         TEXT NOT NULL,      -- comma-separated keys of LINES
    amount_min    INTEGER,            -- EUR, only if stated by an official source
    amount_max    INTEGER,
    amount_text   TEXT,
    opens         TEXT,               -- ISO date
    deadline      TEXT,               -- ISO date (NULL for rolling / not yet announced)
    deadline_kind TEXT NOT NULL,      -- fixed | rolling | expected
    status        TEXT NOT NULL,      -- open | upcoming | rolling | closed (derived)
    verification  TEXT NOT NULL,      -- verified | unverified
    url           TEXT NOT NULL,      -- official page
    source        TEXT,               -- where the facts were confirmed
    fit           TEXT,
    requirements  TEXT,
    action        TEXT,
    bdns          TEXT,               -- BDNS call number (infosubvenciones), if any
    winners       TEXT,               -- who won past/this edition: counts, amounts, comparable winners, source + date
    archived      INTEGER NOT NULL DEFAULT 0,  -- 1 = archived (never deleted)
    archive_reason TEXT,
    review_on     TEXT,               -- ISO date to review an archived row: recover or keep as reference
    first_seen    TEXT NOT NULL,
    last_checked  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS changes (
    opportunity_id TEXT NOT NULL,
    field          TEXT NOT NULL,
    old_value      TEXT,
    new_value      TEXT,
    changed_on     TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS opportunities_no_delete BEFORE DELETE ON opportunities
BEGIN SELECT RAISE(ABORT, 'money-radar: entries are archived, never deleted'); END;
CREATE TRIGGER IF NOT EXISTS changes_no_delete BEFORE DELETE ON changes
BEGIN SELECT RAISE(ABORT, 'money-radar: the change history is append-only'); END;
-- Applications in progress, managed by orchestrator.py (stages in strategy.json).
CREATE TABLE IF NOT EXISTS applications (
    opportunity_id TEXT PRIMARY KEY,
    stage          TEXT NOT NULL,
    applicant      TEXT,
    level          INTEGER,
    started_on     TEXT NOT NULL,
    updated_on     TEXT NOT NULL,
    notes          TEXT
);
CREATE TRIGGER IF NOT EXISTS applications_no_delete BEFORE DELETE ON applications
BEGIN SELECT RAISE(ABORT, 'money-radar: applications are closed, never deleted'); END;
-- Oracle ceremonies (oraculo.py registrar): decisions and commitments by role, never by name.
CREATE TABLE IF NOT EXISTS ceremonies (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    held_on     TEXT NOT NULL,
    summary     TEXT,
    decisions   TEXT NOT NULL,        -- JSON list of strings
    commitments TEXT NOT NULL,        -- JSON list of {rol, tarea, fecha}
    next_on     TEXT
);
CREATE TRIGGER IF NOT EXISTS ceremonies_no_delete BEFORE DELETE ON ceremonies
BEGIN SELECT RAISE(ABORT, 'money-radar: ceremonies are a record, never deleted'); END;
"""


def load_planner():
    """planner.py by file path (``python3 -I`` drops the script dir from sys.path)."""
    spec = importlib.util.spec_from_file_location("planner", HERE / "planner.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
# Columns added after the first release; ALTER TABLE them into older databases.
MIGRATIONS = {
    "bdns": "TEXT",
    "winners": "TEXT",
    "archived": "INTEGER NOT NULL DEFAULT 0",
    "archive_reason": "TEXT",
    "review_on": "TEXT",
}

# ---------------------------------------------------------------------------
# Tracked opportunities. Never invent amounts or dates: leave them None and
# keep verification="unverified" until an official page confirms them.
# ---------------------------------------------------------------------------
ROWS = [
    dict(
        id="cost-oc-2026-1",
        name="COST Open Call OC-2026-1 (new COST Action)",
        funder="COST Association (EU)",
        kind="network", scope="EU", lines="semf,causality",
        amount_min=None, amount_max=None,
        amount_text="Networking funding per Action (meetings, workshops, training schools, short-term scientific missions); budget set by COST",
        opens="2026-07-31", deadline="2026-10-28", deadline_kind="fixed",
        verification="verified",
        url="https://www.cost.eu/funding/how-to-get-funding/open-call-a-simple-one-step-application-process/",
        source="cost.eu Open Call page + OC-2026-1 announcement PDF (31 Mar 2026): collection 28 Oct 2026 12:00 CET",
        fit="SEMF already runs exactly what a COST Action pays for: themed conferences, the interdisciplinary summer school and workshops. A causal-inference-in-health network is a second angle.",
        requirements="Network of proposers from at least 7 COST countries, at least 50% from Inclusiveness Target Countries, at least 40% young researchers. One-step proposal on e-COST. Results expected May 2027.",
        action="Decide this week whether a 7+ country network can be assembled from SEMF partners; if not, plan for the next collection.",
    ),
    dict(
        id="nlnet-2026-11",
        name="NLnet open calls (Restack, CodeSupply, open call)",
        funder="NLnet Foundation (EU-funded programmes)",
        kind="grant", scope="EU", lines="branchout,causality",
        amount_min=5_000, amount_max=50_000,
        amount_text="EUR 5,000 – 50,000 for a first grant",
        opens="2026-09-03", deadline="2026-11-03", deadline_kind="fixed",
        verification="verified",
        url="https://nlnet.nl/propose/",
        source="nlnet.nl/propose + news 2026-08-03: deadline 3 Nov 2026 12:00 CET, then the 3rd of every odd month",
        fit="Fits any open-source tooling: e.g. an open causal-discovery / DAG library from Causality Graphs, or open infrastructure behind Cloudy's digital content.",
        requirements="Open licences and standards; strong European dimension. NLnet states it does not want AI-generated proposals — write it by hand.",
        action="Pick one open-source deliverable and draft a short proposal before 3 Nov; the next window is 3 Jan 2027.",
    ),
    dict(
        id="eic-accelerator-2026-11",
        name="EIC Accelerator — full application cut-off 4 Nov 2026",
        funder="European Innovation Council",
        kind="grant", scope="EU", lines="causality,branchout",
        amount_min=None, amount_max=2_500_000,
        amount_text="Grant below EUR 2.5M, plus optional equity EUR 1–10M",
        opens=None, deadline="2026-11-04", deadline_kind="fixed",
        verification="verified",
        url="https://eic.ec.europa.eu/eic-funding-opportunities/eic-accelerator_en",
        source="eic.ec.europa.eu: 2026 cut-offs 7 Jan, 4 Mar, 6 May, 8 Jul, 2 Sep, 4 Nov (17:00 Brussels); short proposals continuous, batched first Tuesday of each month",
        fit="Only realistic for Causality Graphs if it becomes a product company (e.g. a causal-evidence platform for clinical/pharma teams). Too early for a consultancy.",
        requirements="Single start-up/SME or small mid-cap (or a person intending to found one). A passed short application is needed before the full one.",
        action="Not for this cut-off. If a product path is chosen, submit a short application in 2027.",
    ),
    dict(
        id="horizon-hlth-2027-01-02",
        name="Horizon Europe Cluster Health 2027 (HLTH-2027-01 / -02)",
        funder="European Commission — Horizon Europe",
        kind="grant", scope="EU", lines="causality",
        amount_min=None, amount_max=None,
        amount_text="Consortium projects; budget per topic not yet confirmed",
        opens="2026-10-29", deadline="2027-02-17", deadline_kind="fixed",
        verification="unverified",
        url="https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/opportunities/calls-for-proposals",
        source="Euresearch e-alert citing draft Work Programme v3 (24 Jun 2026): opens 29 Oct 2026, single-stage and stage 1 deadline 17 Feb 2027 (moved earlier from April)",
        fit="Causality Graphs as the causal-analytics / methods partner (SME) in a clinical or health-data consortium.",
        requirements="Multi-country consortium; SMEs join as partners. Topics to be confirmed when the call opens on the Funding & Tenders portal.",
        action="Once the topics are published on 29 Oct, screen them for causal-inference / real-world-evidence angles and contact coordinators by December.",
    ),
    dict(
        id="enisa-prestamo-participativo",
        name="ENISA participatory loans for startups and SMEs",
        funder="ENISA (Ministerio de Industria)",
        kind="loan", scope="Spain", lines="causality,branchout",
        amount_min=25_000, amount_max=1_500_000,
        amount_text="Participatory loan EUR 25,000 – 1,500,000; no personal guarantees",
        opens=None, deadline=None, deadline_kind="rolling",
        verification="verified",
        url="https://www.enisa.es/servicios/financiacion/startups-y-pymes/",
        source="enisa.es startups-y-pymes page: EUR 25k–1.5M; own funds must at least equal the loan; amounts reviewed yearly",
        fit="Working capital for Causality Graphs (or a BranchOut startup) once it is an SL with matching own funds.",
        requirements="Spanish SME with its own legal personality; own funds at least equal to the loan amount; innovative business model.",
        action="Keep in reserve: check the own-funds ratio before applying. No deadline pressure.",
    ),
    dict(
        id="fecyt-fcc-2026",
        name="FECYT Ayudas para el Fomento de la Cultura Científica 2026",
        funder="FECYT (Ministerio de Ciencia)",
        kind="grant", scope="Spain", lines="cloudy,semf,delfina",
        amount_min=None, amount_max=100_000,
        amount_text="Up to EUR 100,000 per application",
        opens="2026-07-01", deadline="2026-09-16", deadline_kind="fixed",
        verification="verified",
        url="https://www.boe.es/boe/dias/2026/06/27/pdfs/BOE-B-2026-22034.pdf",
        source="BOE-B-2026-22034 (27 Jun 2026): applications 1 Jul – 16 Sep 2026 13:00",
        review_on="2027-04-01",
        fit="The most natural fit for Cloudy (science communication for families, citizen science) and for SEMF outreach (art–science–technology category).",
        requirements="Eligible entity with legal personality (check the bases for non-profit associations). Activities run between Jul 2027 and Jun 2029.",
        action="Closed for 2026. Prepare the 2027 application from April 2027; the call usually opens around July.",
        bdns=None,
        winners="2026 awards not published yet (the 2024 edition was resolved on 27 May 2025, about 8 months after its deadline). FECYT 2024 (BDNS 776848): 193 awards, EUR 4.5M total, median EUR 20,500 (range 715–82,200). Winners by type: universities 100, CSIC 27, foundations 25 (median EUR 20,000), associations 5 (median EUR 12,000). Comparable small winners: Asociación Valentia Scientia 27,000; Fundación Albireo Cultura Científica 25,000; Asociación Asíntota 12,000 + 10,000; Fundación madri+d 35,000. Lesson: the 100k ceiling is rare — a realistic request for an association or foundation is EUR 15–25k. Source: BDNS concessions API + FECYT definitive resolution 2024; checked 2026-10-09.",
    ),
    dict(
        id="cdti-neotec-2026",
        name="CDTI NEOTEC 2026",
        funder="CDTI (Ministerio de Ciencia)",
        kind="grant", scope="Spain", lines="causality,branchout",
        amount_min=None, amount_max=None,
        amount_text="Total budget EUR 20.38M (EUR 5M reserved for women-led projects); per-project cap not confirmed",
        opens="2026-04-14", deadline="2026-05-14", deadline_kind="fixed",
        verification="unverified",
        url="https://www.ciencia.gob.es/Convocatorias/2026/NEOTEC2026.html",
        source="Search snippet from ciencia.gob.es (not fetched): applications 14 Apr – 14 May 2026",
        fit="Technology-based small companies building on research results — Causality Graphs if it develops its own technology.",
        requirements="2026 rules (per the Moncloa/CDTI announcement): small technology-based company under 3 years old, share capital at least EUR 20,000, no distributed profits; grant up to 70% of budget (85% when hiring a PhD), cap EUR 250k (325k with a PhD).",
        action="Closed. Watch for NEOTEC 2027 around April 2027.",
        bdns="896999",
        winners="2026 awards not yet in BDNS (the 2025 edition was resolved on 22 Dec 2025, so expect December 2026). NEOTEC 2025 (BDNS 829858): 128 companies, EUR 39.35M, median EUR 325,000 — most winners reached the cap that applies when a PhD holder is hired (cap 250k, 325k with a PhD). Lesson: plan a PhD hire into the project. Galicia (GAIN) runs a 'Recuperación Excelencia NEOTEC' call for companies above the threshold but unfunded; Madrid has no equivalent found. Source: BDNS concessions API; checked 2026-10-09.",
    ),
    dict(
        id="healthstart-madrimasd",
        name="healthstart madri+d (health tech startup programme)",
        funder="Fundación para el Conocimiento madri+d (Comunidad de Madrid)",
        kind="prize", scope="Madrid", lines="causality",
        amount_min=None, amount_max=None,
        amount_text="Startup programme; prize and support not confirmed",
        opens=None, deadline=None, deadline_kind="expected",
        verification="unverified",
        url="https://www.madrimasd.org/",
        source="Press coverage (emprendedores.es): 2026 edition registration closed 25 May",
        fit="Madrid-based health technology startups — directly matches Causality Graphs.",
        requirements="To be confirmed from the next edition's bases.",
        action="Watch for the next edition (spring 2027).",
    ),
    dict(
        id="caixaresearch-health-next",
        name="CaixaResearch Health Research Call (next edition)",
        funder="Fundación \"la Caixa\"",
        kind="grant", scope="Spain", lines="causality",
        amount_min=None, amount_max=None,
        amount_text="Not confirmed for the next edition",
        opens=None, deadline=None, deadline_kind="expected",
        verification="unverified",
        url="https://caixaresearch.org/en/caixaresearch-health-call",
        source="Previous edition opened 18 Sep 2025 and closed 19 Nov 2025 (university research offices); official page returned 403 to the fetcher",
        fit="Translational and clinical research with social impact; Causality Graphs would join as the methods partner of an eligible research centre or hospital.",
        requirements="Applicants are research institutions in Spain/Portugal; a company cannot lead.",
        action="Check whether the 2027 edition opened in September 2026; if so, contact clinical collaborators now.",
    ),
    dict(
        id="erasmus-ka210-2027",
        name="Erasmus+ Small-scale Partnerships (KA210) — 2027 round",
        funder="European Commission — Erasmus+ (SEPIE in Spain)",
        kind="grant", scope="EU", lines="cloudy,delfina",
        amount_min=None, amount_max=None,
        amount_text="Lump-sum grants; 2027 amounts not confirmed",
        opens=None, deadline=None, deadline_kind="expected",
        verification="unverified",
        url="https://erasmus-plus.ec.europa.eu/",
        source="2026 rounds closed 5 Mar and 1 Oct 2026 (national agency pages); 2027 dates to be published in the Programme Guide",
        fit="Bilingual family-reading and health-education materials (Cloudy) with a partner organisation in another EU country — designed for small and first-time organisations.",
        requirements="A registered organisation (OID) and at least one partner from another programme country.",
        action="Find one EU partner organisation (school, library or NGO) before the expected March 2027 deadline.",
    ),
    dict(
        id="carasso-componer-saberes",
        name="Fundación Daniel y Nina Carasso — Componer saberes (art + science + community)",
        funder="Fundación Daniel y Nina Carasso",
        kind="grant", scope="Spain", lines="semf,cloudy,delfina",
        amount_min=None, amount_max=None,
        amount_text="Not confirmed",
        opens=None, deadline=None, deadline_kind="expected",
        verification="unverified",
        url="https://www.fundacioncarasso.org/",
        source="Biennial call; the last edition closed 27 Apr 2025 (university summaries); next edition not announced",
        fit="Projects linking art, science and community — matches SEMF's art–science confluence and Cloudy's family audience.",
        requirements="To be confirmed from the next edition's bases.",
        action="Watch for the 2027 edition.",
    ),
    # --- BranchOut: tech startup hub building a coworking + coliving space ---
    dict(
        id="plan-vivienda-2026-coliving",
        name="Plan Estatal de Vivienda 2026–2030 — cohousing / coliving / alojamientos temporales line",
        funder="Ministerio de Vivienda y Agenda Urbana (calls run by each comunidad autónoma)",
        kind="grant", scope="Spain", lines="branchout,delfina",
        amount_min=None, amount_max=None,
        amount_text="Set by each region's call; plan total EUR 7,000M, co-financed 60% State / 40% region",
        opens=None, deadline=None, deadline_kind="expected",
        verification="verified",
        url="https://www.boe.es/eli/es/rd/2026/04/22/326",
        source="Real Decreto 326/2026 (BOE 23 Apr 2026), art. 3.1: line for cooperative housing in cesión de uso and residential solutions built around shared living; regional calls via bilateral agreements",
        fit="The only national line that explicitly funds coliving. It suits the residential half of BranchOut, not the coworking half.",
        requirements="Beneficiaries include cooperatives and non-profit or limited-profit entities. Homes must be rented for at least 20 years under permanent protection, with capped rents from year one, so this is incompatible with a market-rate coliving. Press reports say the Comunidad de Madrid will not sign up to the plan, so a Madrid site may have no regional call.",
        action="Decide the legal model (cooperative / limited-profit entity vs. company) and the region before pursuing this; if outside Madrid, check that region's 2026–2027 call.",
    ),
    dict(
        id="horizon-neb-2026",
        name="Horizon Europe New European Bauhaus 2026 (HORIZON-NEB-2026-01)",
        funder="European Commission — Horizon Europe NEB Facility",
        kind="grant", scope="EU", lines="branchout",
        amount_min=None, amount_max=None,
        amount_text="Consortium projects; neighbourhood-design topics around EUR 5M per project (secondary source)",
        opens="2026-05-05", deadline="2026-12-01", deadline_kind="fixed",
        verification="unverified",
        url="https://new-european-bauhaus.europa.eu/",
        source="UKRI/IUK and Zabala summaries of the NEB work programme: opens 5 May 2026, deadline 1 Dec 2026 17:00; topics include spatial design of neighbourhoods and inhabitants' health and well-being",
        fit="BranchOut's coworking + coliving building could be a pilot or demonstration site in a consortium on shared, sustainable neighbourhood living. The 2027 call (deadline 1 Dec 2027) adds an intergenerational-neighbourhoods topic.",
        requirements="Multi-country research and innovation consortium; BranchOut would join as an SME or pilot-site partner, not lead.",
        action="Too tight to lead for 1 Dec. Contact Spanish universities or architecture groups already bidding, offering BranchOut as a pilot site; aim seriously at the 2027 call.",
    ),
    dict(
        id="startup-europe-2027",
        name="Horizon Europe Startup Europe (HORIZON-EIE-2027-01-CONNECT-01)",
        funder="European Commission — European Innovation Ecosystems",
        kind="grant", scope="EU", lines="branchout",
        amount_min=None, amount_max=None,
        amount_text="Around EUR 2M per project, up to 9 projects (secondary source)",
        opens="2027-06-01", deadline="2027-09-15", deadline_kind="fixed",
        verification="unverified",
        url="https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/opportunities/calls-for-proposals",
        source="Western Balkans Info Hub / grant aggregators citing the EIE 2026–2027 work programme: call 1 Jun – 15 Sep 2027",
        fit="Funds ecosystem builders that connect startup communities across Europe — the closest EU match for a startup hub itself.",
        requirements="Consortium; at least 50% of beneficiaries established in 'moderate' or 'emerging' innovator countries or regions (check how Spain/Madrid are classified).",
        action="Start building a cross-border hub partnership (2–3 hubs in moderate/emerging innovator regions) in early 2027.",
    ),
    dict(
        id="neb-connect-cocreate-2027",
        name="Connect NEB / Co-create NEB open call 2027",
        funder="EU — New European Bauhaus (with EIT Community)",
        kind="grant", scope="EU", lines="branchout",
        amount_min=None, amount_max=None,
        amount_text="Not stated on the official page",
        opens="2026-07-22", deadline="2026-09-30", deadline_kind="fixed",
        verification="verified",
        url="https://new-european-bauhaus.europa.eu/calls-proposals/co-create-neb_en",
        source="new-european-bauhaus.europa.eu: deadline 30 Sep 2026 17:00 CEST; status closed",
        fit="Community-rooted organisations and local partnerships with municipalities improving neighbourhoods and shared spaces — the community side of a coworking/coliving hub.",
        requirements="Co-create NEB needs a local partnership including the municipality.",
        action="Closed. Watch for the next edition (published around July) and line up a municipal partner in advance.",
        bdns=None,
        winners="Not researched yet: the 2027-call results come after the 30 Sep 2026 deadline; the previous edition's winners list still needs to be looked up on new-european-bauhaus.europa.eu. Pending for the next run.",
    ),
    dict(
        id="madrid-cheque-innovacion",
        name="Comunidad de Madrid — Cheque Innovación",
        funder="Comunidad de Madrid",
        kind="grant", scope="Madrid", lines="branchout",
        amount_min=None, amount_max=None,
        amount_text="EUR 12,000 (technology watch) to 60,000 (prototype validation) per press release — not shown on the official page",
        opens=None, deadline=None, deadline_kind="rolling",
        verification="verified",
        url="https://www.comunidad.madrid/inversion/innova",
        source="comunidad.madrid/inversion/innova: 'Convocatoria siempre abierta', direct award throughout 2026",
        fit="Small innovation projects for SMEs resident in the hub — useful as a service BranchOut points its startups to, rather than funding for the space.",
        requirements="Madrid SMEs; aimed at traditional-sector SMEs becoming innovative (tech startups may not fit).",
        action="Add to the hub's member-support toolkit; check eligibility per startup.",
    ),
    dict(
        id="madrid-sello-excelencia",
        name="Comunidad de Madrid — Sello de Excelencia (EIC Accelerator)",
        funder="Comunidad de Madrid",
        kind="grant", scope="Madrid", lines="branchout,causality",
        amount_min=None, amount_max=None,
        amount_text="Up to 70% of eligible costs; 2026 credit EUR 5.5M (BOCM 21 Jul 2026, per summaries) — confirm in the order",
        opens=None, deadline=None, deadline_kind="rolling",
        verification="verified",
        url="https://www.comunidad.madrid/innova/sello-excelencia",
        source="comunidad.madrid: 'Convocatoria siempre abierta'; 2026 credit by ORDEN 2820/2026 of 10 Jul (BOCM 21 Jul 2026)",
        fit="Pays Madrid companies whose EIC Accelerator proposal got the Seal of Excellence but no EU money — a safety net for any hub startup (or Causality Graphs) applying to EIC.",
        requirements="EIC Accelerator Seal of Excellence; workplace and project in the Comunidad de Madrid; projects up to 24 months.",
        action="Mention to hub startups applying to EIC; no action until a Seal is obtained.",
    ),
    dict(
        id="leader-rural-coworking",
        name="LEADER rural development — coworking / coliving in rural areas",
        funder="EU EAFRD via regional governments and Grupos de Acción Local",
        kind="grant", scope="Spain", lines="branchout",
        amount_min=None, amount_max=None,
        amount_text="Varies by local action group; e.g. Basque LEADER 2026 up to 65% for companies, max EUR 100,000",
        opens=None, deadline=None, deadline_kind="expected",
        verification="unverified",
        url="https://www.euskadi.eus/ayuda_subvencion/2026/leader-habilitacion-de-espacios/web01-a2pac/es/",
        source="euskadi.eus LEADER 2026 (habilitación de espacios) and search summaries; other regions (Extremadura coworking aid for municipalities under 20,000, Navarra Pyrenees housing+employment) only fund local entities",
        fit="If BranchOut's coliving/coworking is in a rural or depopulated area, LEADER and demographic-challenge funds are the strongest public money for the building itself.",
        requirements="Project located in the territory of a Grupo de Acción Local; private companies up to ~65%, public entities up to 100% (varies by region). Several regional coworking lines only accept municipalities — partner with the town hall.",
        action="Only relevant if the site is rural: once the location is known, contact that area's Grupo de Acción Local.",
        archived=True,
        archive_reason="Phase 1 location is Madrid West (Las Rozas / Majadahonda / Pozuelo), which is not LEADER territory; keep for the expansion phase",
        review_on="2027-04-01",
    ),
    # --- Madrid West (Las Rozas, Majadahonda, Pozuelo): space and local funding ---
    dict(
        id="lasrozas-cesion-espacios-cultura",
        name="Las Rozas — free use of rooms in municipal cultural centres for cultural associations",
        funder="Ayuntamiento de Las Rozas (Concejalía de Educación y Cultura)",
        kind="grant", scope="Madrid West", lines="semf,cloudy,delfina",
        amount_min=None, amount_max=None,
        amount_text="In-kind: free rooms (no fee) in Centro Cultural Entremontes, Pérez de la Riva and Las Matas; temporary or permanent use, max 4 years",
        opens=None, deadline=None, deadline_kind="expected",
        verification="verified",
        url="https://transparencia.lasrozas.es/wp-content/uploads/2024/08/20240607_Otros_Bases-Demaniales-permanente-temporal-2024-2025.pdf",
        source="Bases for course 2024–2025 (signed 7 Jun 2024): cultural associations on the Registro Municipal de Asociaciones with their registered office in Las Rozas; no fee (art. 92.5 LPAP). Calls are yearly per school course; 2025–26 also existed",
        fit="Free space for SEMF workshops and seminars and Cloudy family reading sessions — the cheapest way to get a physical base in Madrid West while the hub is built.",
        requirements="Must be a cultural ASSOCIATION legally constituted, registered in Las Rozas' municipal register, with its registered office and activities in Las Rozas. A foundation may not qualify — check; an association (e.g. SEMF, or a 'Friends of Delfina León' association) does.",
        action="Register SEMF or a cultural association in the Las Rozas Registro Municipal de Asociaciones now, then watch for the 2027–28 course call (bases usually signed in June).",
    ),
    dict(
        id="pozuelo-subv-cultura-2026",
        name="Pozuelo de Alarcón — subsidies for non-profit cultural entities 2026",
        funder="Ayuntamiento de Pozuelo de Alarcón",
        kind="grant", scope="Madrid West", lines="cloudy,semf,delfina",
        amount_min=None, amount_max=None,
        amount_text="EUR 72,500 total budget (competitive); per-entity cap in the full bases",
        opens="2026-03-31", deadline="2026-04-14", deadline_kind="fixed",
        verification="verified",
        review_on="2027-02-01",
        url="https://www.infosubvenciones.es/bdnstrans/GE/es/convocatoria/894351",
        source="BOCM 30 Mar 2026 (BDNS 894351): 15 calendar days from publication; fundable: exhibitions, concerts, publications, etc.",
        fit="Explicitly funds publications and cultural events — Cloudy's books and SEMF events, if the entity is based in Pozuelo.",
        requirements="Non-profit cultural entity registered in Pozuelo's Registro Municipal de Asociaciones Vecinales, with main activity and registered office in Pozuelo.",
        action="Closed (window was only 15 days). Decide which municipality hosts the registered office; if Pozuelo, register before February 2027 to catch the 2027 call.",
        bdns="894351",
        winners="Winners not published in BDNS: none of Pozuelo's culture calls (2016–2026) has awards uploaded (checked 2026-10-09). Look for the Junta de Gobierno Local award decision on the Pozuelo sede / transparency portal. Call history: yearly since 2016, but published at irregular times (2026: 20 Mar; 2025: 10 Jun; 2024: 6 Nov) with only a 15-day window — monitor BDNS for Pozuelo.",
    ),
    dict(
        id="majadahonda-emprende-2026",
        name="Premios Majadahonda Emprende 2026",
        funder="Ayuntamiento de Majadahonda (Desarrollo, Empleo y Comercio)",
        kind="prize", scope="Madrid West", lines="branchout",
        amount_min=700, amount_max=2_800,
        amount_text="EUR 12,000 total; Best StartUp EUR 2,500 / 1,500; Best Consolidated Company up to EUR 2,800",
        opens="2026-05-30", deadline="2026-09-21", deadline_kind="fixed",
        verification="verified",
        url="https://www.infosubvenciones.es/bdnstrans/GE/es/convocatoria/906922",
        source="BOCM 29 May 2026 (BDNS 906922): applications until 21 Sep 2026",
        fit="Small, but local visibility for BranchOut startups based in Majadahonda.",
        requirements="Entrepreneurs and companies of Majadahonda, up to date with AEAT, Social Security and the council.",
        action="Closed. Enter BranchOut (or a resident startup) in the 2027 edition if the hub is in Majadahonda.",
        bdns="906922",
        winners="2026 winners not published yet (the 2025 edition was awarded on 12 Nov 2025). 2025 edition (BDNS 848973): 7 prizes, EUR 12,000. Company winners: The Natural Healthy and Honest Food Company SL 2,800; Gambooza Market SL 2,500; Majadahonda Te Va SL 2,000; Vulnscan Tech SL 1,500; Ramos y Werner SL 1,500; plus 2 individuals (700, 1,000; names masked in BDNS). Lesson: open to any local business; only one clearly tech winner. Source: BDNS concessions API; checked 2026-10-09.",
    ),
    dict(
        id="lasrozas-innova-foro",
        name="Las Rozas Innova — Foro de Inversión + Hub access (municipal innovation company)",
        funder="Las Rozas Innova (Ayuntamiento de Las Rozas)",
        kind="equity", scope="Madrid West", lines="branchout",
        amount_min=None, amount_max=None,
        amount_text="Pitch to business angels and VCs; finalists get 6 months in Hub Las Rozas Innova with mentoring",
        opens=None, deadline=None, deadline_kind="expected",
        verification="unverified",
        url="https://www.lasrozas.es/",
        source="Press (El Referente, emprendedores.es): 5th forum registration to 14 Mar 2026; 6th edition with BStartup/BIGBAN/Senda closed 13 Sep 2026. Las Rozas Innova also ran 'Cheques Coworking' (2021, BDNS 547225) incl. a liquidity line for coworking operators",
        fit="The local tech ecosystem BranchOut wants to join: investors for its startups, a partner (or competitor) hub, and a past precedent of municipal money for coworking operators in Las Rozas.",
        requirements="Seed / early-stage tech startups; coworking cheques required operating a coworking space in Las Rozas.",
        action="Meet Las Rozas Innova: propose BranchOut as a coworking/coliving partner and ask whether coworking cheques will return. Apply to the next forum (spring edition, ~March).",
    ),
    dict(
        id="pozuelo-innpar-vivero",
        name="Pozuelo de Alarcón — INNPAR Vivero (municipal incubator) and business-development sponsorships",
        funder="Ayuntamiento de Pozuelo de Alarcón",
        kind="grant", scope="Madrid West", lines="branchout",
        amount_min=None, amount_max=None,
        amount_text="Incubator space and services; sponsorship/support for business-development projects (amounts not published)",
        opens=None, deadline=None, deadline_kind="rolling",
        verification="unverified",
        url="https://sede.pozuelodealarcon.es/",
        source="Pozuelo e-office procedures 'Adhesión al Convenio INNPAR VIVERO' and 'Patrocinios y Colaboraciones en Proyectos de Desarrollo Empresarial y Comercio' (via papelea.com summaries)",
        fit="Interim space for BranchOut startups and possible municipal co-sponsorship of hub events; La Finca Ventures Camp (Pozuelo) is a nearby incubator partner.",
        requirements="To be checked on the Pozuelo sede: terms of the vivero agreement and of the sponsorship procedure.",
        action="Request the INNPAR Vivero conditions and ask the council about sponsoring BranchOut community events.",
    ),
    dict(
        id="lasrozas-cultura-asociaciones-2026",
        name="Las Rozas — subsidies for cultural programmes by local associations 2026",
        funder="Ayuntamiento de Las Rozas",
        kind="grant", scope="Madrid West", lines="semf,cloudy",
        amount_min=None, amount_max=None,
        amount_text="EUR 20,000 total; in practice about EUR 1,700 per association",
        opens="2026-05-19", deadline="2026-06-08", deadline_kind="fixed",
        verification="verified",
        url="https://www.infosubvenciones.es/bdnstrans/GE/es/convocatoria/904440",
        source="BDNS 904440: applications 19 May – 8 Jun 2026; beneficiaries: non-profit legal entities of the municipality",
        fit="Small top-up for SEMF or Cloudy activities once an association is registered in Las Rozas.",
        requirements="Association registered in Las Rozas with its registered office there.",
        action="Closed. Low value; only worth it once a Las Rozas-registered association exists (next call around May 2027).",
        bdns="904440",
        winners="2026 (BDNS 904440): 11 awards on 1 Jul 2026, EUR 18,584 total, almost flat EUR 1,618–1,700 each — the budget is split evenly. Winners: local theatre companies, choir, photography, magic and crafts associations, a seniors' association, a brotherhood. Lesson: an even split across local groups — symbolic money, but it signals a relationship with the council's culture department. Source: BDNS concessions API; checked 2026-10-09.",
    ),
    dict(
        id="lasrozas-convenios-entidades",
        name="Las Rozas — direct collaboration agreements (convenios / subvenciones nominativas) with associations and foundations",
        funder="Ayuntamiento de Las Rozas",
        kind="grant", scope="Madrid West", lines="delfina,semf,cloudy,branchout",
        amount_min=None, amount_max=None,
        amount_text="Negotiated per agreement; 2026 examples from EUR 8,000 to EUR 60,000",
        opens=None, deadline=None, deadline_kind="rolling",
        verification="verified",
        url="https://www.infosubvenciones.es/bdnstrans/GE/es/convocatoria/930664",
        source="BDNS records of Las Rozas agreements published Sep–Oct 2026 (e.g. 930664, 933142, 931470)",
        fit="The biggest municipal money in Las Rozas is not competitive; it goes through annual agreements with entities the council works with. A foundation running science-culture, family and innovation programmes in Las Rozas fits the 'Cultura' agreements.",
        requirements="Needs an existing relationship and a project the council wants to co-own; the entity must be legally constituted (Fundación Delfina León once registered). Agreements are budgeted in the yearly municipal budget, so negotiate before budget drafting (autumn).",
        action="Present Fundación Delfina León's programme to the Concejalía de Cultura and to Las Rozas Innova before the 2027 budget is drafted; propose a pilot agreement for 2027.",
        bdns="930664",
        winners="2026 agreements (BDNS): Fundación Mundial de la Felicidad EUR 60,000 (culture); Asociación Cultural La Unión Las Matas EUR 10,000; Fundación Trébol EUR 8,000 (disability training); plus agreements with Cruz Roja, Cáritas, AECC and sports federations. Lesson: foundations with a cultural programme get the largest amounts. Source: BDNS; checked 2026-10-09.",
    ),
    dict(
        id="madrid-cesion-centros-educativos",
        name="Use of public school premises outside school hours (Comunidad de Madrid Decreto 11/2018)",
        funder="Councils of Madrid West (Majadahonda form verified) under Comunidad de Madrid Decreto 11/2018",
        kind="grant", scope="Madrid West", lines="cloudy,semf,delfina",
        amount_min=None, amount_max=None,
        amount_text="In-kind: classrooms and facilities of public infant/primary schools outside school hours",
        opens=None, deadline=None, deadline_kind="rolling",
        verification="verified",
        url="https://cultura.majadahonda.org/documents/36614/249061/solcitud+cesion_instalaciones.pdf/8dba47af-644a-b6c4-ade3-3f0e359bd7c4",
        source="Majadahonda request form (Decreto 11/2018, BOCM 12 Mar 2018): associations need CAM + municipal registration; 'colectivos y otras entidades' may apply with proof of activity",
        fit="Family science and reading sessions (Cloudy) and workshops (SEMF) in schools at weekends or evenings — near families. Unlike most cessions, it is also open to non-association entities.",
        requirements="Activity project (Annex I), civil-liability insurance or guarantee; associations: Comunidad de Madrid + municipal registry; other entities: proof of activity.",
        action="Use for Cloudy pilot sessions as soon as an entity and insurance exist; check the same procedure in Las Rozas and Pozuelo.",
    ),
]


def derive_status(row):
    """open | upcoming | rolling | closed, from the dates as of TODAY."""
    if row["deadline_kind"] == "rolling":
        return "rolling"
    deadline = row["deadline"] and dt.date.fromisoformat(row["deadline"])
    opens = row["opens"] and dt.date.fromisoformat(row["opens"])
    if deadline and deadline < TODAY:
        return "closed"
    if row["deadline_kind"] == "expected" or (opens and opens > TODAY):
        return "upcoming"
    return "open"


def archive_state(raw, row):
    """(archived, archive_reason, review_on) for a ROWS entry."""
    if raw.get("archived"):
        return 1, raw.get("archive_reason"), raw.get("review_on")
    if row["status"] == "closed":
        review = raw.get("review_on") or (
            dt.date.fromisoformat(row["deadline"]) + dt.timedelta(days=REVIEW_AFTER_CLOSE_DAYS)).isoformat()
        return 1, raw.get("archive_reason") or "Deadline passed; kept to prepare the next edition", review
    return 0, None, None


def migrate(conn):
    have = {r[1] for r in conn.execute("PRAGMA table_info(opportunities)")}
    for col, decl in MIGRATIONS.items():
        if col not in have:
            conn.execute(f"ALTER TABLE opportunities ADD COLUMN {col} {decl}")


def log_change(conn, oid, field, old, new, today):
    conn.execute(
        "INSERT INTO changes VALUES (?, ?, ?, ?, ?)",
        (oid, field, None if old is None else str(old), None if new is None else str(new), today),
    )


def archive_removed(conn, today):
    """Rows that left ROWS are archived, never deleted."""
    ids = {r["id"] for r in ROWS}
    review = (TODAY + dt.timedelta(days=REVIEW_AFTER_REMOVAL_DAYS)).isoformat()
    reason = "Removed from build.py ROWS; re-add it there to restore"
    for cur in conn.execute("SELECT id, archived FROM opportunities").fetchall():
        if cur["id"] in ids or cur["archived"]:
            continue
        for field, new in (("archived", 1), ("archive_reason", reason), ("review_on", review)):
            log_change(conn, cur["id"], field, None if field != "archived" else 0, new, today)
            conn.execute(f"UPDATE opportunities SET {field} = ? WHERE id = ?", (new, cur["id"]))
        print(f"archived (removed from ROWS): {cur['id']}")


def sync(conn):
    today = TODAY.isoformat()
    for raw in ROWS:
        row = {f: raw.get(f) for f in FIELDS}
        row["status"] = derive_status(row)
        row["archived"], row["archive_reason"], row["review_on"] = archive_state(raw, row)
        cur = conn.execute("SELECT * FROM opportunities WHERE id = ?", (row["id"],)).fetchone()
        if cur is None:
            conn.execute(
                f"INSERT OR IGNORE INTO opportunities ({', '.join(FIELDS)}, first_seen, last_checked) "
                f"VALUES ({', '.join('?' * (len(FIELDS) + 2))})",
                [row[f] for f in FIELDS] + [today, today],
            )
            continue
        for field in TRACKED:
            old, new = cur[field], row[field]
            if old != new:
                log_change(conn, row["id"], field, old, new, today)
                conn.execute(f"UPDATE opportunities SET {field} = ? WHERE id = ?", (new, row["id"]))
        conn.execute("UPDATE opportunities SET last_checked = ? WHERE id = ?", (today, row["id"]))
    archive_removed(conn, today)
    conn.commit()


def load(conn):
    opps = [dict(r) for r in conn.execute("SELECT * FROM opportunities ORDER BY deadline IS NULL, deadline")]
    changes = [dict(r) for r in conn.execute(
        "SELECT * FROM changes ORDER BY changed_on DESC, rowid DESC LIMIT 50")]
    return opps, changes


def alerts(opps, changes):
    """What the routine should notify about (see CLAUDE.md > Notifications)."""
    today = TODAY.isoformat()
    soon = []
    for o in opps:
        if o["verification"] == "verified" and o["status"] == "open" and o["deadline"] and not o["archived"]:
            days = (dt.date.fromisoformat(o["deadline"]) - TODAY).days
            if 0 <= days <= CLOSING_SOON_DAYS:
                soon.append({**_brief(o), "days_left": days})
    new_high = [_brief(o) for o in opps
                if o["first_seen"] == today and (o["amount_max"] or 0) >= HIGH_VALUE_EUR
                and not o["archived"]]
    return {
        "generated": today,
        "closing_soon": soon,
        "new_high_value": new_high,
        "new_today": [o["id"] for o in opps if o["first_seen"] == today],
        "changed_today": sorted({c["opportunity_id"] for c in changes if c["changed_on"] == today}),
        # Archived rows whose review date has come: recover (new edition / unarchive) or re-date.
        "review_due": [{**_brief(o), "archive_reason": o["archive_reason"], "review_on": o["review_on"]}
                       for o in opps if o["archived"] and o["review_on"] and o["review_on"] <= today],
    }


def _brief(o):
    return {k: o[k] for k in ("id", "name", "lines", "amount_text", "deadline", "url", "action")}


def render(opps, changes, lessons, plan):
    data = json.dumps(
        {"today": TODAY.isoformat(), "lines": LINES, "opportunities": opps,
         "changes": changes, "lessons": lessons, "plan": plan},
        ensure_ascii=False,
    ).replace("</", "<\\/")
    template = (HERE / "dashboard_template.html").read_text(encoding="utf-8")
    return (template
            .replace("/*__DATA__*/null", data)
            .replace("__GENERATED__", html.escape(TODAY.strftime("%d %b %Y"))))


def read_lessons():
    path = HERE / "LESSONS.md"
    if not path.exists():
        return []
    return [line[2:].strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.startswith("- ")][-10:]


def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    migrate(conn)
    sync(conn)
    opps, changes = load(conn)
    apps = [dict(r) for r in conn.execute("SELECT * FROM applications ORDER BY updated_on DESC")]
    ceremonies = [dict(r, decisions=json.loads(r["decisions"]), commitments=json.loads(r["commitments"]))
                  for r in conn.execute("SELECT * FROM ceremonies ORDER BY held_on DESC, id DESC")]
    conn.close()

    planner = load_planner()
    plan = planner.make_plan(opps, planner.load_strategy(), apps, TODAY, ceremonies)
    out = HERE / "graphify-out"
    out.mkdir(exist_ok=True)
    (out / "graph.json").write_text(json.dumps(planner.graph_export(plan), ensure_ascii=False, indent=1) + "\n",
                                    encoding="utf-8")
    plan["has_3d"] = (out / "graph3d.html").exists()
    DASHBOARD.write_text(render(opps, changes, read_lessons(), plan), encoding="utf-8")
    report = alerts(opps, changes)
    ALERTS.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    counts = {}
    for o in opps:
        key = "archived" if o["archived"] else o["status"]
        counts[key] = counts.get(key, 0) + 1
    print(f"{len(opps)} opportunities {counts}; {len(report['review_due'])} archive reviews due; "
          f"{len(report['new_today'])} new, {len(report['changed_today'])} changed today; "
          f"{len(report['closing_soon'])} closing within {CLOSING_SOON_DAYS} days")


if __name__ == "__main__":
    main()
